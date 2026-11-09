import base64
import io
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_app import PASSWORD, item, settings

from fatties.analysis import FakeAnalyzer
from fatties.app import create_app, set_password
from fatties.photos import LocalPhotos, PhotoError, R2Photos, prepare_photo
from fatties.store import Store
from fatties.summary import period_summary


class MemoryPhotos:
    def __init__(self):
        self.images = {}
        self.fail_put = False
        self.fail_delete = False

    def put(self, key, photo, thumbnail):
        self.images[key] = (photo, thumbnail)
        if self.fail_put:
            raise PhotoError("interrupted upload")

    def get(self, key, *, thumbnail=False):
        return self.images[key][int(thumbnail)]

    def delete(self, key):
        if self.fail_delete:
            raise PhotoError("offline")
        self.images.pop(key, None)


def png():
    data = io.BytesIO()
    Image.new("RGB", (800, 600), "orange").save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def pair(tmp_path):
    photos = MemoryPhotos()
    cfg = settings(tmp_path)
    store = Store(cfg.db_path)
    store.ensure_people(cfg.people)
    for name in cfg.people:
        set_password(store, name, PASSWORD)
    app = create_app(cfg, analyzer=FakeAnalyzer(), store=store, photo_storage=photos)
    clients = [TestClient(app, base_url="https://fatties.test") for _ in cfg.people]
    for client, name in zip(clients, cfg.people, strict=True):
        assert client.post("/api/login", json={"name": name, "password": PASSWORD}).status_code == 200
    return clients[0], clients[1], store, photos, app


def log(client, pid, **extra):
    response = client.post("/api/meals", json={"person_id": pid, "title": "Rice", "items": [item()], **extra})
    assert response.status_code == 201, response.text
    return response.json()


def token(client):
    response = client.post("/api/widget/credentials")
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_photo_is_private_compressed_deleted_and_save_is_idempotent(pair):
    polar, partner, store, photos, app = pair
    payload = {"photo_base64": base64.b64encode(png()).decode(), "request_id": str(uuid.uuid4())}
    meal = log(polar, 1, **payload)
    assert meal["has_photo"] and "photo_key" not in meal
    assert log(polar, 1, **payload)["id"] == meal["id"]
    conflict = polar.post("/api/meals", json={"person_id": 1, "title": "Changed rice", "items": [item()], **payload})
    assert conflict.status_code == 409
    assert store.meal(meal["id"])["title"] == "Rice"
    assert len(photos.images) == 1
    assert len(store.meals_on(1, date.fromisoformat(meal["eaten_on"]))) == 1
    for client in [polar, partner]:
        image = client.get(f"/api/meals/{meal['id']}/photo")
        assert image.status_code == 200 and image.headers["cache-control"] == "no-store"
        assert Image.open(io.BytesIO(image.content)).format == "JPEG"
        thumb = client.get(f"/api/meals/{meal['id']}/photo?thumbnail=true")
        assert max(Image.open(io.BytesIO(thumb.content)).size) == 512
    assert TestClient(app).get(f"/api/meals/{meal['id']}/photo").status_code == 401
    assert partner.delete(f"/api/meals/{meal['id']}").status_code == 403
    assert polar.delete(f"/api/meals/{meal['id']}").status_code == 204
    assert not photos.images and not store.photo_cleanup_keys()
    assert partner.get(f"/api/meals/{meal['id']}").status_code == 404


def test_failed_upload_and_failed_cleanup_are_retried(pair):
    polar, _, store, photos, _ = pair
    photos.fail_put = True
    response = polar.post("/api/meals", json={"person_id": 1, "title": "x", "items": [item()], "photo_base64": base64.b64encode(png()).decode()})
    assert response.status_code == 503
    assert store.latest_meal(1) is None and not photos.images
    photos.fail_put = False
    meal = log(polar, 1, photo_base64=base64.b64encode(png()).decode())
    photos.fail_delete = True
    assert polar.delete(f"/api/meals/{meal['id']}").status_code == 204
    assert store.latest_meal(1) is None and store.photo_cleanup_keys()
    photos.fail_delete = False
    log(polar, 1)
    assert not photos.images and not store.photo_cleanup_keys()


def test_concurrent_retries_create_one_meal_and_one_photo(pair):
    polar, _, store, photos, app = pair
    payload = {"person_id": 1, "title": "Rice", "items": [item()], "photo_base64": base64.b64encode(png()).decode(), "request_id": str(uuid.uuid4())}
    clients = [TestClient(app, base_url="https://fatties.test", cookies=polar.cookies) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda client: client.post("/api/meals", json=payload), clients))
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert store.db.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 1
    assert len(photos.images) == 1


def test_database_failure_compensates_uploaded_objects(pair, monkeypatch):
    polar, _, store, photos, app = pair

    def fail(**kwargs):
        raise sqlite3.OperationalError("simulated write failure")

    monkeypatch.setattr(store, "add_meal", fail)
    client = TestClient(app, base_url="https://fatties.test", cookies=polar.cookies, raise_server_exceptions=False)
    response = client.post("/api/meals", json={"person_id": 1, "title": "Rice", "items": [item()], "photo_base64": base64.b64encode(png()).decode()})
    assert response.status_code == 500
    assert not photos.images and not store.photo_cleanup_keys()


def test_rejected_photos_do_not_create_logs_or_objects(pair):
    polar, _, store, photos, _ = pair
    for data in ["not base64", base64.b64encode(b"not an image").decode()]:
        response = polar.post("/api/meals", json={"person_id": 1, "title": "x", "items": [item()], "photo_base64": data})
        assert response.status_code == 400
    assert not photos.images and store.latest_meal(1) is None


def test_widget_scope_ordering_thumbnail_race_and_no_logs(pair):
    polar, partner, store, _, app = pair
    polar_token = token(polar)
    assert polar_token not in str([tuple(r) for r in store.db.execute("SELECT * FROM widget_credentials")])
    reader = TestClient(app, base_url="https://fatties.test", headers={"Authorization": f"Bearer {polar_token}"})
    assert reader.get("/api/widget/partner/latest").json()["meal"] is None
    own = log(polar, 1, title="Own food")
    first = log(partner, 2, eaten_at="2026-11-09T12:00", photo_base64=base64.b64encode(png()).decode())
    latest = log(partner, 2, title="Backdated", eaten_at="2026-01-01T12:00", photo_base64=base64.b64encode(png()).decode())
    # Force equal creation instants; ID breaks ties consistently.
    with store.db:
        store.db.execute("UPDATE meals SET created_at = '2026-11-09T12:00:00+08:00'")
    body = reader.get("/api/widget/partner/latest?person_id=1").json()
    assert body["partner"]["id"] == 2 and body["meal"]["id"] == latest["id"]
    assert body["meal"]["url"] == f"https://fatties.test/?meal={latest['id']}"
    for forbidden in [own["id"], first["id"]]:
        assert reader.get(f"/api/widget/partner/latest/thumbnail?meal_id={forbidden}").status_code == 409
    assert reader.get(body["meal"]["thumbnail_url"]).status_code == 200
    assert reader.get("/api/people").status_code == 401
    assert reader.post("/api/meals", json={"person_id": 1, "title": "x", "items": [item()]}).status_code == 401
    assert polar.get("/api/widget/partner/latest").status_code == 401  # Cookie alone cannot read the widget feed.
    assert partner.delete(f"/api/meals/{latest['id']}").status_code == 204
    assert reader.get(body["meal"]["thumbnail_url"]).status_code == 409
    assert reader.get("/api/widget/partner/latest").json()["meal"]["id"] == first["id"]
    partner.delete(f"/api/meals/{first['id']}")
    assert reader.get("/api/widget/partner/latest").json()["meal"] is None


@pytest.mark.parametrize("reason", ["disconnect", "logout", "password", "expiry"])
def test_widget_revocation_is_per_person(pair, reason):
    polar, partner, store, _, app = pair
    readers = [TestClient(app, headers={"Authorization": f"Bearer {token(client)}"}) for client in [polar, partner]]
    assert all(c.get("/api/widget/partner/latest").status_code == 200 for c in readers)
    if reason == "disconnect":
        polar.delete("/api/widget/credentials")
    elif reason == "logout":
        polar.post("/api/logout")
    elif reason == "password":
        set_password(store, "Polar", "new-password")
    else:
        with store.db:
            store.db.execute("UPDATE widget_credentials SET expires_at = 0 WHERE person_id = 1")
    assert readers[0].get("/api/widget/partner/latest").status_code == 401
    assert readers[1].get("/api/widget/partner/latest").status_code == 200


def test_month_leap_year_historical_targets_missing_days_and_future_logs(tmp_path):
    store = Store(tmp_path / "history.db")
    store.ensure_people(["A"])
    targets = {"kcal": 2000, "carbs_g": 200, "protein_g": 100, "fat_g": 60, "sugar_g": 40}
    store.save_plan(1, date(2024, 2, 2), targets, {})
    store.save_plan(1, date(2024, 2, 15), {**targets, "kcal": 1800}, {})
    for day, kcal in [(1, 3000), (2, 2000), (15, 1600), (29, 1900)]:
        store.add_meal(person_id=1, eaten_at=datetime(2024, 2, day, 12), meal_type=None, title="x", note=None, items=[item(kcal=kcal)], model=None)
    month = period_summary(store, date(2024, 2, 20), date(2024, 2, 20), "month")
    a = month["people"][0]
    assert month["start"] == "2024-02-01" and month["end"] == "2024-02-29"
    assert len(a["days"]) == 29 and a["all_logged_days"] == 3
    assert a["elapsed_days"] == 20 and a["logged_days"] == 2
    assert a["total"]["kcal"] == 3600
    assert a["target_to_date"]["kcal"] == 13 * 2000 + 6 * 1800
    assert a["unlogged_past_days"] == 16
    assert a["average_target"]["kcal"] == 1900
    assert a["all_total"]["kcal"] == 6600
    year = period_summary(store, date(2024, 2, 20), date(2024, 2, 20), "year")
    assert len(year["people"][0]["days"]) == 366
    assert year["people"][0]["buckets"][1]["consumed"]["kcal"] == 2200
    assert year["people"][0]["previous_period"]["start"] == "2023-01-01"
    future = period_summary(store, date(2025, 1, 1), date(2024, 2, 20), "year")["people"][0]
    assert future["elapsed_days"] == 0 and future["planned_days_begun"] == 0
    assert future["target_to_date"]["kcal"] == 0


def test_track_validation_and_manual_zero_calories(pair):
    polar, _, _, _, _ = pair
    for period in ["day", "week", "month", "year"]:
        assert polar.get(f"/api/track?period={period}").status_code == 200
    assert polar.get("/api/track?period=quarter").status_code == 422
    assert polar.get("/api/track?date=0001-01-01").status_code == 400
    assert polar.get("/api/track?date=2026-02-30").status_code == 400
    meal = log(polar, 1, items=[item(kcal=0)])
    assert meal["totals"]["kcal"] == 0  # Explicit corrected calories stay as shown by the UI.


def test_old_database_gets_additive_columns_and_existing_meals_remain_readable(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
      INSERT INTO people VALUES (1, 'Legacy');
      CREATE TABLE meals (id INTEGER PRIMARY KEY, person_id INTEGER, eaten_on TEXT, eaten_at TEXT,
        meal_type TEXT, title TEXT, note TEXT, items_json TEXT, kcal REAL, carbs_g REAL, protein_g REAL,
        fat_g REAL, sugar_g REAL, model TEXT, created_at TEXT);
      INSERT INTO meals VALUES (7, 1, '2026-01-01', '2026-01-01T12:00', NULL, 'Old rice', NULL,
        '[]', 300, 60, 6, 2, 0, NULL, '2026-01-01T12:00:00+08:00');
    """)
    db.close()
    store = Store(path)
    assert store.meal(7)["title"] == "Old rice"
    assert store.meal(7)["has_photo"] is False
    assert store.photo_key(7) is None
    assert store.person(1)["name"] == "Legacy"
    assert Store(path).meal(7)["totals"]["kcal"] == 300


def test_local_storage_and_photo_sanitization(tmp_path):
    full, thumb = prepare_photo(png())
    assert Image.open(io.BytesIO(full)).size == (800, 600)
    assert Image.open(io.BytesIO(thumb)).size == (512, 384)
    storage = LocalPhotos(tmp_path / "private")
    key = uuid.uuid4().hex
    storage.put(key, full, thumb)
    assert storage.get(key) == full and storage.get(key, thumbnail=True) == thumb
    with pytest.raises(PhotoError):
        storage.get("../../private")
    storage.delete(key)
    storage.delete(key)
    with pytest.raises(PhotoError):
        storage.get(key)
    image = Image.new("RGB", (50, 40), "green")
    metadata = Image.Exif()
    metadata[270] = "private test metadata"
    source = io.BytesIO()
    image.save(source, "JPEG", exif=metadata)
    clean, small = prepare_photo(source.getvalue())
    assert not Image.open(io.BytesIO(clean)).getexif()
    assert not Image.open(io.BytesIO(small)).getexif()


def test_r2_adapter_uses_private_s3_objects_without_live_network(tmp_path):
    from botocore.response import StreamingBody
    from botocore.stub import Stubber

    cfg = replace(settings(tmp_path), r2_account_id="a" * 32, r2_bucket="private-meals", r2_access_key_id="test", r2_secret_access_key="test")
    adapter = R2Photos(cfg)
    key = "a" * 32
    with Stubber(adapter.client) as stub:
        for name, data in [("photo", b"image"), ("thumb", b"thumbnail")]:
            stub.add_response("put_object", {}, {"Bucket": "private-meals", "Key": f"meals/{key}/{name}.jpg", "Body": data, "ContentType": "image/jpeg"})
        adapter.put(key, b"image", b"thumbnail")
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"thumbnail"), 9), "ContentLength": 9},
                          {"Bucket": "private-meals", "Key": f"meals/{key}/thumb.jpg"})
        assert adapter.get(key, thumbnail=True) == b"thumbnail"
        for name in ["photo", "thumb"]:
            stub.add_response("delete_object", {}, {"Bucket": "private-meals", "Key": f"meals/{key}/{name}.jpg"})
        adapter.delete(key)
        stub.assert_no_pending_responses()

from fastapi.testclient import TestClient
from test_app import PASSWORD, PLAN, item, make_app

from fatties.app import set_password


def test_both_people_own_their_writes_and_share_reads(tmp_path):
    app, store = make_app(tmp_path)
    set_password(store, "Partner", PASSWORD)
    polar, partner = TestClient(app), TestClient(app)
    people = store.people()
    for client, person, other in [(polar, people[0], people[1]), (partner, people[1], people[0])]:
        assert client.post("/api/login", json={"name": person["name"], "password": PASSWORD}).status_code == 200
        assert client.put(f"/api/people/{person['id']}/plan", json={"inputs": PLAN}).status_code == 200
        before = store.plans(other["id"])
        assert client.put(f"/api/people/{other['id']}/plan", json={"inputs": PLAN}).status_code == 403
        assert store.plans(other["id"]) == before
        assert client.post("/api/meals", json={"person_id": other["id"], "title": "x", "items": [item()]}).status_code == 403
    meal = partner.post("/api/meals", json={"person_id": people[1]["id"], "title": "Partner's rice", "items": [item()]}).json()
    assert polar.delete(f"/api/meals/{meal['id']}").status_code == 403
    assert store.meal(meal["id"]) is not None
    for client in [polar, partner]:
        assert len(client.get("/api/day").json()["people"]) == 2
        assert all(person["plan"] for person in client.get("/api/people").json())
    assert partner.delete(f"/api/meals/{meal['id']}").status_code == 204

from datetime import datetime

import pytest
from test_app import item, settings
from test_upgrade import MemoryPhotos, png

from fatties.config import Settings
from fatties.photo_migration import migrate_photos, migrate_photos_command
from fatties.photos import LocalPhotos, PhotoError, prepare_photo
from fatties.store import Store


@pytest.fixture
def local_photos(tmp_path):
    cfg = settings(tmp_path)
    store = Store(cfg.db_path)
    store.ensure_people(["Polar"])
    source = LocalPhotos(cfg.db_path.parent / "photos")
    key = "a" * 32
    source.put(key, *prepare_photo(png()))
    meal = store.add_meal(person_id=1, eaten_at=datetime.now(), meal_type=None, title="Rice", note=None,
                          items=[item()], model="fake", photo_key=key)
    return cfg, store, source, meal, key


def test_migration_checks_both_copies_is_repeatable_and_preserves_originals(local_photos):
    _, store, source, meal, key = local_photos
    target = MemoryPhotos()
    before = store.meal(meal["id"])
    assert migrate_photos(store, source) == 1
    assert not target.images
    assert migrate_photos(store, source, target) == 1
    assert migrate_photos(store, source, target) == 1
    assert target.get(key) == source.get(key)
    assert target.get(key, thumbnail=True) == source.get(key, thumbnail=True)
    assert store.meal(meal["id"]) == before


def test_failed_migration_leaves_source_and_database_intact(local_photos):
    _, store, source, meal, key = local_photos
    target = MemoryPhotos()
    target.fail_put = True
    original = source.get(key)
    with pytest.raises(PhotoError):
        migrate_photos(store, source, target)
    assert source.get(key) == original
    assert store.meal(meal["id"])["has_photo"]
    target.fail_put = False
    assert migrate_photos(store, source, target) == 1


def test_verification_failure_prevents_reporting_success(local_photos):
    _, store, source, _, key = local_photos

    class CorruptTarget(MemoryPhotos):
        def get(self, key, *, thumbnail=False):
            return b"corrupted"

    with pytest.raises(PhotoError, match="verification failed"):
        migrate_photos(store, source, CorruptTarget())
    assert source.get(key)


def test_migration_dry_run_cli_needs_no_cloudflare_login(local_photos, monkeypatch, capsys):
    cfg, _, _, _, _ = local_photos
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: cfg))
    migrate_photos_command(["--dry-run"])
    assert "1 local photo(s) checked; no uploads" in capsys.readouterr().out

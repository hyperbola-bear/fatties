"""Copy private local meal photos to R2 without changing the active backend."""

from __future__ import annotations

import argparse

from .config import Settings
from .photos import LocalPhotos, PhotoError, PhotoStorage, R2Photos
from .store import Store


def migrate_photos(store: Store, source: PhotoStorage, target: PhotoStorage | None = None) -> int:
    """Validate originals, optionally copy/read back, and always keep originals.

    Run with the server stopped. A SQLite write lock prevents meal deletion or
    creation during the copy, including from another process. No rows change.
    Re-running after interruption safely overwrites the same immutable keys.
    """
    with store.lock, store.db:
        store.db.execute("BEGIN IMMEDIATE")
        keys = store.photo_keys()
        for key in keys:
            photo = source.get(key)
            thumbnail = source.get(key, thumbnail=True)
            if target is not None:
                target.put(key, photo, thumbnail)
                if target.get(key) != photo or target.get(key, thumbnail=True) != thumbnail:
                    raise PhotoError("copied photo verification failed; local originals are unchanged")
        return len(keys)


def migrate_photos_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="fatties migrate-photos-to-r2",
        description="With the server stopped, copy and verify local photos in R2. Keeps local originals and configuration unchanged.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Check local photos without connecting to R2")
    args = parser.parse_args(argv)
    settings = Settings.load()
    if not settings.db_path.is_file():
        parser.error("no existing meal database; start and use Fatties before migrating photos")
    store = Store(settings.db_path)
    source = LocalPhotos(settings.photo_dir or settings.db_path.parent / "photos")
    try:
        target = None if args.dry_run else R2Photos(settings)
        count = migrate_photos(store, source, target)
    except (PhotoError, OSError, ValueError) as exc:
        parser.exit(1, f"Migration stopped: {exc}. Keep local storage enabled and retry after fixing the issue.\n")
    finally:
        store.db.close()
    if args.dry_run:
        print(f"Ready: {count} local photo(s) checked; no uploads performed.")
    else:
        print(f"Copied and verified {count} photo(s) and thumbnails in R2. Local originals and configuration are unchanged.")
        print("You can now set FATTIES_PHOTO_STORAGE=r2 and restart Fatties. Keep the originals as a backup.")

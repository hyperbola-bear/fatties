"""Private photo storage. Uploads occur only when a meal is saved, never on analysis."""

from __future__ import annotations

import io
import re
import warnings
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings


class PhotoError(Exception):
    pass


class PhotoStorage(Protocol):
    def put(self, key: str, photo: bytes, thumbnail: bytes) -> None: ...
    def get(self, key: str, *, thumbnail: bool = False) -> bytes: ...
    def delete(self, key: str) -> None: ...


def prepare_photo(data: bytes) -> tuple[bytes, bytes]:
    """Decode rather than trust MIME, remove metadata, and retain only bounded JPEGs."""
    if not data or len(data) > 5 * 1024 * 1024:
        raise PhotoError("photo must be between 1 byte and 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 25_000_000:
                    raise PhotoError("photo is too large; choose a smaller image")
                image = ImageOps.exif_transpose(original).convert("RGB")
                result = []
                for size, quality in [(1568, 85), (512, 80)]:
                    copy = image.copy()
                    copy.thumbnail((size, size))
                    output = io.BytesIO()
                    copy.save(output, "JPEG", quality=quality, optimize=True)
                    result.append(output.getvalue())
                return result[0], result[1]
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise PhotoError("this photo cannot be read; choose a JPEG, PNG, or WebP image") from None


def object_name(key: str, thumbnail: bool) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise PhotoError("invalid photo key")
    return f"meals/{key}/{'thumb' if thumbnail else 'photo'}.jpg"


class LocalPhotos:
    """Development/private-server fallback; never mounted as public static files."""

    def __init__(self, directory: Path):
        self.directory = directory

    def put(self, key: str, photo: bytes, thumbnail: bytes) -> None:
        for small, data in [(False, photo), (True, thumbnail)]:
            path = self.directory / object_name(key, small)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def get(self, key: str, *, thumbnail: bool = False) -> bytes:
        try:
            return (self.directory / object_name(key, thumbnail)).read_bytes()
        except OSError:
            raise PhotoError("photo unavailable") from None

    def delete(self, key: str) -> None:
        for small in [False, True]:
            (self.directory / object_name(key, small)).unlink(missing_ok=True)


class R2Photos:
    def __init__(self, settings: Settings):
        if not all([settings.r2_account_id, settings.r2_bucket, settings.r2_access_key_id, settings.r2_secret_access_key]):
            raise ValueError("R2 needs R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY")
        import boto3
        from botocore.config import Config

        self.bucket = settings.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
        )

    def put(self, key: str, photo: bytes, thumbnail: bytes) -> None:
        try:
            for small, data in [(False, photo), (True, thumbnail)]:
                self.client.put_object(Bucket=self.bucket, Key=object_name(key, small), Body=data, ContentType="image/jpeg")
        except Exception:
            raise PhotoError("photo storage is unavailable; your meal has not been saved") from None

    def get(self, key: str, *, thumbnail: bool = False) -> bytes:
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=object_name(key, thumbnail))
            with result["Body"] as body:
                return body.read()
        except Exception:
            raise PhotoError("photo unavailable") from None

    def delete(self, key: str) -> None:
        try:
            for small in [False, True]:
                self.client.delete_object(Bucket=self.bucket, Key=object_name(key, small))
        except Exception:
            raise PhotoError("photo cleanup needs to be retried") from None


def make_photo_storage(settings: Settings) -> PhotoStorage:
    if settings.photo_storage == "r2":
        return R2Photos(settings)
    if settings.photo_storage == "local":
        return LocalPhotos(settings.photo_dir or settings.db_path.parent / "photos")
    raise ValueError("FATTIES_PHOTO_STORAGE must be local or r2")

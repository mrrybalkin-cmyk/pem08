"""Validated original-byte uploads with application-owned paths and cleanup."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
import warnings

from PIL import Image, UnidentifiedImageError


class InvalidImage(ValueError):
    pass


class ImageTooLarge(InvalidImage):
    pass


@dataclass(frozen=True)
class ValidatedImage:
    mime_type: str
    extension: str
    width: int
    height: int


@dataclass(frozen=True)
class StoredFile:
    storage_path: str
    sha256: str


@dataclass(frozen=True)
class RemovedFile:
    storage_path: str
    content: bytes


FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


def validate_image(content: bytes, declared_mime: str | None, max_bytes: int) -> ValidatedImage:
    if len(content) > max_bytes:
        raise ImageTooLarge("Image exceeds MAX_IMAGE_MB")
    if declared_mime not in {item[0] for item in FORMATS.values()}:
        raise InvalidImage("Only JPEG, PNG and WebP images are supported")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                actual = FORMATS.get(image.format)
                if actual is None or actual[0] != declared_mime:
                    raise InvalidImage("Declared MIME does not match image format")
                width, height = image.size
                image.verify()
            # verify() alone may not decode all pixels (notably JPEG).
            with Image.open(BytesIO(content)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidImage("Invalid image payload") from exc
    return ValidatedImage(actual[0], actual[1], width, height)


class StorageService:
    def __init__(self, upload_dir: Path):
        self.upload_dir = upload_dir.resolve()

    def resolve(self, storage_path: str) -> Path:
        # Only application-generated flat filenames can reference uploads.
        name = Path(storage_path)
        if name.name != storage_path or any(char in storage_path for char in ("/", "\\", ":")):
            raise ValueError("Unsafe storage path")
        UUID(name.stem)
        if name.suffix not in ({item[1] for item in FORMATS.values()} | {".pdf"}):
            raise ValueError("Unsupported storage extension")
        path = (self.upload_dir / name).resolve()
        if path.parent != self.upload_dir:
            raise ValueError("Storage path escapes upload directory")
        return path

    def save(self, content: bytes, extension: str) -> StoredFile:
        name = f"{uuid4()}{extension}"
        path = self.resolve(name)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        stream = path.open("xb")
        try:
            with stream:
                stream.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StoredFile(name, hashlib.sha256(content).hexdigest())

    def delete(self, storage_path: str) -> None:
        self.resolve(storage_path).unlink(missing_ok=True)

    def read(self, storage_path: str, max_bytes: int) -> bytes:
        with self.resolve(storage_path).open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise OSError("Persisted image exceeds configured size limit")
        return content

    def remove_reversibly(self, storage_path: str) -> RemovedFile | None:
        path = self.resolve(storage_path)
        if not path.exists():
            return None
        content = path.read_bytes()
        path.unlink()
        return RemovedFile(storage_path, content)

    def restore(self, removed: RemovedFile) -> None:
        path = self.resolve(removed.storage_path)
        with path.open("xb") as stream:
            stream.write(removed.content)

import hashlib
from io import BytesIO
from uuid import UUID

from PIL import Image
import pytest

from backend.services.storage_service import InvalidImage, StorageService, validate_image


@pytest.mark.parametrize("format,mime,extension", [
    ("JPEG", "image/jpeg", ".jpg"), ("PNG", "image/png", ".png"), ("WEBP", "image/webp", ".webp"),
])
def test_validated_original_bytes_storage_and_reversible_delete(tmp_path, format, mime, extension):
    output = BytesIO()
    Image.new("RGB", (4, 3), "blue").save(output, format=format)
    content = output.getvalue()
    info = validate_image(content, mime, len(content))
    assert (info.width, info.height, info.extension) == (4, 3, extension)
    storage = StorageService(tmp_path)
    first = storage.save(content, info.extension)
    second = storage.save(content, info.extension)
    assert first.storage_path != second.storage_path
    UUID(storage.resolve(first.storage_path).stem)
    assert storage.resolve(first.storage_path).read_bytes() == content
    assert first.sha256 == hashlib.sha256(content).hexdigest()
    removed = storage.remove_reversibly(first.storage_path)
    assert not storage.resolve(first.storage_path).exists()
    storage.restore(removed)
    assert storage.resolve(first.storage_path).read_bytes() == content
    storage.delete(first.storage_path)
    storage.delete(first.storage_path)
    storage.delete(second.storage_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["../outside.png", "..\\outside.png", "/tmp/out.png", "C:\\out.png", "C:out.png", "a.png"])
def test_storage_paths_cannot_escape(tmp_path, name):
    with pytest.raises(ValueError):
        StorageService(tmp_path).delete(name)


def test_storage_symlink_cannot_escape(tmp_path):
    from uuid import uuid4
    root = tmp_path / "uploads"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    link = root / f"{uuid4()}.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(ValueError):
        StorageService(root).delete(link.name)
    assert outside.read_bytes() == b"outside"


def test_unsupported_and_corrupt_images():
    output = BytesIO()
    Image.new("RGB", (2, 2)).save(output, format="GIF")
    for content, mime in [(b"corrupt", "image/png"), (output.getvalue(), "image/png"), (output.getvalue(), "image/gif")]:
        with pytest.raises(InvalidImage):
            validate_image(content, mime, 10000)


def test_partial_write_cleanup_and_collision_protection(tmp_path, monkeypatch):
    from pathlib import Path
    from uuid import uuid4
    storage = StorageService(tmp_path)
    original_open = Path.open

    class PartialWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, content):
            self.stream.write(content[:2])
            raise OSError("Injected partial write")

    def failing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return PartialWrite(stream) if mode == "xb" else stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", failing_open)
        with pytest.raises(OSError, match="partial write"):
            storage.save(b"original bytes", ".png")
    assert list(tmp_path.iterdir()) == []
    identifier = uuid4()
    existing = tmp_path / f"{identifier}.png"
    existing.write_bytes(b"existing")
    monkeypatch.setattr("backend.services.storage_service.uuid4", lambda: identifier)
    with pytest.raises(FileExistsError):
        storage.save(b"new content", ".png")
    assert existing.read_bytes() == b"existing"

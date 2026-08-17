import hashlib
from pathlib import Path

from app.services.storage import save_upload


def test_save_upload_writes_file_and_returns_hash(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path)

    file_path, content_hash = save_upload("bill.pdf", b"hello world")

    assert Path(file_path).exists()
    assert Path(file_path).read_bytes() == b"hello world"
    assert content_hash == hashlib.sha256(b"hello world").hexdigest()


def test_save_upload_generates_unique_filenames(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path)

    path_a, _ = save_upload("bill.pdf", b"content-a")
    path_b, _ = save_upload("bill.pdf", b"content-b")

    assert path_a != path_b

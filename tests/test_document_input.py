import base64

import pytest

from app.services.document_input import UnsupportedFileTypeError, build_content_block


def test_build_content_block_for_pdf(tmp_path):
    pdf_path = tmp_path / "bill.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes")

    block = build_content_block(str(pdf_path))

    assert block["type"] == "document"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["source"]["data"] == base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode()


def test_build_content_block_for_png(tmp_path):
    png_path = tmp_path / "photo.png"
    png_path.write_bytes(b"fake-png-bytes")

    block = build_content_block(str(png_path))

    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_build_content_block_for_jpeg(tmp_path):
    for suffix in (".jpg", ".jpeg"):
        jpeg_path = tmp_path / f"photo{suffix}"
        jpeg_path.write_bytes(b"fake-jpeg-bytes")

        block = build_content_block(str(jpeg_path))

        assert block["type"] == "image"
        assert block["source"]["media_type"] == "image/jpeg"


def test_build_content_block_raises_on_unsupported_extension(tmp_path):
    txt_path = tmp_path / "notes.txt"
    txt_path.write_bytes(b"plain text")

    with pytest.raises(UnsupportedFileTypeError):
        build_content_block(str(txt_path))

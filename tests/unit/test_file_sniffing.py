"""A file's type comes from its first bytes, never from its name or the browser's claim."""

import pytest

from app.modules.tickets.files import sniff_content_type
from app.modules.tickets.models import DocumentType


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"%PDF-1.7\n", DocumentType.PDF),
        (b"\x89PNG\r\n\x1a\n\x00\x00", DocumentType.PNG),
        (b"\xff\xd8\xff\xe0", DocumentType.JPEG),
        (b"\xff\xd8\xff\xdb", DocumentType.JPEG),
        (b"<html>", None),
        (b"GIF89a", None),
        (b"%PDF", None),  # cut short: not the full signature
        (b"\x89PNG", None),
        (b"", None),
    ],
)
def test_the_type_comes_from_the_first_bytes(head: bytes, expected: DocumentType | None) -> None:
    assert sniff_content_type(head) == expected

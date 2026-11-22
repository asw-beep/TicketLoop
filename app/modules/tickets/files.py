"""Decide a file's type from its first bytes ("magic numbers").

The browser's Content-Type and the filename are whatever the uploader says; anyone can name
an HTML page receipt.png. Every format we accept starts with a fixed signature, so we read
that instead (OWASP File Upload Cheat Sheet). A file we cannot identify is refused.
"""

from app.modules.tickets.models import DocumentType

_SIGNATURES: tuple[tuple[bytes, DocumentType], ...] = (
    (b"%PDF-", DocumentType.PDF),
    (b"\x89PNG\r\n\x1a\n", DocumentType.PNG),
    (b"\xff\xd8\xff", DocumentType.JPEG),
)

# The longest signature above; read at least this many bytes before calling.
SNIFF_BYTES = 8


def sniff_content_type(head: bytes) -> DocumentType | None:
    for signature, document_type in _SIGNATURES:
        if head.startswith(signature):
            return document_type
    return None

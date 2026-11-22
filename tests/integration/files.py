"""Small but real files for upload tests. Each starts with its format's signature bytes."""

# A 1x1 blue PNG.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00"
    b"\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xd0\xf6>\x02\x00\x01\xdf\x01;\x8f\xc9#\x18"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# A minimal one-page PDF.
PDF_BYTES = (
    b"%PDF-1.7\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 72]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

# The start of a JPEG (SOI and a JFIF APP0 segment) and its end marker. Enough for a
# signature check; no image viewer needed in tests.
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

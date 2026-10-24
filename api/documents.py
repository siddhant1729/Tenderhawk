"""Bid document text: PDF -> text (PyMuPDF) and cleanup of bilingual GeM documents.

    python -m api.documents bid.pdf      # print the cleaned text of a local PDF
"""
import re
import sys
from pathlib import Path

import pymupdf

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def pdf_to_text(data: bytes) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def clean_text(text: str) -> str:
    """Drop the Hindi half of bilingual labels ("ईएमड रािश/EMD Amount" -> "EMD Amount").

    GeM PDFs embed Devanagari with broken glyph maps, so it extracts as noise that costs tokens
    and confuses clause matching. English segments, numbers and bid numbers (GEM/2026/B/...) survive.
    """
    out = []
    for line in text.splitlines():
        if _DEVANAGARI.search(line):
            line = "/".join(p for p in line.split("/") if not _DEVANAGARI.search(p))
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def to_text(raw: str | bytes) -> str:
    """Whatever the fetcher handed us (text, or PDF bytes) -> cleaned text."""
    if isinstance(raw, bytes):
        raw = pdf_to_text(raw) if raw[:5] == b"%PDF-" else raw.decode("utf-8", "replace")
    return clean_text(raw)


if __name__ == "__main__":
    print(to_text(Path(sys.argv[1]).read_bytes()))

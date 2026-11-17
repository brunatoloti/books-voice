from __future__ import annotations

import re


def chapter_paragraphs(chapter: dict) -> list[str]:
    """Return readable paragraphs from both current and legacy saved books.

    Books already stored in Google Sheets contain narration segments rather
    than the original EPUB markup. Line breaks kept during extraction recover
    most paragraph boundaries; a segment boundary is also a safe fallback for
    older content.
    """
    paragraphs: list[str] = []
    for segment in chapter.get("segments", []):
        parts = re.split(r"\n+", str(segment))
        paragraphs.extend(part.strip() for part in parts if part.strip())

    title = re.sub(r"\s+", " ", str(chapter.get("title", ""))).strip().casefold()
    if paragraphs:
        first = re.sub(r"\s+", " ", paragraphs[0]).strip().casefold()
        if title and first == title:
            paragraphs.pop(0)
    return paragraphs

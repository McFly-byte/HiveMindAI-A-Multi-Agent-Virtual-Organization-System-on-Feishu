from __future__ import annotations

import re
from pathlib import Path


_PARAGRAPH = re.compile(r"\n\s*\n+")


def read_text_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".rst", ".log"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".json":
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"unsupported file type: {suffix}. Convert to .md/.txt before ingest.")


def chunk_text(text: str, target_chars: int = 1600, overlap_chars: int = 200) -> list[str]:
    """Paragraph-aware chunker.

    Splits on blank lines first (paragraphs are semantic boundaries), then
    greedily packs paragraphs until the target size, falling back to a hard
    sliding window for paragraphs that exceed the target on their own.

    target_chars approximates ~512 tokens for mixed CJK/ASCII content.
    """
    text = (text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in _PARAGRAPH.split(text) if p.strip()]
    chunks: list[str] = []
    buffer = ""

    for paragraph in paragraphs:
        if len(paragraph) > target_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_sliding_window(paragraph, target_chars, overlap_chars))
            continue

        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= target_chars:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
            buffer = paragraph

    if buffer:
        chunks.append(buffer)
    return chunks


def _sliding_window(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0:
        return [text]
    step = max(1, size - overlap)
    out: list[str] = []
    i = 0
    while i < len(text):
        out.append(text[i : i + size])
        i += step
    return out

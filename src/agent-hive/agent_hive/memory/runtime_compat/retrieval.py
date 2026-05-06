from __future__ import annotations

import re
import sqlite3
from typing import Iterable


_FTS_TOKEN = re.compile(r"[\w一-鿿]+", re.UNICODE)
_CJK_RUN = re.compile(r"[一-鿿]+")
_TRIGRAM_LEN = 3


def _to_match_query(query: str) -> str:
    """Sanitize free-form query into an FTS5 MATCH expression.

    The schema uses the ``trigram`` tokenizer, which indexes overlapping
    3-char windows. A phrase query against trigram requires the *whole*
    phrase to appear consecutively, which is too strict for paraphrased
    queries. We therefore explode each token into 3-char sliding windows
    and OR them so partial overlap still scores.
    """
    tokens = _FTS_TOKEN.findall(query or "")
    if not tokens:
        return ""

    windows: list[str] = []
    for token in tokens:
        if _CJK_RUN.fullmatch(token) and len(token) > _TRIGRAM_LEN:
            for i in range(len(token) - _TRIGRAM_LEN + 1):
                windows.append(token[i : i + _TRIGRAM_LEN])
        elif len(token) >= _TRIGRAM_LEN:
            windows.append(token)
        # tokens shorter than the trigram length cannot match — drop them

    # Deduplicate while preserving order, then OR.
    seen: set[str] = set()
    unique: list[str] = []
    for w in windows:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    if not unique:
        return ""
    return " OR ".join(f'"{w}"' for w in unique)


def bm25_search(
    conn: sqlite3.Connection,
    table: str,
    fts_table: str,
    query: str,
    top_k: int,
    where_sql: str = "",
    where_params: tuple = (),
) -> list[tuple[str, float]]:
    """Return [(row_id, score)] from FTS5 BM25, lower bm25() == better match."""
    match = _to_match_query(query)
    if not match:
        return []

    sql = f"""
        SELECT t.id, bm25({fts_table}) AS rank
        FROM {fts_table}
        JOIN {table} t ON t.rowid = {fts_table}.rowid
        WHERE {fts_table} MATCH ?
        {('AND ' + where_sql) if where_sql else ''}
        ORDER BY rank ASC
        LIMIT ?
    """
    params = (match, *where_params, top_k)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # No FTS match (empty index, no MATCH support, etc.)
        return []
    # Convert bm25 rank (smaller = better) into a positive score.
    return [(row[0], 1.0 / (1.0 + float(row[1]))) for row in rows]


def reciprocal_rank_fusion(
    rankings: Iterable[list[tuple[str, float]]],
    k: int = 60,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked lists via RRF.

    rankings: each element is a list of (id, score) sorted best-first.
    Returns a single fused list of (id, fused_score) sorted best-first.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, (item_id, _score) in enumerate(ranking):
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

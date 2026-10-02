from __future__ import annotations

import base64
import gzip
import json
from datetime import datetime, timezone

import pandas as pd


CONTENT_VERSION = 1
CHUNK_SIZE = 40_000

COLUMNS = [
    "owner_id",
    "book_id",
    "title",
    "author",
    "file_name",
    "total_chapters",
    "chunk_index",
    "chunk_count",
    "content_version",
    "payload",
    "created_at",
    "updated_at",
]


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def read_content(conn, worksheet: str) -> pd.DataFrame:
    try:
        frame = conn.read(worksheet=worksheet, ttl=0)
    except Exception as exc:
        if exc.__class__.__name__ not in {"WorksheetNotFound", "SpreadsheetNotFound"}:
            raise
        return _empty_frame()

    if frame is None or frame.empty:
        return _empty_frame()
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[COLUMNS].copy()


def encode_book(book: dict, chunk_size: int = CHUNK_SIZE) -> list[str]:
    raw = json.dumps(
        book,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    return [
        encoded[index : index + chunk_size]
        for index in range(0, len(encoded), chunk_size)
    ]


def decode_book(chunks: list[str]) -> dict:
    try:
        encoded = "".join(chunks)
        compressed = base64.b64decode(encoded.encode("ascii"), validate=True)
        raw = gzip.decompress(compressed)
        book = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("O conteúdo salvo está incompleto ou corrompido.") from exc
    if not isinstance(book, dict) or not book.get("chapters"):
        raise ValueError("O conteúdo salvo não possui capítulos válidos.")
    return book


def list_saved_books(conn, worksheet: str, owner_id: str) -> pd.DataFrame:
    frame = read_content(conn, worksheet)
    if frame.empty:
        return frame

    matches = frame[frame["owner_id"].astype(str) == str(owner_id)].copy()
    if matches.empty:
        return matches

    matches["chunk_index"] = pd.to_numeric(
        matches["chunk_index"], errors="coerce"
    ).fillna(-1).astype(int)
    matches["chunk_count"] = pd.to_numeric(
        matches["chunk_count"], errors="coerce"
    ).fillna(0).astype(int)
    matches["updated_at"] = pd.to_datetime(
        matches["updated_at"], errors="coerce", utc=True
    )
    books = (
        matches.sort_values(["updated_at", "chunk_index"], ascending=[False, True])
        .groupby("book_id", as_index=False, sort=False)
        .first()
    )
    return books.sort_values("updated_at", ascending=False, na_position="last")


def load_saved_book(
    conn,
    worksheet: str,
    owner_id: str,
    book_id: str,
) -> dict | None:
    frame = read_content(conn, worksheet)
    if frame.empty:
        return None

    matches = frame[
        (frame["owner_id"].astype(str) == str(owner_id))
        & (frame["book_id"].astype(str) == str(book_id))
    ].copy()
    if matches.empty:
        return None

    matches["chunk_index"] = pd.to_numeric(
        matches["chunk_index"], errors="coerce"
    ).fillna(-1).astype(int)
    matches["chunk_count"] = pd.to_numeric(
        matches["chunk_count"], errors="coerce"
    ).fillna(0).astype(int)
    matches = matches.sort_values("chunk_index")

    expected_count = int(matches.iloc[0]["chunk_count"])
    expected_indexes = list(range(expected_count))
    actual_indexes = matches["chunk_index"].tolist()
    if expected_count <= 0 or actual_indexes != expected_indexes:
        raise ValueError("O conteúdo salvo está com blocos ausentes.")

    book = decode_book(matches["payload"].astype(str).tolist())
    if str(book.get("book_id")) != str(book_id):
        raise ValueError("O identificador do conteúdo salvo não confere.")
    return book


def save_book_content(
    conn,
    worksheet: str,
    owner_id: str,
    book: dict,
    file_name: str,
) -> None:
    frame = read_content(conn, worksheet)
    now = datetime.now(timezone.utc).isoformat()
    created_at = now

    if not frame.empty:
        mask = (
            (frame["owner_id"].astype(str) == str(owner_id))
            & (frame["book_id"].astype(str) == str(book["book_id"]))
        )
        previous = frame.loc[mask]
        if not previous.empty:
            previous_created_at = previous.iloc[0].get("created_at")
            if pd.notna(previous_created_at) and str(previous_created_at).strip():
                created_at = str(previous_created_at)
        frame = frame.loc[~mask].copy()

    chunks = encode_book(book)
    rows = pd.DataFrame(
        [
            {
                "owner_id": owner_id,
                "book_id": book["book_id"],
                "title": book["title"],
                "author": book["author"],
                "file_name": file_name,
                "total_chapters": len(book["chapters"]),
                "chunk_index": index,
                "chunk_count": len(chunks),
                "content_version": CONTENT_VERSION,
                "payload": chunk,
                "created_at": created_at,
                "updated_at": now,
            }
            for index, chunk in enumerate(chunks)
        ],
        columns=COLUMNS,
    )
    updated = rows if frame.empty else pd.concat([frame, rows], ignore_index=True)[COLUMNS]
    try:
        conn.update(worksheet=worksheet, data=updated)
    except Exception as exc:
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
        conn.create(worksheet=worksheet, data=updated)


def delete_saved_book(conn, worksheet: str, owner_id: str, book_id: str) -> None:
    frame = read_content(conn, worksheet)
    if frame.empty:
        return
    mask = (
        (frame["owner_id"].astype(str) == str(owner_id))
        & (frame["book_id"].astype(str) == str(book_id))
    )
    if not mask.any():
        return
    conn.update(worksheet=worksheet, data=frame.loc[~mask, COLUMNS].copy())

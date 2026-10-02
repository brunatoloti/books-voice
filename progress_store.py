from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


COLUMNS = [
    "owner_id",
    "book_id",
    "title",
    "author",
    "chapter_index",
    "segment_index",
    "total_chapters",
    "current_chapter_title",
    "progress_percent",
    "voice",
    "rate",
    "completed",
    "started_at",
    "updated_at",
]


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def read_progress(conn, worksheet: str) -> pd.DataFrame:
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


def load_book_progress(
    conn,
    worksheet: str,
    owner_id: str,
    book_id: str,
) -> dict | None:
    frame = read_progress(conn, worksheet)
    if frame.empty:
        return None

    matches = frame[
        (frame["owner_id"].astype(str) == str(owner_id))
        & (frame["book_id"].astype(str) == str(book_id))
    ]
    if matches.empty:
        return None

    row = matches.iloc[-1].to_dict()
    row["chapter_index"] = int(float(row.get("chapter_index", 0) or 0))
    row["segment_index"] = int(float(row.get("segment_index", 0) or 0))
    row["progress_percent"] = float(row.get("progress_percent", 0) or 0)
    row["rate"] = int(float(row.get("rate", 0) or 0))
    row["completed"] = str(row.get("completed", "false")).strip().lower() in {
        "true",
        "1",
        "yes",
    }
    return row


def load_user_progress(conn, worksheet: str, owner_id: str) -> pd.DataFrame:
    frame = read_progress(conn, worksheet)
    if frame.empty:
        return frame

    frame = frame[frame["owner_id"].astype(str) == str(owner_id)].copy()
    if frame.empty:
        return frame

    frame["progress_percent"] = pd.to_numeric(
        frame["progress_percent"], errors="coerce"
    ).fillna(0.0)
    frame["chapter_index"] = pd.to_numeric(
        frame["chapter_index"], errors="coerce"
    ).fillna(0).astype(int)
    frame["total_chapters"] = pd.to_numeric(
        frame["total_chapters"], errors="coerce"
    ).fillna(0).astype(int)
    frame["completed"] = frame["completed"].astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )
    frame["updated_at"] = pd.to_datetime(
        frame["updated_at"], errors="coerce", utc=True
    )
    return frame.sort_values("updated_at", ascending=False, na_position="last")


def save_book_progress(
    conn,
    worksheet: str,
    owner_id: str,
    book: dict,
    chapter_index: int,
    segment_index: int,
    progress_percent: float,
    voice: str,
    rate: int,
    completed: bool,
) -> None:
    frame = read_progress(conn, worksheet)
    now = datetime.now(timezone.utc).isoformat()
    started_at = now
    if not frame.empty:
        mask = (
            (frame["owner_id"].astype(str) == str(owner_id))
            & (frame["book_id"].astype(str) == str(book["book_id"]))
        )
        previous = frame.loc[mask]
        if not previous.empty:
            previous_started_at = previous.iloc[-1].get("started_at")
            if pd.notna(previous_started_at) and str(previous_started_at).strip():
                started_at = str(previous_started_at)
        frame = frame.loc[~mask].copy()

    row = pd.DataFrame(
        [
            {
                "owner_id": owner_id,
                "book_id": book["book_id"],
                "title": book["title"],
                "author": book["author"],
                "chapter_index": int(chapter_index),
                "segment_index": int(segment_index),
                "total_chapters": len(book["chapters"]),
                "current_chapter_title": book["chapters"][chapter_index]["title"],
                "progress_percent": round(float(progress_percent), 2),
                "voice": voice,
                "rate": int(rate),
                "completed": bool(completed),
                "started_at": started_at,
                "updated_at": now,
            }
        ],
        columns=COLUMNS,
    )
    updated = row if frame.empty else pd.concat([frame, row], ignore_index=True)[COLUMNS]
    try:
        conn.update(worksheet=worksheet, data=updated)
    except Exception as exc:
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
        conn.create(worksheet=worksheet, data=updated)


def delete_book_progress(conn, worksheet: str, owner_id: str, book_id: str) -> None:
    frame = read_progress(conn, worksheet)
    if frame.empty:
        return
    mask = (
        (frame["owner_id"].astype(str) == str(owner_id))
        & (frame["book_id"].astype(str) == str(book_id))
    )
    if not mask.any():
        return
    conn.update(worksheet=worksheet, data=frame.loc[~mask, COLUMNS].copy())

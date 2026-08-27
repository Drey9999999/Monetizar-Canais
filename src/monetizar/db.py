"""Estado do projeto em SQLite: fontes usadas, cortes gerados e fila de posts.

Sem isso, rodar o pipeline duas vezes gera os mesmos cortes de novo — e
republicar o mesmo clipe é a forma mais rápida de um canal levar strike de
conteúdo repetitivo.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    video_id     TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT,
    channel      TEXT,
    duration     REAL,
    view_count   INTEGER,
    downloaded_at TEXT,
    status       TEXT DEFAULT 'downloaded',
    meta         TEXT
);

CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL REFERENCES sources(video_id),
    channel_slug  TEXT NOT NULL,
    path          TEXT NOT NULL UNIQUE,
    start_s       REAL NOT NULL,
    end_s         REAL NOT NULL,
    score         REAL,
    title         TEXT,
    description   TEXT,
    hashtags      TEXT,
    hook          TEXT,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'ready'
);

CREATE TABLE IF NOT EXISTS schedule (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id      INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    channel_slug TEXT NOT NULL,
    platform     TEXT NOT NULL,
    publish_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    posted_at    TEXT,
    note         TEXT,
    UNIQUE (clip_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_clips_channel ON clips(channel_slug, status);
CREATE INDEX IF NOT EXISTS idx_schedule_due ON schedule(status, publish_at);
"""


@dataclass
class ClipRecord:
    id: int
    source_id: str
    channel_slug: str
    path: str
    start_s: float
    end_s: float
    score: float
    title: str
    description: str
    hashtags: list[str]
    hook: str
    created_at: str
    status: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ClipRecord":
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            channel_slug=row["channel_slug"],
            path=row["path"],
            start_s=row["start_s"],
            end_s=row["end_s"],
            score=row["score"] or 0.0,
            title=row["title"] or "",
            description=row["description"] or "",
            hashtags=json.loads(row["hashtags"] or "[]"),
            hook=row["hook"] or "",
            created_at=row["created_at"],
            status=row["status"],
        )


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- fontes

    def has_source(self, video_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sources WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None

    def add_source(self, info: Any, status: str = "downloaded") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sources
                   (video_id, url, title, channel, duration, view_count, downloaded_at, status, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(video_id) DO UPDATE SET status = excluded.status""",
                (
                    info.video_id,
                    info.url,
                    info.title,
                    info.channel,
                    info.duration,
                    info.view_count,
                    datetime.now().isoformat(timespec="seconds"),
                    status,
                    json.dumps({"tags": info.tags, "channel_id": info.channel_id}),
                ),
            )

    def mark_source(self, video_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE video_id = ?", (status, video_id)
            )

    # ---------------------------------------------------------------- cortes

    def add_clip(
        self,
        *,
        source_id: str,
        channel_slug: str,
        path: str,
        start_s: float,
        end_s: float,
        score: float,
        title: str,
        description: str,
        hashtags: list[str],
        hook: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO clips
                   (source_id, channel_slug, path, start_s, end_s, score,
                    title, description, hashtags, hook, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_id, channel_slug, path, start_s, end_s, score,
                    title, description, json.dumps(hashtags, ensure_ascii=False),
                    hook, datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid)

    def clip_exists(self, path: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM clips WHERE path = ?", (path,)).fetchone()
        return row is not None

    def ready_clips(self, channel_slug: str | None = None) -> list[ClipRecord]:
        """Cortes prontos e ainda não colocados na fila de publicação."""
        query = """SELECT c.* FROM clips c
                   WHERE c.status = 'ready'
                     AND NOT EXISTS (SELECT 1 FROM schedule s WHERE s.clip_id = c.id)"""
        params: tuple[Any, ...] = ()
        if channel_slug:
            query += " AND c.channel_slug = ?"
            params = (channel_slug,)
        query += " ORDER BY c.score DESC, c.id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ClipRecord.from_row(r) for r in rows]

    def count_clips(self, channel_slug: str | None = None) -> int:
        query = "SELECT COUNT(*) AS n FROM clips"
        params: tuple[Any, ...] = ()
        if channel_slug:
            query += " WHERE channel_slug = ?"
            params = (channel_slug,)
        with self._connect() as conn:
            return int(conn.execute(query, params).fetchone()["n"])

    # ------------------------------------------------------------------ fila

    def schedule_clip(
        self, clip_id: int, channel_slug: str, platform: str, publish_at: str
    ) -> bool:
        """Agenda um corte. Devolve False se já estava agendado nessa plataforma."""
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO schedule
                   (clip_id, channel_slug, platform, publish_at)
                   VALUES (?, ?, ?, ?)""",
                (clip_id, channel_slug, platform, publish_at),
            )
            return cur.rowcount > 0

    def pending_schedule(
        self, channel_slug: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = """SELECT s.id, s.clip_id, s.channel_slug, s.platform, s.publish_at,
                          s.status, c.path, c.title, c.description, c.hashtags, c.hook
                   FROM schedule s JOIN clips c ON c.id = s.clip_id
                   WHERE s.status = 'pending'"""
        params: list[Any] = []
        if channel_slug:
            query += " AND s.channel_slug = ?"
            params.append(channel_slug)
        query += " ORDER BY s.publish_at ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def mark_posted(self, schedule_id: int, note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE schedule
                   SET status = 'posted', posted_at = ?, note = ?
                   WHERE id = ?""",
                (datetime.now().isoformat(timespec="seconds"), note, schedule_id),
            )

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                "sources": conn.execute("SELECT COUNT(*) n FROM sources").fetchone()["n"],
                "clips": conn.execute("SELECT COUNT(*) n FROM clips").fetchone()["n"],
                "scheduled": conn.execute(
                    "SELECT COUNT(*) n FROM schedule WHERE status = 'pending'"
                ).fetchone()["n"],
                "posted": conn.execute(
                    "SELECT COUNT(*) n FROM schedule WHERE status = 'posted'"
                ).fetchone()["n"],
            }

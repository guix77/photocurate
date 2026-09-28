"""Local experiment state. Frozen sets and runs refer to immutable media versions."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    current_version_id INTEGER REFERENCES media_versions(id),
    status TEXT NOT NULL,
    error TEXT,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_versions (
    id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id),
    file_size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    capture_datetime TEXT,
    latitude REAL,
    longitude REAL,
    orientation INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proxies (
    version_id INTEGER NOT NULL REFERENCES media_versions(id),
    long_edge INTEGER NOT NULL,
    path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    PRIMARY KEY (version_id, long_edge)
);
CREATE TABLE IF NOT EXISTS evaluation_sets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_root TEXT NOT NULL,
    seed INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_members (
    set_id INTEGER NOT NULL REFERENCES evaluation_sets(id),
    position INTEGER NOT NULL,
    media_id INTEGER NOT NULL REFERENCES media(id),
    version_id INTEGER NOT NULL REFERENCES media_versions(id),
    PRIMARY KEY (set_id, position),
    UNIQUE (set_id, media_id)
);
CREATE TABLE IF NOT EXISTS evaluation_extensions (
    id INTEGER PRIMARY KEY,
    set_id INTEGER NOT NULL REFERENCES evaluation_sets(id),
    previous_size INTEGER NOT NULL,
    new_size INTEGER NOT NULL,
    seed TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS human_labels (
    set_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('0', '1', '2', '3', '?')),
    annotator TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (set_id, position),
    FOREIGN KEY (set_id, position) REFERENCES evaluation_members(set_id, position)
);
CREATE TABLE IF NOT EXISTS label_history (
    id INTEGER PRIMARY KEY,
    set_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    annotator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (set_id, position) REFERENCES evaluation_members(set_id, position)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    set_id INTEGER NOT NULL REFERENCES evaluation_sets(id),
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_members (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    position INTEGER NOT NULL,
    version_id INTEGER NOT NULL REFERENCES media_versions(id),
    proxy_path TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
);
CREATE TABLE IF NOT EXISTS results (
    run_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    output_json TEXT,
    raw_response TEXT,
    score REAL,
    usage_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    cost_source TEXT,
    latency_ms REAL NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, position),
    FOREIGN KEY (run_id, position) REFERENCES run_members(run_id, position)
);
"""


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError(f"Unsupported database schema version: {version}")
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA user_version = 1")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def get_set(self, name: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM evaluation_sets WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown evaluation set: {name}")
        return row

    def get_run(self, run_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run: {run_id}")
        return row

    def members(self, set_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT em.*, m.source_path, v.file_size, v.mtime_ns,
                      hl.label, hl.annotator
               FROM evaluation_members em
               JOIN media m ON m.id = em.media_id
               JOIN media_versions v ON v.id = em.version_id
               LEFT JOIN human_labels hl ON hl.set_id = em.set_id AND hl.position = em.position
               WHERE em.set_id = ? ORDER BY em.position""",
            (set_id,),
        ).fetchall()

    def set_label(self, set_id: int, position: int, label: str, annotator: str) -> None:
        if label not in ("0", "1", "2", "3", "?"):
            raise ValueError("Label must be 0, 1, 2, 3 or ?")
        if not annotator.strip():
            raise ValueError("An annotator name is required")
        timestamp = now()
        with self.db:
            self.db.execute(
                """INSERT INTO human_labels VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(set_id, position) DO UPDATE SET
                   label = excluded.label, annotator = excluded.annotator,
                   updated_at = excluded.updated_at""",
                (set_id, position, label, annotator, timestamp),
            )
            self.db.execute(
                """INSERT INTO label_history
                   (set_id, position, label, annotator, created_at) VALUES (?, ?, ?, ?, ?)""",
                (set_id, position, label, annotator, timestamp),
            )

"""Versioned schema and short, explicit SQLite transactions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from media_kit._version import SCHEMA_VERSION
from media_kit.errors import StorageCompatibilityError

SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE assets (
 asset_id TEXT PRIMARY KEY, key TEXT NOT NULL, revision INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','deleting','deleted')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX live_asset_key ON assets(key) WHERE status != 'deleted';
CREATE TABLE revisions (
 asset_id TEXT NOT NULL REFERENCES assets(asset_id), revision INTEGER NOT NULL,
 path TEXT NOT NULL UNIQUE, info TEXT NOT NULL, reclaimed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(asset_id,revision)
);
CREATE TABLE variant_requests (
 asset_id TEXT NOT NULL, revision INTEGER NOT NULL, profile TEXT NOT NULL,
 fingerprint TEXT NOT NULL, recipe TEXT NOT NULL, limits TEXT NOT NULL,
 current INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(asset_id,revision,profile,fingerprint),
 FOREIGN KEY(asset_id,revision) REFERENCES revisions(asset_id,revision)
);
CREATE UNIQUE INDEX current_profile ON variant_requests(asset_id,revision,profile)
 WHERE current = 1;
CREATE TABLE variants (
 asset_id TEXT NOT NULL, revision INTEGER NOT NULL, profile TEXT NOT NULL,
 fingerprint TEXT NOT NULL, path TEXT NOT NULL UNIQUE, result TEXT NOT NULL,
 reclaimed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(asset_id,revision,profile,fingerprint),
 FOREIGN KEY(asset_id,revision,profile,fingerprint)
 REFERENCES variant_requests(asset_id,revision,profile,fingerprint)
);
CREATE TABLE tasks (
 task_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, revision INTEGER NOT NULL,
 profile TEXT NOT NULL, fingerprint TEXT NOT NULL, source_hash TEXT NOT NULL,
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 claim_token TEXT, lease_until REAL, error TEXT,
 UNIQUE(asset_id,revision,profile,fingerprint),
 FOREIGN KEY(asset_id,revision,profile,fingerprint)
 REFERENCES variant_requests(asset_id,revision,profile,fingerprint)
);
CREATE INDEX runnable_tasks ON tasks(status,lease_until);
CREATE INDEX asset_tasks ON tasks(asset_id,revision);
CREATE TABLE operations (
 operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, asset_id TEXT NOT NULL,
 phase TEXT NOT NULL, detail TEXT NOT NULL,
 cleanup TEXT NOT NULL DEFAULT 'pending', error TEXT
);
CREATE INDEX incomplete_operations ON operations(phase,cleanup);
CREATE TABLE idempotency_records (
 kind TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
 asset_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(kind,key)
);
"""


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    else:
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        connection.execute("PRAGMA synchronous=FULL")
    return connection


def initialize(path: Path, store_id: str) -> None:
    """Create version 1 only. Opening never calls this migration runner."""
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO metadata VALUES (?,?)", ("store_id", store_id))
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    finally:
        connection.close()


def verify(connection: sqlite3.Connection, store_id: str) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise StorageCompatibilityError(f"unsupported schema {version}; expected {SCHEMA_VERSION}")
    try:
        row = connection.execute("SELECT value FROM metadata WHERE key='store_id'").fetchone()
        if row is None or row[0] != store_id:
            raise StorageCompatibilityError("database and media store identities differ")
    except sqlite3.DatabaseError as error:
        raise StorageCompatibilityError(f"invalid store schema: {error}") from error


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

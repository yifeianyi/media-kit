"""Leased task claims and publication guarded by current asset/request identity."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from media_kit.errors import StorageError
from media_kit.models import Profile, TransformConfig, TransformRecipe
from media_kit.persistence import database
from media_kit.storage.files import atomic_move, remove_tree, store_lock
from media_kit.storage.paths import safe_join, variant_path
from media_kit.transforms.converter import content_hash, convert_thumbnail

if TYPE_CHECKING:
    from media_kit.api import MediaStore

LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 30


def claim(
    store: MediaStore, asset_id: str | None = None, *, now: float | None = None
) -> sqlite3.Row | None:
    store._writable()
    instant = time.time() if now is None else now
    with database.transaction(store.database_path) as connection:
        args: tuple[object, ...] = (instant,)
        filter_asset = ""
        if asset_id is not None:
            filter_asset = " AND t.asset_id=?"
            args += (asset_id,)
        row = connection.execute(
            """SELECT t.*,r.recipe,r.limits,v.path AS source_path,v.info
          FROM tasks t JOIN assets a ON a.asset_id=t.asset_id
          JOIN variant_requests r USING(asset_id,revision,profile,fingerprint)
          JOIN revisions v ON v.asset_id=t.asset_id AND v.revision=t.revision
          WHERE a.status='active' AND a.revision=t.revision AND r.current=1
          AND NOT EXISTS (SELECT 1 FROM operations o WHERE o.asset_id=t.asset_id AND o.phase='prepared')
          AND (t.status='pending' OR (t.status='running' AND t.lease_until<=?))"""
            + filter_asset
            + " ORDER BY t.rowid LIMIT 1",
            args,
        ).fetchone()
        if row is None:
            return None
        token = str(uuid4())
        connection.execute(
            "UPDATE tasks SET status='running',claim_token=?,lease_until=?,attempts=attempts+1,error=NULL WHERE task_id=?",
            (token, instant + LEASE_SECONDS, row["task_id"]),
        )
        # Load from the same transaction so the returned token belongs to this claim.
        claimed = connection.execute(
            """SELECT t.*,r.recipe,r.limits,v.path AS source_path,v.info
          FROM tasks t JOIN variant_requests r USING(asset_id,revision,profile,fingerprint)
          JOIN revisions v ON v.asset_id=t.asset_id AND v.revision=t.revision
          WHERE t.task_id=?""",
            (row["task_id"],),
        ).fetchone()
        return cast(sqlite3.Row, claimed)


def heartbeat(store: MediaStore, task_id: str, token: str, *, now: float | None = None) -> bool:
    instant = time.time() if now is None else now
    with database.transaction(store.database_path) as connection:
        result = connection.execute(
            "UPDATE tasks SET lease_until=? WHERE task_id=? AND status='running' AND claim_token=? AND lease_until>?",
            (instant + LEASE_SECONDS, task_id, token, instant),
        )
        return result.rowcount == 1


def _current(connection: sqlite3.Connection, task: sqlite3.Row) -> bool:
    row = connection.execute(
        """SELECT t.task_id FROM tasks t
      JOIN assets a ON a.asset_id=t.asset_id
      JOIN revisions v ON v.asset_id=t.asset_id AND v.revision=t.revision
      JOIN variant_requests r USING(asset_id,revision,profile,fingerprint)
      WHERE t.task_id=? AND t.status='running' AND t.claim_token=? AND t.lease_until>?
      AND a.status='active' AND a.revision=t.revision AND r.current=1
      AND json_extract(v.info,'$.content_hash')=t.source_hash""",
        (task["task_id"], task["claim_token"], time.time()),
    ).fetchone()
    return row is not None


def _check_destination(
    store: MediaStore, connection: sqlite3.Connection, destination: Path
) -> None:
    if destination.exists():
        registered = connection.execute(
            "SELECT 1 FROM variants WHERE path=? AND reclaimed=0",
            (destination.relative_to(store.root).as_posix(),),
        ).fetchone()
        if registered is None or not destination.is_file():
            raise StorageError("variant destination is occupied by unmanaged content")


def run_once(store: MediaStore, asset_id: str | None = None) -> int:
    task = claim(store, asset_id)
    if task is None:
        return 0
    token = task["claim_token"]
    work = store._private("staging", "worker-" + token)
    temporary = work / "output.webp"
    stopped = threading.Event()

    def renew() -> None:
        while not stopped.wait(HEARTBEAT_SECONDS):
            try:
                if not heartbeat(store, task["task_id"], token):
                    return
            except sqlite3.Error:
                # A transient busy/error cannot extend a lease; publication still rechecks it.
                return

    renewer = threading.Thread(target=renew, name="media-kit-heartbeat", daemon=True)
    renewer.start()
    try:
        source = safe_join(store.root, task["source_path"], must_exist=True)
        if content_hash(source) != task["source_hash"]:
            raise ValueError("source content hash differs from the committed revision")
        recipe = TransformRecipe(**json.loads(task["recipe"]))
        profile = Profile(
            task["profile"],
            recipe.size,
            recipe.format,
            recipe.quality,
            recipe.method,
            recipe.algorithm_version,
        )
        limits = json.loads(task["limits"])
        result = convert_thumbnail(
            source, temporary, profile, TransformConfig(profiles=(profile,), **limits)
        )
        store._fault("variant_converted")
        with store_lock(store.state_dir), database.transaction(store.database_path) as connection:
            if not _current(connection, task):
                connection.execute(
                    "UPDATE tasks SET status='pending',error='claim expired before publication',claim_token=NULL,lease_until=NULL WHERE task_id=? AND claim_token=? AND status='running'",
                    (task["task_id"], token),
                )
                return 1
            destination = variant_path(
                store.root,
                store.config.variant_dir,
                task["asset_id"],
                task["revision"],
                task["fingerprint"],
                task["profile"],
            )
            _check_destination(store, connection, destination)
            operation_id = str(uuid4())
            detail = {
                "task_id": task["task_id"],
                "claim_token": token,
                "destination": destination.relative_to(store.root).as_posix(),
                "staging": temporary.relative_to(store.root).as_posix(),
            }
            connection.execute(
                "INSERT INTO operations(operation_id,kind,asset_id,phase,detail) VALUES (?,'variant',?,'prepared',?)",
                (operation_id, task["asset_id"], json.dumps(detail, sort_keys=True)),
            )
        # The durable operation intent exists before a complete file enters the
        # publicly served tree. Recovery quarantines an unregistered destination.
        store._fault("variant_intent_committed")
        with store_lock(store.state_dir), database.transaction(store.database_path) as connection:
            if not _current(connection, task):
                connection.execute(
                    "UPDATE operations SET phase='rolled_back',error='task expired before publication' WHERE operation_id=?",
                    (operation_id,),
                )
                connection.execute(
                    "UPDATE tasks SET status='pending',claim_token=NULL,lease_until=NULL,error='claim expired before publication' WHERE task_id=? AND claim_token=? AND status='running'",
                    (task["task_id"], token),
                )
                return 1
            try:
                _check_destination(store, connection, destination)
            except StorageError as error:
                connection.execute(
                    "UPDATE operations SET phase='rolled_back',error=? WHERE operation_id=?",
                    (str(error), operation_id),
                )
                connection.execute(
                    "UPDATE tasks SET status='failed',error=?,claim_token=NULL,lease_until=NULL WHERE task_id=? AND claim_token=?",
                    (str(error), task["task_id"], token),
                )
                return 1
            atomic_move(temporary, destination)
            store._fault("variant_installed")
            metadata = asdict(result)
            del metadata["path"]
            connection.execute(
                "INSERT INTO variants(asset_id,revision,profile,fingerprint,path,result) VALUES (?,?,?,?,?,?) ON CONFLICT(asset_id,revision,profile,fingerprint) DO UPDATE SET path=excluded.path,result=excluded.result,reclaimed=0",
                (
                    task["asset_id"],
                    task["revision"],
                    task["profile"],
                    task["fingerprint"],
                    destination.relative_to(store.root).as_posix(),
                    json.dumps(metadata, sort_keys=True),
                ),
            )
            connection.execute(
                "UPDATE tasks SET status='succeeded',error=NULL,claim_token=NULL,lease_until=NULL WHERE task_id=? AND claim_token=?",
                (task["task_id"], token),
            )
            connection.execute(
                "UPDATE operations SET phase='committed',cleanup='complete' WHERE operation_id=?",
                (operation_id,),
            )
    except Exception as error:  # noqa: BLE001 - each conversion failure must persist its cause.
        with database.transaction(store.database_path) as connection:
            connection.execute(
                "UPDATE tasks SET status='failed',error=?,claim_token=NULL,lease_until=NULL WHERE task_id=? AND status='running' AND claim_token=? AND lease_until>?",
                (f"{type(error).__name__}: {error}", task["task_id"], token, time.time()),
            )
    finally:
        stopped.set()
        renewer.join()
        remove_tree(work)
    return 1

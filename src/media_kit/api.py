"""Framework-independent public asset lifecycle and local managed store."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Self, cast
from uuid import uuid4

from media_kit._version import SCHEMA_VERSION, STORAGE_LAYOUT_VERSION
from media_kit.config import StoreConfig
from media_kit.errors import (
    AssetConflictError,
    AssetNotFoundError,
    ConfigurationError,
    IdempotencyConflictError,
    RevisionConflictError,
    StorageCompatibilityError,
    StorageError,
)
from media_kit.models import (
    AssetPage,
    AssetStatus,
    AssetView,
    CheckReport,
    DeleteResult,
    ImageInfo,
    ProcessResult,
    Profile,
    TaskStatus,
    TaskView,
    TransformRecipe,
    VariantStatus,
    VariantView,
)
from media_kit.persistence import database
from media_kit.storage.files import (
    atomic_move,
    copy_input,
    remove_tree,
    store_lock,
)
from media_kit.storage.paths import (
    original_path,
    public_url,
    safe_join,
    validate_filename,
    validate_key,
)
from media_kit.transforms.converter import content_hash, inspect_image
from media_kit.transforms.fingerprint import fingerprint, recipe_for


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MediaStore:
    """An explicitly initialized single-machine store. Use as a context manager."""

    def __init__(self, config: StoreConfig, store_id: str, *, readonly: bool = False) -> None:
        self.store_id = store_id
        self.root = config.normalized_root.resolve()
        self.state_dir = config.normalized_state_dir.resolve()
        self._layout = (config.original_dir, config.variant_dir)
        self._config = config
        self.database_path = self.state_dir / "media.sqlite3"
        self._readonly = readonly
        self._closed = False
        self._closing = False
        self._process_stop = threading.Event()
        self._process_drain = False
        self._process_lock = threading.Lock()
        self._process_event = threading.Event()
        self._process_thread: threading.Thread | None = None
        self._process_assets: set[str] = set()
        # Test-only failure injection; no behavior unless explicitly assigned by a test.
        self._fault: Callable[[str], None] = lambda point: None

    @property
    def config(self) -> StoreConfig:
        return self._config

    @config.setter
    def config(self, value: StoreConfig) -> None:
        if (
            value.normalized_root.resolve() != self.root
            or value.normalized_state_dir.resolve() != self.state_dir
            or (value.original_dir, value.variant_dir) != self._layout
        ):
            raise ConfigurationError(
                "store root, state_dir, and managed directory layout are fixed"
            )
        self._config = value

    @classmethod
    def init(cls, config: StoreConfig) -> MediaStore:
        root, state = config.normalized_root.resolve(), config.normalized_state_dir.resolve()
        cls._validate_roots(root, state)
        if (root / ".media-kit.json").exists():
            return cls.open(config)
        for directory in (root, state):
            if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
                raise StorageCompatibilityError(
                    f"initialization requires an empty directory: {directory}"
                )
        root.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        store_id = str(uuid4())
        identity = cls._identity(config, store_id)
        for relative in (
            config.original_dir,
            config.variant_dir,
            ".media-kit-private/staging",
            ".media-kit-private/quarantine",
        ):
            safe_join(root, relative).mkdir(parents=True, exist_ok=True)
        safe_join(root, ".media-kit-private", must_exist=True).chmod(0o700)
        state.chmod(0o700)
        cls._validate_device(root, config)
        (state / ".lock").touch(exist_ok=False, mode=0o600)
        database.initialize(state / "media.sqlite3", store_id)
        (state / "media.sqlite3").chmod(0o600)
        cls._write_json(state / "store.json", identity)
        cls._write_json(root / ".media-kit.json", identity)
        return cls(config, store_id)

    @classmethod
    def open(cls, config: StoreConfig) -> MediaStore:
        store = cls._load(config, readonly=False)
        try:
            store.recover()
        except BaseException:
            store.close()
            raise
        return store

    @classmethod
    def readonly(cls, config: StoreConfig) -> MediaStore:
        """Read without initialization, migration, recovery, or background work."""
        return cls._load(config, readonly=True)

    @classmethod
    def _load(cls, config: StoreConfig, *, readonly: bool) -> MediaStore:
        root, state = config.normalized_root.resolve(), config.normalized_state_dir.resolve()
        cls._validate_roots(root, state)
        try:
            for file in (
                root / ".media-kit.json",
                state / "store.json",
                state / "media.sqlite3",
                state / ".lock",
            ):
                if file.is_symlink() or not file.is_file():
                    raise StorageCompatibilityError(f"missing or unsafe store file: {file}")
            media = json.loads((root / ".media-kit.json").read_text())
            private = json.loads((state / "store.json").read_text())
            if media != private or media != cls._identity(config, media["store_id"]):
                raise StorageCompatibilityError("store identity, layout, or version mismatch")
            cls._validate_device(root, config)
            with closing(database.connect(state / "media.sqlite3", readonly=True)) as connection:
                database.verify(connection, media["store_id"])
        except (OSError, KeyError, ValueError, sqlite3.DatabaseError) as error:
            raise StorageCompatibilityError(f"cannot open managed store: {error}") from error
        return cls(config, media["store_id"], readonly=readonly)

    @staticmethod
    def _validate_roots(root: Path, state: Path) -> None:
        if root == state or root in state.parents or state in root.parents:
            raise ConfigurationError("resolved root and state_dir must not overlap")

    @staticmethod
    def _identity(config: StoreConfig, store_id: str) -> dict[str, Any]:
        return {
            "store_id": store_id,
            "layout_version": STORAGE_LAYOUT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "original_dir": config.original_dir,
            "variant_dir": config.variant_dir,
        }

    @staticmethod
    def _validate_device(root: Path, config: StoreConfig) -> None:
        device = root.stat().st_dev
        for part in (
            config.original_dir,
            config.variant_dir,
            ".media-kit-private",
            ".media-kit-private/staging",
            ".media-kit-private/quarantine",
        ):
            path = safe_join(root, part, must_exist=True)
            if not path.is_dir() or path.stat().st_dev != device:
                raise StorageCompatibilityError(
                    "managed directories must share one local filesystem"
                )

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("x") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        atomic_move(temporary, path)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise StorageError("store is closed")
        connection = database.connect(self.database_path, readonly=True)
        try:
            yield connection
        finally:
            connection.close()

    def _writable(self) -> None:
        if (
            self._closed
            or self._readonly
            or (self._closing and threading.current_thread() is not self._process_thread)
        ):
            raise StorageError("store is closed or readonly")

    def _private(self, *parts: str) -> Path:
        return safe_join(self.root, ".media-kit-private", *parts)

    def _move_registered_file(self, source: Path, target: Path, relative: str) -> None:
        """Move one registered regular file, recognizing an interrupted prior move."""
        source_exists = source.exists()
        target_exists = target.exists()
        if source_exists and target_exists:
            raise StorageError(
                f"registered media has conflicting source and quarantine files: {relative}"
            )
        if source_exists:
            if not source.is_file():
                raise StorageError(f"registered media path is not a regular file: {relative}")
            atomic_move(source, target)
            return
        if target_exists and target.is_file():
            return
        raise StorageError(f"registered media file is missing: {relative}")

    def _prune_empty_asset_directories(self, source: Path, public_root: Path) -> None:
        """Remove only empty directories left by managed files; preserve unknown content."""
        parent = source.parent
        while parent == public_root or public_root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            if parent == public_root:
                break
            parent = parent.parent

    @contextmanager
    def _mutation(self) -> Iterator[sqlite3.Connection]:
        self._writable()
        with store_lock(self.state_dir):
            self._recover_locked()
            with database.transaction(self.database_path) as connection:
                yield connection

    @staticmethod
    def _ensure_recovered(connection: sqlite3.Connection, asset_id: str) -> None:
        unfinished = connection.execute(
            "SELECT kind FROM operations WHERE asset_id=? AND phase='prepared' AND kind!='delete'",
            (asset_id,),
        ).fetchone()
        if unfinished is not None:
            raise StorageError(f"asset has a {unfinished['kind']} operation that needs recovery")

    def local_path(self, coordinate: str) -> Path:
        validate_key(coordinate)
        if not any(
            coordinate.startswith(prefix + "/")
            for prefix in (self.config.original_dir, self.config.variant_dir)
        ):
            raise StorageError("coordinate is not in a public managed directory")
        return safe_join(self.root, coordinate, must_exist=True)

    def save(
        self,
        source: str | Path | BinaryIO,
        *,
        key: str | None = None,
        idempotency_key: str | None = None,
    ) -> AssetView:
        if key is not None:
            validate_key(key)
        return self._install("save", source, key=key, idempotency_key=idempotency_key)

    def replace(
        self,
        asset_id: str,
        source: str | Path | BinaryIO,
        *,
        expected_revision: int,
        idempotency_key: str | None = None,
    ) -> AssetView:
        return self._install(
            "replace",
            source,
            asset_id=asset_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def _install(
        self,
        kind: str,
        source: str | Path | BinaryIO,
        *,
        key: str | None = None,
        asset_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> AssetView:
        self._writable()
        operation_id = str(uuid4())
        with store_lock(self.state_dir):
            staging = self._private("staging", operation_id)
            source_name = (
                source if isinstance(source, (str, Path)) else getattr(source, "name", None)
            )
            filename = (
                validate_filename(Path(source_name).name)
                if isinstance(source_name, (str, Path)) and source_name
                else "input"
            )
            staged = staging / filename
            prepared = False
            try:
                copy_input(source, staged, self.config.max_bytes)
                info = inspect_image(
                    staged, max_bytes=self.config.max_bytes, max_pixels=self.config.max_pixels
                )
                if filename == "input":
                    filename = "image." + {"JPEG": "jpg"}.get(info.format, info.format.lower())
                digest = self._digest(
                    {
                        "kind": kind,
                        "asset_id": asset_id,
                        "key": key,
                        "expected_revision": expected_revision,
                        "hash": info.content_hash,
                        "profiles": [asdict(p) for p in self.config.profiles],
                        "max_bytes": self.config.max_bytes,
                        "max_pixels": self.config.max_pixels,
                    }
                )
                # Report request conflicts before attempting unrelated interrupted maintenance.
                with self._read() as connection:
                    replay = self._replay(connection, kind, idempotency_key, digest)
                    if replay is not None:
                        return self._view(
                            connection, replay["asset_id"], operation_revision=replay["revision"]
                        )
                    if kind == "save" and key is not None:
                        occupied = connection.execute(
                            "SELECT 1 FROM assets WHERE key=? AND status!='deleted'", (key,)
                        ).fetchone()
                        if occupied:
                            raise AssetConflictError(f"logical key already exists: {key}")
                    elif kind == "replace":
                        assert asset_id is not None
                        self._expect(self._asset(connection, asset_id), expected_revision)
                self._recover_locked()
                with database.transaction(self.database_path) as connection:
                    replay = self._replay(connection, kind, idempotency_key, digest)
                    if replay is not None:
                        return self._view(
                            connection, replay["asset_id"], operation_revision=replay["revision"]
                        )
                    if kind == "save":
                        target_id = str(uuid4())
                        logical_key = key or target_id
                        if connection.execute(
                            "SELECT 1 FROM assets WHERE key=? AND status!='deleted'", (logical_key,)
                        ).fetchone():
                            raise AssetConflictError(f"logical key already exists: {logical_key}")
                        revision = 1
                    else:
                        assert asset_id is not None
                        asset = self._asset(connection, asset_id)
                        self._expect(asset, expected_revision)
                        self._ensure_recovered(connection, asset_id)
                        target_id, logical_key = asset_id, asset["key"]
                        revision = asset["revision"] + 1
                    destination = original_path(
                        self.root, self.config.original_dir, target_id, revision, filename
                    )
                    detail = {
                        "destination": destination.relative_to(self.root).as_posix(),
                        "staging": staged.relative_to(self.root).as_posix(),
                        "hash": info.content_hash,
                    }
                    connection.execute(
                        "INSERT INTO operations(operation_id,kind,asset_id,phase,detail) VALUES (?,?,?,?,?)",
                        (operation_id, kind, target_id, "prepared", _json(detail)),
                    )
                prepared = True
                self._fault("intent_committed")
                with database.transaction(self.database_path) as connection:
                    if destination.exists():
                        raise StorageError("immutable destination already exists")
                    atomic_move(staged, destination)
                    self._fault("original_installed")
                    now = _now()
                    if kind == "save":
                        connection.execute(
                            "INSERT INTO assets VALUES (?,?,?,?,?,?)",
                            (target_id, logical_key, revision, "active", now, now),
                        )
                    else:
                        self._expect(self._asset(connection, target_id), expected_revision)
                        connection.execute(
                            "UPDATE assets SET revision=?,updated_at=? WHERE asset_id=?",
                            (revision, now, target_id),
                        )
                        connection.execute(
                            "UPDATE tasks SET status='superseded',error='source revision replaced',claim_token=NULL WHERE asset_id=? AND status IN ('pending','running')",
                            (target_id,),
                        )
                    connection.execute(
                        "INSERT INTO revisions(asset_id,revision,path,info) VALUES (?,?,?,?)",
                        (target_id, revision, detail["destination"], _json(asdict(info))),
                    )
                    for profile in self.config.profiles:
                        self._request(connection, target_id, revision, profile, info.content_hash)
                    self._remember(connection, kind, idempotency_key, digest, target_id, revision)
                    connection.execute(
                        "UPDATE operations SET phase='committed',cleanup='complete' WHERE operation_id=?",
                        (operation_id,),
                    )
                    self._fault("before_asset_commit")
                self._fault("asset_committed")
                return self.get(target_id)
            except OSError as error:
                raise StorageError(f"original installation failed: {error}") from error
            finally:
                # Persisted intent owns recovery after any interrupted installation.
                if not prepared or not staged.exists():
                    remove_tree(staging)

    @staticmethod
    def _digest(value: Any) -> str:
        return hashlib.sha256(_json(value).encode()).hexdigest()

    @staticmethod
    def _asset(connection: sqlite3.Connection, asset_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None:
            raise AssetNotFoundError(asset_id)
        return cast(sqlite3.Row, row)

    @staticmethod
    def _expect(asset: sqlite3.Row, expected_revision: int | None) -> None:
        if asset["status"] != "active" or expected_revision != asset["revision"]:
            raise RevisionConflictError("asset is not active at the expected revision")

    @staticmethod
    def _replay(
        connection: sqlite3.Connection, kind: str, key: str | None, digest: str
    ) -> sqlite3.Row | None:
        if key is None:
            return None
        if not isinstance(key, str) or not key or len(key) > 512:
            raise IdempotencyConflictError("idempotency key must contain 1..512 characters")
        row = connection.execute(
            "SELECT * FROM idempotency_records WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        if row is not None and row["digest"] != digest:
            raise IdempotencyConflictError("idempotency key was used for a different request")
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _remember(
        connection: sqlite3.Connection,
        kind: str,
        key: str | None,
        digest: str,
        asset_id: str,
        revision: int,
    ) -> None:
        if key is not None:
            connection.execute(
                "INSERT INTO idempotency_records VALUES (?,?,?,?,?)",
                (kind, key, digest, asset_id, revision),
            )

    def _request(
        self,
        connection: sqlite3.Connection,
        asset_id: str,
        revision: int,
        profile: Profile,
        source_hash: str,
    ) -> None:
        recipe = recipe_for(profile)
        identity = fingerprint(recipe)
        connection.execute(
            "UPDATE variant_requests SET current=0 WHERE asset_id=? AND revision=? AND profile=?",
            (asset_id, revision, profile.name),
        )
        connection.execute(
            "UPDATE tasks SET status='superseded',error='profile recipe superseded',claim_token=NULL WHERE asset_id=? AND revision=? AND profile=? AND fingerprint!=? AND status IN ('pending','running')",
            (asset_id, revision, profile.name, identity),
        )
        limits = _json({"max_bytes": self.config.max_bytes, "max_pixels": self.config.max_pixels})
        connection.execute(
            "INSERT INTO variant_requests VALUES (?,?,?,?,?,?,1) ON CONFLICT(asset_id,revision,profile,fingerprint) DO UPDATE SET current=1",
            (asset_id, revision, profile.name, identity, _json(asdict(recipe)), limits),
        )
        connection.execute(
            "INSERT OR IGNORE INTO tasks(task_id,asset_id,revision,profile,fingerprint,source_hash,status) VALUES (?,?,?,?,?,?,'pending')",
            (str(uuid4()), asset_id, revision, profile.name, identity, source_hash),
        )
        self._repair_task(connection, asset_id, revision, profile.name, identity)

    def _repair_task(
        self,
        connection: sqlite3.Connection,
        asset_id: str,
        revision: int,
        profile: str,
        identity: str,
    ) -> None:
        args = (asset_id, revision, profile, identity)
        row = connection.execute(
            "SELECT * FROM variants WHERE asset_id=? AND revision=? AND profile=? AND fingerprint=?",
            args,
        ).fetchone()
        if row is not None:
            path = safe_join(self.root, row["path"])
            if row["reclaimed"] and path.exists():
                raise StorageError("reclaimed variant path is occupied by an unmanaged file")
            if (
                not row["reclaimed"]
                and path.is_file()
                and content_hash(path) == json.loads(row["result"])["content_hash"]
            ):
                connection.execute(
                    "UPDATE tasks SET status='succeeded',error=NULL,claim_token=NULL WHERE asset_id=? AND revision=? AND profile=? AND fingerprint=? AND status!='running'",
                    args,
                )
                return
            # Keep ownership of damaged/missing registered files until replacement
            # or quarantine succeeds, so deletion still accounts for every file.
        connection.execute(
            "UPDATE tasks SET status='pending',error=NULL,claim_token=NULL,lease_until=NULL WHERE asset_id=? AND revision=? AND profile=? AND fingerprint=? AND status!='running'",
            args,
        )

    def get(self, asset_id: str) -> AssetView:
        with self._read() as connection:
            return self._view(connection, asset_id)

    def resolve(self, asset_id: str, *, profiles: Sequence[str] | None = None) -> AssetView:
        with self._read() as connection:
            return self._view(connection, asset_id, profiles=profiles)

    def resolve_many(
        self, asset_ids: Sequence[str], *, profiles: Sequence[str] | None = None
    ) -> tuple[AssetView | None, ...]:
        with self._read() as connection:
            connection.execute("BEGIN")
            result: list[AssetView | None] = []
            for asset_id in asset_ids:
                try:
                    result.append(self._view(connection, asset_id, profiles=profiles))
                except AssetNotFoundError:
                    result.append(None)
            return tuple(result)

    def list(self, *, prefix: str | None = None, page: int = 1, page_size: int = 30) -> AssetPage:
        if page < 1 or not 1 <= page_size <= 1000:
            raise ConfigurationError("page must be positive and page_size must be 1..1000")
        with self._read() as connection:
            connection.execute("BEGIN")
            where, args = (
                ("", ()) if prefix is None else ("WHERE substr(key,1,?)=?", (len(prefix), prefix))
            )
            total = connection.execute("SELECT count(*) FROM assets " + where, args).fetchone()[0]
            rows = connection.execute(
                "SELECT asset_id FROM assets "
                + where
                + " ORDER BY rowid,asset_id LIMIT ? OFFSET ?",
                (*args, page_size, (page - 1) * page_size),
            ).fetchall()
            return AssetPage(
                tuple(self._view(connection, row["asset_id"]) for row in rows),
                total,
                page,
                page_size,
            )

    def _view(
        self,
        connection: sqlite3.Connection,
        asset_id: str,
        *,
        profiles: Sequence[str] | None = None,
        operation_revision: int | None = None,
    ) -> AssetView:
        asset = self._asset(connection, asset_id)
        source = connection.execute(
            "SELECT * FROM revisions WHERE asset_id=? AND revision=?", (asset_id, asset["revision"])
        ).fetchone()
        rows = connection.execute(
            """SELECT r.*, t.status, t.error, v.path, v.result, v.reclaimed FROM variant_requests r
          JOIN tasks t USING(asset_id,revision,profile,fingerprint)
          LEFT JOIN variants v USING(asset_id,revision,profile,fingerprint)
          WHERE r.asset_id=? AND r.revision=? AND r.current=1 ORDER BY r.profile""",
            (asset_id, asset["revision"]),
        ).fetchall()
        if profiles is not None and set(profiles) - {row["profile"] for row in rows}:
            raise ConfigurationError("unknown or unrequested profile")
        active = asset["status"] == "active"
        views: list[VariantView] = []
        states: list[str] = []
        for row in rows:
            state = {"succeeded": "ready", "cancelled": "superseded"}.get(
                row["status"], row["status"]
            )
            result = json.loads(row["result"]) if row["result"] is not None else {}
            path = row["path"] if active and state == "ready" and not row["reclaimed"] else None
            error = row["error"]
            if path is not None:
                variant_file = safe_join(self.root, path)
                if not variant_file.is_file() or content_hash(variant_file) != result.get(
                    "content_hash"
                ):
                    path = None
                    state = "failed"
                    error = "managed variant is missing or has changed content"
            if profiles is None or row["profile"] in profiles:
                views.append(
                    VariantView(
                        row["profile"],
                        cast(VariantStatus, state),
                        row["fingerprint"],
                        path,
                        public_url(
                            self.config.urls.variant_base,
                            self._relative_public(path, self.config.variant_dir),
                        ),
                        result.get("width"),
                        result.get("height"),
                        result.get("file_size"),
                        result.get("content_hash"),
                        error,
                    )
                )
                states.append(state)
        if not states or all(state == "ready" for state in states):
            aggregate = "ready"
        elif "running" in states:
            aggregate = "processing"
        elif "pending" in states:
            aggregate = "pending"
        else:
            aggregate = "partial" if "ready" in states else "failed"
        original = source["path"] if active else None
        if original is not None:
            original_file = safe_join(self.root, original)
            if (
                not original_file.is_file()
                or content_hash(original_file) != json.loads(source["info"])["content_hash"]
            ):
                original = None
        return AssetView(
            asset_id,
            asset["key"],
            asset["revision"],
            cast(AssetStatus, asset["status"]),
            original,
            public_url(
                self.config.urls.original_base,
                self._relative_public(original, self.config.original_dir),
            ),
            ImageInfo(**json.loads(source["info"])),
            tuple(views),
            aggregate,
            asset["created_at"],
            asset["updated_at"],
            operation_revision,
        )

    @staticmethod
    def _relative_public(path: str | None, directory: str) -> str | None:
        return None if path is None else Path(path).relative_to(directory).as_posix()

    def tasks(self, asset_id: str | None = None) -> tuple[TaskView, ...]:
        with self._read() as connection:
            where, args = ("", ()) if asset_id is None else ("WHERE t.asset_id=?", (asset_id,))
            rows = connection.execute(
                "SELECT t.*,r.recipe FROM tasks t JOIN variant_requests r USING(asset_id,revision,profile,fingerprint) "
                + where
                + " ORDER BY t.rowid",
                args,
            ).fetchall()
            return tuple(
                TaskView(
                    row["task_id"],
                    row["asset_id"],
                    row["revision"],
                    row["profile"],
                    row["fingerprint"],
                    cast(TaskStatus, row["status"]),
                    row["attempts"],
                    row["error"],
                    TransformRecipe(**json.loads(row["recipe"])),
                    row["lease_until"],
                )
                for row in rows
            )

    def rebuild(self, asset_id: str, *, profiles: Sequence[str] | None = None) -> AssetView:
        self._writable()
        chosen = [
            profile
            for profile in self.config.profiles
            if profiles is None or profile.name in profiles
        ]
        if profiles is not None and set(profiles) - {profile.name for profile in chosen}:
            raise ConfigurationError("unknown configured profile")
        with self._mutation() as connection:
            asset = self._asset(connection, asset_id)
            if asset["status"] != "active":
                raise RevisionConflictError("cannot rebuild an inactive asset")
            self._ensure_recovered(connection, asset_id)
            source = connection.execute(
                "SELECT info FROM revisions WHERE asset_id=? AND revision=?",
                (asset_id, asset["revision"]),
            ).fetchone()
            for profile in chosen:
                self._request(
                    connection,
                    asset_id,
                    asset["revision"],
                    profile,
                    json.loads(source["info"])["content_hash"],
                )
        return self.get(asset_id)

    def retry(self, asset_id: str) -> AssetView:
        self._writable()
        with self._mutation() as connection:
            asset = self._asset(connection, asset_id)
            if asset["status"] != "active":
                raise RevisionConflictError("cannot retry an inactive asset")
            self._ensure_recovered(connection, asset_id)
            for row in connection.execute(
                "SELECT profile,fingerprint FROM variant_requests WHERE asset_id=? AND revision=? AND current=1",
                (asset_id, asset["revision"]),
            ).fetchall():
                self._repair_task(
                    connection, asset_id, asset["revision"], row["profile"], row["fingerprint"]
                )
        return self.get(asset_id)

    def delete(
        self, asset_id: str, *, expected_revision: int, idempotency_key: str | None = None
    ) -> DeleteResult:
        self._writable()
        digest = self._digest({"asset_id": asset_id, "expected_revision": expected_revision})
        with store_lock(self.state_dir):
            self._recover_locked()
            with database.transaction(self.database_path) as connection:
                replay = self._replay(connection, "delete", idempotency_key, digest)
                asset = self._asset(connection, asset_id)
                self._ensure_recovered(connection, asset_id)
                if replay is None and asset["status"] == "active":
                    self._expect(asset, expected_revision)
                    operation_id = str(uuid4())
                    connection.execute(
                        "UPDATE assets SET status='deleting',updated_at=? WHERE asset_id=?",
                        (_now(), asset_id),
                    )
                    connection.execute(
                        "UPDATE tasks SET status='cancelled',error='asset deletion committed',claim_token=NULL WHERE asset_id=? AND status IN ('pending','running')",
                        (asset_id,),
                    )
                    connection.execute(
                        "INSERT INTO operations(operation_id,kind,asset_id,phase,detail) VALUES (?,'delete',?,'prepared',?)",
                        (operation_id, asset_id, _json(self._delete_plan(connection, asset_id))),
                    )
                    self._remember(
                        connection, "delete", idempotency_key, digest, asset_id, asset["revision"]
                    )
            self._fault("delete_decided")
            return self._advance_delete(asset_id)

    def _advance_delete(self, asset_id: str) -> DeleteResult:
        with database.transaction(self.database_path) as connection:
            asset = self._asset(connection, asset_id)
            operation = connection.execute(
                "SELECT * FROM operations WHERE asset_id=? AND kind='delete' ORDER BY rowid DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            if operation is None:
                raise StorageError("deleting asset has no recovery intent")
            errors: list[str] = []
            removed = 0
            if asset["status"] == "deleting":
                detail = json.loads(operation["detail"])
                for label in ("originals", "variants"):
                    try:
                        for index, relative in enumerate(detail[label]):
                            source = safe_join(self.root, relative)
                            target = self._private(
                                "quarantine", operation["operation_id"], label, str(index)
                            )
                            already_quarantined = target.exists()
                            self._move_registered_file(source, target, relative)
                            if not already_quarantined:
                                removed += 1
                            base = (
                                self.config.original_dir
                                if label == "originals"
                                else self.config.variant_dir
                            )
                            self._prune_empty_asset_directories(
                                source, safe_join(self.root, base, asset_id)
                            )
                        self._fault("delete_" + label + "_moved")
                    except OSError as error:
                        errors.append(str(error))
                if not errors:
                    connection.execute(
                        "UPDATE assets SET status='deleted',updated_at=? WHERE asset_id=?",
                        (_now(), asset_id),
                    )
                    connection.execute(
                        "UPDATE operations SET phase='committed',error=NULL WHERE operation_id=?",
                        (operation["operation_id"],),
                    )
                else:
                    connection.execute(
                        "UPDATE operations SET error=? WHERE operation_id=?",
                        ("; ".join(errors), operation["operation_id"]),
                    )
            status: AssetStatus = "deleting" if errors else "deleted"
            return DeleteResult(
                asset_id, status, removed, operation["cleanup"] != "complete", tuple(errors)
            )

    @staticmethod
    def _delete_plan(connection: sqlite3.Connection, asset_id: str) -> dict[str, Sequence[str]]:
        originals = connection.execute(
            "SELECT path FROM revisions WHERE asset_id=? AND reclaimed=0 ORDER BY revision",
            (asset_id,),
        ).fetchall()
        variants = connection.execute(
            "SELECT path FROM variants WHERE asset_id=? AND reclaimed=0 ORDER BY revision,profile,fingerprint",
            (asset_id,),
        ).fetchall()
        return {
            "originals": [row["path"] for row in originals],
            "variants": [row["path"] for row in variants],
        }

    def recover(self) -> dict[str, int]:
        self._writable()
        with store_lock(self.state_dir):
            return self._recover_locked()

    def _recover_locked(self) -> dict[str, int]:
        recovered = failed = 0
        with self._read() as connection:
            operations = connection.execute(
                "SELECT * FROM operations WHERE phase='prepared' ORDER BY rowid"
            ).fetchall()
        for operation in operations:
            if operation["kind"] == "reclaim":
                if self._advance_reclaim(operation):
                    recovered += 1
                else:
                    failed += 1
            elif operation["kind"] == "delete":
                result = self._advance_delete(operation["asset_id"])
                if result.status != "deleted":
                    # Keep the asset unavailable and the operation retryable. Opening
                    # remains possible so operators can repair permissions or disk state.
                    failed += 1
                    continue
                recovered += 1
            elif operation["kind"] == "variant":
                detail = json.loads(operation["detail"])
                destination = safe_join(self.root, detail["destination"])
                try:
                    with database.transaction(self.database_path) as connection:
                        registered = connection.execute(
                            "SELECT 1 FROM variants WHERE path=?", (detail["destination"],)
                        ).fetchone()
                        orphan_target = self._private(
                            "quarantine", operation["operation_id"], "orphan"
                        )
                        if registered is None and (destination.exists() or orphan_target.exists()):
                            self._move_registered_file(
                                destination,
                                orphan_target,
                                detail["destination"],
                            )
                        temporary = safe_join(self.root, detail["staging"])
                        staging_target = self._private(
                            "quarantine", operation["operation_id"], "staging"
                        )
                        if temporary.exists() or staging_target.exists():
                            self._move_registered_file(
                                temporary,
                                staging_target,
                                detail["staging"],
                            )
                        work_dir = self._private("staging", "worker-" + detail["claim_token"])
                        if work_dir.exists():
                            remove_tree(work_dir)
                        connection.execute(
                            "UPDATE tasks SET status='pending',claim_token=NULL,lease_until=NULL,error=NULL WHERE task_id=? AND claim_token=? AND status='running'",
                            (detail["task_id"], detail["claim_token"]),
                        )
                        connection.execute(
                            "UPDATE operations SET phase='rolled_back',cleanup='pending',error='uncommitted variant isolated' WHERE operation_id=?",
                            (operation["operation_id"],),
                        )
                except OSError as error:
                    with database.transaction(self.database_path) as connection:
                        connection.execute(
                            "UPDATE operations SET error=? WHERE operation_id=?",
                            (str(error), operation["operation_id"]),
                        )
                    failed += 1
                    continue
                recovered += 1
                continue
            elif operation["kind"] in {"save", "replace"}:
                detail = json.loads(operation["detail"])
                try:
                    with database.transaction(self.database_path) as connection:
                        for label in ("destination", "staging"):
                            # A later committed revision owns its file even if an old intent names it.
                            registered = connection.execute(
                                "SELECT 1 FROM revisions WHERE path=?", (detail[label],)
                            ).fetchone()
                            if registered is not None:
                                continue
                            path = safe_join(self.root, detail[label])
                            target = self._private("quarantine", operation["operation_id"], label)
                            if path.exists() or target.exists():
                                self._move_registered_file(path, target, detail[label])
                                if label == "destination":
                                    public_root = safe_join(
                                        self.root, self.config.original_dir, operation["asset_id"]
                                    )
                                    self._prune_empty_asset_directories(path, public_root)
                        connection.execute(
                            "UPDATE operations SET phase='rolled_back',cleanup='pending',error='uncommitted installation isolated' WHERE operation_id=?",
                            (operation["operation_id"],),
                        )
                except OSError as error:
                    with database.transaction(self.database_path) as connection:
                        connection.execute(
                            "UPDATE operations SET error=? WHERE operation_id=?",
                            (str(error), operation["operation_id"]),
                        )
                    failed += 1
                    continue
                recovered += 1
                continue
        return {"recovered": recovered, **({"failed": failed} if failed else {})}

    def _plan_reclaims(self) -> None:
        with database.transaction(self.database_path) as connection:
            pending: set[tuple[str, int, str | None, str | None]] = set()
            for operation in connection.execute(
                "SELECT asset_id,detail FROM operations WHERE kind='reclaim' AND phase='prepared'"
            ).fetchall():
                detail = json.loads(operation["detail"])
                pending.add(
                    (
                        operation["asset_id"],
                        detail["revision"],
                        detail.get("profile"),
                        detail.get("fingerprint"),
                    )
                )
            old = connection.execute(
                "SELECT r.asset_id,r.revision,r.path FROM revisions r JOIN assets a USING(asset_id) WHERE a.status='active' AND r.revision!=a.revision AND r.reclaimed=0"
            ).fetchall()
            for row in old:
                if any(
                    item[0] == row["asset_id"] and item[1] == row["revision"] for item in pending
                ):
                    continue
                variants = connection.execute(
                    "SELECT path FROM variants WHERE asset_id=? AND revision=? AND reclaimed=0 ORDER BY profile,fingerprint",
                    (row["asset_id"], row["revision"]),
                ).fetchall()
                detail = {
                    "revision": row["revision"],
                    "paths": [row["path"], *(variant["path"] for variant in variants)],
                }
                connection.execute(
                    "INSERT INTO operations(operation_id,kind,asset_id,phase,detail) VALUES (?,'reclaim',?,'prepared',?)",
                    (str(uuid4()), row["asset_id"], _json(detail)),
                )
            old_variants = connection.execute("""SELECT r.*,v.path,v.reclaimed FROM variant_requests r
                JOIN assets a USING(asset_id)
                JOIN variants v USING(asset_id,revision,profile,fingerprint)
                WHERE a.status='active' AND a.revision=r.revision AND r.current=0""").fetchall()
            for row in old_variants:
                if row["reclaimed"] != 0:
                    continue
                identity = (row["asset_id"], row["revision"], row["profile"], row["fingerprint"])
                revision_identity = (row["asset_id"], row["revision"], None, None)
                if identity in pending or revision_identity in pending:
                    continue
                detail = {
                    "revision": row["revision"],
                    "profile": row["profile"],
                    "fingerprint": row["fingerprint"],
                    "paths": [row["path"]],
                }
                connection.execute(
                    "INSERT INTO operations(operation_id,kind,asset_id,phase,detail) VALUES (?,'reclaim',?,'prepared',?)",
                    (str(uuid4()), row["asset_id"], _json(detail)),
                )

    def _advance_reclaim(self, operation: sqlite3.Row) -> bool:
        detail = json.loads(operation["detail"])
        with database.transaction(self.database_path) as connection:
            try:
                for index, relative in enumerate(detail["paths"]):
                    source = safe_join(self.root, relative)
                    target = self._private("quarantine", operation["operation_id"], str(index))
                    self._move_registered_file(source, target, relative)
                    for public_dir in (self.config.original_dir, self.config.variant_dir):
                        asset_root = f"{public_dir}/{operation['asset_id']}"
                        if relative.startswith(asset_root + "/"):
                            self._prune_empty_asset_directories(
                                source, safe_join(self.root, asset_root)
                            )
                            break
                    self._fault("reclaim_moved")
            except OSError as error:
                connection.execute(
                    "UPDATE operations SET error=? WHERE operation_id=?",
                    (str(error), operation["operation_id"]),
                )
                return False
            args = (operation["asset_id"], detail["revision"])
            if "profile" in detail:
                connection.execute(
                    "UPDATE variants SET reclaimed=1 WHERE asset_id=? AND revision=? AND profile=? AND fingerprint=?",
                    (*args, detail["profile"], detail["fingerprint"]),
                )
            else:
                connection.execute(
                    "UPDATE revisions SET reclaimed=1 WHERE asset_id=? AND revision=?", args
                )
                connection.execute(
                    "UPDATE variants SET reclaimed=1 WHERE asset_id=? AND revision=?", args
                )
            connection.execute(
                "UPDATE operations SET phase='committed',error=NULL WHERE operation_id=?",
                (operation["operation_id"],),
            )
        return True

    def cleanup(self) -> dict[str, int]:
        self._writable()
        complete = failed = 0
        with store_lock(self.state_dir):
            self._recover_locked()
            self._plan_reclaims()
            self._recover_locked()
            with self._read() as connection:
                operations = connection.execute(
                    "SELECT * FROM operations WHERE phase!='prepared' AND cleanup!='complete'"
                ).fetchall()
                failed_reclaims = connection.execute(
                    "SELECT count(*) FROM operations WHERE kind='reclaim' AND phase='prepared' AND error IS NOT NULL"
                ).fetchone()[0]
            failed += failed_reclaims
            for operation in operations:
                try:
                    remove_tree(self._private("quarantine", operation["operation_id"]))
                    remove_tree(self._private("staging", operation["operation_id"]))
                    state, error = "complete", None
                    complete += 1
                except OSError as failure:
                    state, error = "failed", str(failure)
                    failed += 1
                with database.transaction(self.database_path) as connection:
                    connection.execute(
                        "UPDATE operations SET cleanup=?,error=? WHERE operation_id=?",
                        (state, error, operation["operation_id"]),
                    )
        return {"complete": complete, "failed": failed}

    def check(self) -> CheckReport:
        known: dict[str, str] = {}
        with self._read() as connection:
            count = connection.execute("SELECT count(*) FROM assets").fetchone()[0]
            for table, data in (("revisions", "info"), ("variants", "result")):
                for row in connection.execute(
                    f"SELECT r.path,r.{data} FROM {table} r JOIN assets a USING(asset_id) WHERE a.status!='deleted' AND r.reclaimed=0"
                ):
                    known[row["path"]] = json.loads(row[data])["content_hash"]
        missing, mismatches = [], []
        for relative, digest in known.items():
            path = safe_join(self.root, relative)
            if not path.is_file():
                missing.append(relative)
            elif content_hash(path) != digest:
                mismatches.append(relative)
        actual = set()
        for directory in (self.config.original_dir, self.config.variant_dir):
            base = safe_join(self.root, directory)
            for path in base.rglob("*"):
                safe_join(self.root, path.relative_to(self.root).as_posix())
                if path.is_file():
                    actual.add(path.relative_to(self.root).as_posix())
        return CheckReport(
            count,
            tuple(sorted(missing)),
            tuple(sorted(mismatches)),
            tuple(sorted(actual - known.keys())),
        )

    def worker_once(self, asset_id: str | None = None) -> int:
        self._writable()
        from media_kit.tasks.runtime import run_once

        return run_once(self, asset_id)

    def worker(
        self,
        *,
        once: bool = False,
        poll_interval: float = 1,
        stop_event: threading.Event | None = None,
    ) -> int:
        self._writable()
        stop = stop_event or threading.Event()
        count = 0
        while not stop.is_set():
            processed = self.worker_once()
            count += processed
            if not processed:
                if once:
                    return count
                stop.wait(poll_interval)
        return count

    def process(self, asset_id: str, *, timeout: float | None = None) -> ProcessResult:
        self._writable()
        if timeout is not None and timeout < 0:
            raise ConfigurationError("timeout must be nonnegative")
        initial = self.get(asset_id)
        if initial.status != "active":
            raise RevisionConflictError("cannot process an inactive asset")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._process_lock:
            self._process_assets.add(asset_id)
            self._process_event.set()
            if self._process_thread is None or not self._process_thread.is_alive():
                self._process_thread = threading.Thread(
                    target=self._drive_process, name="media-kit-process", daemon=True
                )
                self._process_thread.start()
        while True:
            asset = self.get(asset_id)
            if asset.status != "active" or asset.processing_status not in ("pending", "processing"):
                return ProcessResult(asset)
            if deadline is not None and time.monotonic() >= deadline:
                return ProcessResult(asset, timed_out=True)
            time.sleep(0.01)

    def _drive_process(self) -> None:
        while True:
            with self._process_lock:
                asset_id = next(iter(self._process_assets), None)
            if asset_id is None:
                if self._process_stop.is_set():
                    return
                self._process_event.wait(0.05)
                self._process_event.clear()
                continue
            try:
                processed = self.worker_once(asset_id)
                asset = self.get(asset_id)
                if (
                    not processed
                    and asset.processing_status in ("pending", "processing")
                    and asset.status == "active"
                ):
                    self._process_event.wait(0.05)
                    self._process_event.clear()
                    continue
            except (OSError, sqlite3.Error):
                with self._process_lock:
                    self._process_assets.discard(asset_id)
                return
            with self._process_lock:
                if (
                    not processed
                    or asset.processing_status not in ("pending", "processing")
                    or asset.status != "active"
                ):
                    self._process_assets.discard(asset_id)
                should_stop = self._process_stop.is_set() and not self._process_drain
            if should_stop:
                return

    def close(self, *, wait: bool = True) -> None:
        """Stop accepting work; wait=True drains local work, wait=False leaves leases recoverable."""
        self._closing = True
        self._process_drain = wait
        self._process_stop.set()
        self._process_event.set()
        thread = self._process_thread
        if wait and thread is not None:
            thread.join()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

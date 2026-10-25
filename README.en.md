<div align="center">

# media-kit

**Framework-independent local image hosting and thumbnail processing for Python**

Originals preserved · Immutable revisions · Durable tasks · Crash recovery

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Pillow](https://img.shields.io/badge/Pillow-11%E2%80%9313-yellow)](https://python-pillow.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Typed](https://img.shields.io/badge/Typing-py.typed-blue)](https://peps.python.org/pep-0561/)
[![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey)](#requirements)

[简体中文](README.md) · [English](README.en.md)

</div>

`media-kit` turns the media-side logic of "store an image, derive several thumbnails, and still be able to replace, delete and recover it later" into a standalone Python package. It uses only the local filesystem and the standard-library SQLite, depends on no web framework, and does not take over your application's business tables or authorization.

Originals are preserved byte for byte. Thumbnails are independent derived results that may be produced asynchronously. Synchronous processing and a standalone worker share the same durable task queue and leases. Importing the package has no file, database, or background-process side effects, and read-only commands such as `status` / `check` never write to storage.

> Version `0.1.0`, Alpha. The release version, on-disk layout version, SQLite schema version, and transform algorithm version evolve independently.

## Features

| Capability | Description |
| --- | --- |
| Immutable originals | A successful `save` fully installs and commits the original; `replace` creates an immutable new revision and never overwrites historical files in place. |
| Stable asset identity | `asset_id` is a stable, non-reused UUID; the logical `key` only identifies business paths and never determines physical filenames. |
| Multiple thumbnail profiles | WebP variants at longest edge 320/640/1280 by default (quality 80, method 4); named profiles are supported, with EXIF orientation, alpha preservation, first-frame animation, and no upscaling. |
| Identifiable derived identity | Output-affecting settings and the algorithm version are part of the recipe fingerprint, which appears in result paths; changing output behavior requires a new algorithm version, so old identities never masquerade as new results. |
| Durable tasks and leases | Synchronous `process()` and the standalone worker share one queue; claims carry a claim token, lease, and heartbeat. After a crash, another worker takes over, and stale tasks can never publish. |
| Idempotent retries | `save` / `replace` / `delete` accept idempotency keys and request digests, so network retries can be replayed safely without mistaking an old success for a new change. |
| Safe boundaries | `root`, `state_dir`, public original/variant directories, and private staging/quarantine stay separated; symlink escapes are rejected, and neither the whole root nor the state directory is exposed. |
| Recoverable deletion | Deletion advances through `active → deleting → deleted`; `deleted` is reported only after every managed historical version has left the public directory, with a separate state for private quarantine cleanup. |
| Read-only verification | `check()` reports missing files, content-hash mismatches, and unknown files; `recover()` only advances operations already recorded by the package and never adopts unknown files. |
| Distribution friendly | Wheels and sdists ship offline documentation, runnable examples, the `py.typed` marker, the CLI, and license/provenance notices; after installation, `media-kit docs --path` locates the manuals. |

## When to use it

- A single Python project hosts its own media directory on a local POSIX/Linux filesystem.
- You need stable asset identity, immutable revisions, and recoverable replace/delete semantics.
- You want thumbnails generated in the background and resumed after a restart, or you want to start synchronously and split out a worker later.
- You do not want to introduce another service, cloud SDK, or business database just for media.

The first release **does not** cover adopting existing media directories, video transcoding, object-storage/CDN integration, multi-host shared storage, Windows support, an admin UI, or business authorization and reference checks (those stay with the host application). See [Integration](docs/integration.md) and [Upgrading](docs/upgrading.md) for the exact boundaries.

## Requirements

- Python 3.11+ (CI covers 3.11 and 3.15)
- Pillow 11–13
- A local POSIX/Linux filesystem that supports SQLite, `fcntl` file locks, and atomic renames

## Installation

The project is not published on PyPI yet; install it from source or from a locally built artifact:

```bash
python -m pip install .

# or build a wheel first, then install it
python -m pip install build
python -m build --wheel
python -m pip install dist/media_kit-0.1.0-py3-none-any.whl
```

Verify the CLI after installation:

```bash
media-kit --help
media-kit docs --path
```

## Quick start

The snippet below creates a temporary directory and a test image, then walks through init → save → thumbnail → URL lookup without touching any existing media directory:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_kit import MediaStore, StoreConfig, URLMapping

with TemporaryDirectory() as temporary:
    base = Path(temporary)
    source = base / "cover.png"
    Image.new("RGB", (800, 600), "steelblue").save(source)

    config = StoreConfig(
        root=base / "media",
        state_dir=base / "state",
        urls=URLMapping("/media/src/", "/media/thu/"),
    )
    with MediaStore.init(config) as store:
        asset = store.save(source, key="articles/42/cover.png")
        result = store.process(asset.asset_id, timeout=10)

        print(result.asset.processing_status)  # ready
        print(result.asset.original_url)       # /media/src/<asset_id>/1/cover.png
        for variant in result.asset.variants:
            print(variant.profile, variant.width, variant.height, variant.url)
```

After a successful `save` the original is fully installed and the asset plus task are committed, while thumbnails may still be pending. A `process` timeout only ends the caller's wait: the original and task remain, and the local execution context or a later worker continues. Whether the original is available is expressed separately from whether thumbnails are ready, so do not treat "thumbnails succeeded" as the only signal that the upload was committed.

### Production configuration

```toml
# media.toml
[storage]
root = "/srv/example/media"
state_dir = "/var/lib/example/media"
original_dir = "src"
variant_dir = "thu"

[thumbnails]
sizes = [320, 640, 1280]
format = "webp"
quality = 80
method = 4
max_pixels = 40000000
max_bytes = 20971520
algorithm_version = "webp-v1"

[urls]
original_base = "/media/src/"
variant_base = "/media/thu/"
```

```bash
media-kit --config media.toml init       # explicitly initialize a brand-new empty directory
media-kit --config media.toml status     # read-only inspection
media-kit --config media.toml worker --once
```

`root` and `state_dir` must be absolute, distinct, and non-overlapping. Production deployments should use a TOML configuration and a standalone worker; see [Configuration](docs/configuration.md) and [Operations](docs/operations.md).

## Directory layout

```text
<root>/
  .media-kit.json
  src/<asset_id>/<revision>/<filename>
  thu/<asset_id>/<revision>/<recipe_fingerprint>/<profile>.webp
  .media-kit-private/
    staging/<operation_id>/
    quarantine/<operation_id>/
<state_dir>/
  store.json
  media.sqlite3
```

A static file server should map only the original and variant directories, never the whole `root`, the `state_dir`, or `.media-kit-private`. The package does not promise to purge existing browser or CDN caches; cache invalidation stays with the host application.

## Core concepts

| Concept | Meaning |
| --- | --- |
| `asset_id` | Stable, non-reused asset UUID and the only identifier for lookups and business association. |
| `revision` | Immutable original version starting at 1; replacement increments it even when the bytes are identical. |
| `key` | Optional business logical path used only for conflict checks and list filtering, never as a physical path. |
| `profile` | Named thumbnail recipe: longest edge, format, quality, method, and algorithm version. |
| Recipe fingerprint | SHA-256 over output-affecting settings and `algorithm_version`, and part of the result path. |
| State dimensions | Asset lifecycle `active/deleting/deleted` and processing status `pending/processing/ready/partial/failed` are independent. |
| Tasks and leases | Durable tasks record revision, profile, recipe snapshot, attempts, and errors; workers claim under a lease and re-check the token, asset state, and current request before publishing. |
| Version identities | `VERSION`, `STORAGE_LAYOUT_VERSION`, `SCHEMA_VERSION`, and `ALGORITHM_VERSION` are independent and evaluated separately during upgrades. |

## CLI

| Command | Purpose |
| --- | --- |
| `media-kit --config <toml> init` | Explicitly initialize a brand-new media directory |
| `media-kit --config <toml> status [asset_id]` | Read-only inspection of assets and durable tasks, with `--prefix` filtering and `--page` pagination |
| `media-kit --config <toml> check` | Read-only verification of registered files, hashes, and unknown files |
| `media-kit --config <toml> worker --once` | Drain currently claimable tasks and exit; supports `--asset-id` |
| `media-kit --config <toml> worker --poll-interval 1` | Continuous worker mode |
| `media-kit --config <toml> retry <asset_id>` | Retry failed or missing profiles from the current request snapshot |
| `media-kit --config <toml> rebuild <asset_id> --profile <name>` | Explicitly request new recipes from the current configuration; `--profile` is repeatable |
| `media-kit --config <toml> recover` | Explicitly advance interrupted operations recorded by the package |
| `media-kit --config <toml> cleanup` | Reclaim historical revisions and old recipe files, and clean the private quarantine |
| `media-kit docs --path` | Locate the offline documentation installed with the package |

On success the CLI prints JSON and exits 0; expected errors print JSON with `error.type` / `error.message` to stderr and exit 2; interruptions exit 130. `--config` must appear before the subcommand.

## Documentation

The complete offline documentation ships with the wheel and sdist and needs neither network access nor the source checkout.

| Topic | Entry point |
| --- | --- |
| Documentation index | [docs/index.md](docs/index.md) |
| First integration and a full example | [Quickstart](docs/quickstart.md) |
| Python/TOML, layout, and URLs | [Configuration](docs/configuration.md) |
| Methods, return values, errors, and retries | [Python API](docs/API.md) |
| Versions, file states, and deletion | [Storage and lifecycle](docs/storage-and-lifecycle.md) |
| Business database, authorization, static serving | [Integration](docs/integration.md) |
| Workers, verification, recovery, backups | [Operations](docs/operations.md) |
| Upgrades and disk/schema compatibility | [Upgrading](docs/upgrading.md) |
| Modifying source, tests, and builds | [Development](docs/development.md) |
| Custom profiles, algorithms, and forks | [Customization](docs/customization.md) |
| Navigation for integrators and maintenance agents | [Agent guide](docs/agent-guide.md) |

The installed examples can be run directly:

```bash
python -c "from media_kit.documentation import examples_path; print(examples_path())"
# replace <examples> with the printed directory
python <examples>/lifecycle.py
python <examples>/convert.py
python <examples>/static_server.py --help
```

## Development

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests examples tools
.venv/bin/python -m mypy
.venv/bin/python -m build

# verify a wheel and an sdist-rebuilt install outside the source tree
.venv/bin/python tools/verify_distribution.py --python python3.11
```

Source layout: `src/media_kit/` (API, configuration, storage, persistence, tasks, transforms), `tests/`, `examples/`, `docs/` (the single authoritative source of documentation), and `tools/` (distribution and customization acceptance). Before changing source, read [AGENTS.md](AGENTS.md) and [Development](docs/development.md); tests must use isolated temporary `root`/`state_dir` directories and never touch real media.

## Versioning and compatibility

- `VERSION`: release version, currently `0.1.0`.
- `STORAGE_LAYOUT_VERSION` / `SCHEMA_VERSION`: on-disk layout and SQLite schema versions, both `1` today, fixed after initialization.
- `ALGORITHM_VERSION`: default transform algorithm `webp-v1`; changing output behavior requires a new version.

`open` / `readonly` validate the instance marker and database version. Newer, incompatible storage raises `StorageCompatibilityError` and stops writes. Ordinary opens never create tables or migrate the schema implicitly; the first release starts from fresh instances and provides no automatic migration command. See [Operations](docs/operations.md) and [Upgrading](docs/upgrading.md) for upgrade, backup, and restore procedures.

## Contributing

Issues, discussions, and pull requests are welcome. Before submitting:

1. Read [AGENTS.md](AGENTS.md) and [Development](docs/development.md), and preserve the existing public API contracts.
2. Add contract tests for behavior changes; when transform behavior changes, update `algorithm_version` and its tests as well.
3. Keep `pytest`, `ruff`, and `mypy` green, and run the distribution acceptance script when relevant.
4. Maintain documentation only in `docs/`; verify ownership and licensing before adding or copying third-party material, and update `NOTICE.md`.

## License and provenance

The project's own code is released under the [MIT License](LICENSE). Third-party dependencies keep their own licenses. Reference material was used only for behavioral research and no source code was copied; see [NOTICE.md](NOTICE.md).

Distribution name `media-kit`, import name `media_kit`, and command `media-kit` are local defaults; verify name availability before publishing to a package registry. For forking and repackaging, see [Customization](docs/customization.md).

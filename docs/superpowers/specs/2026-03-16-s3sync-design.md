# s3sync — Design Spec
_Date: 2026-03-16_

## Overview

`s3sync` is a macOS CLI tool + background daemon that watches one or more local folders and continuously syncs file changes to AWS S3. It maintains a local SQLite state database to track what has been synced, supports client-side encryption via `age`, per-folder include/exclude filters, and is managed as a native launchd service.

---

## Goals

- Watch multiple local folders; each maps to an independent S3 bucket + prefix
- Maintain a local SQLite state DB to efficiently detect which files need syncing (no S3 listing on every start)
- On daemon start: walk local files, compare to DB state, upload only changed/new files
- On file change: upload changed/new files to S3 with a 500ms per-file debounce
- On file delete: optionally delete from S3 (configurable per watch entry)
- Optionally encrypt files with `age` before upload (client-side, per watch entry)
- Include/exclude glob patterns per watch entry
- Managed as a launchd agent (auto-start on login, auto-restart on crash)
- CLI for all lifecycle operations

## Non-Goals

- Bidirectional sync (S3 → local)
- GUI / menu bar app
- Cross-platform (macOS only for now)
- Custom S3-compatible endpoints

---

## Tech Stack

| Concern | Library |
|---|---|
| CLI | `typer` |
| File watching | `watchdog` (uses macOS FSEvents) |
| S3 | `boto3` |
| State database | `sqlite3` (stdlib) |
| Config parsing | `tomllib` (stdlib, Python 3.11+) |
| Config validation | `pydantic` v2 |
| Encryption | `pyrage` (age format, Rust bindings) |
| Daemon management | launchd plist + `launchctl` |
| Packaging | `pyproject.toml` + `pip install -e .` |

---

## Config File

Location: `~/.config/s3sync/config.toml`

```toml
[aws]
profile = "default"   # AWS profile from ~/.aws/credentials
region  = "us-east-1"

[[watch]]
path             = "/Users/you/Documents/reports"
bucket           = "my-reports-bucket"
prefix           = "reports/"
delete_on_remove = false
include          = []                          # empty = sync all files
exclude          = ["*.tmp", ".DS_Store"]

[[watch]]
path              = "/Users/you/sensitive-docs"
bucket            = "my-encrypted-bucket"
prefix            = "docs/"
delete_on_remove  = false
include           = ["*.pdf", "*.docx"]
exclude           = [".DS_Store"]
encrypt           = true
age_recipients    = [
  "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p",
  "age1another...recipient..."
]
age_identity_file = "~/.config/s3sync/age-key.txt"   # private key for `s3sync decrypt`
```

### Config Rules
- `include` / `exclude`: patterns use Python `fnmatch` syntax applied to the **filename only** (e.g., `*.pdf`, `*.tmp`). `exclude` always wins over `include`. Empty `include` means sync all.
- `encrypt`: defaults to `false`; requires at least one `age_recipients` entry when `true`
- `age_recipients`: multiple public keys supported — pyrage encrypts once for all recipients; any corresponding private key can decrypt
- `age_identity_file`: required when `encrypt = true`; used for `s3sync decrypt`; path is expanded (`~` → home dir)
- Startup fails with a clear error if `encrypt = true` and `age_recipients` is missing/empty

---

## SQLite State Database

Location: `~/.config/s3sync/state.db`

### Schema

```sql
CREATE TABLE IF NOT EXISTS synced_files (
    path       TEXT    PRIMARY KEY,   -- absolute local path
    watch_root TEXT    NOT NULL,      -- the [[watch]] path this file belongs to
    mtime      REAL    NOT NULL,      -- local file mtime at time of last successful sync
    size       INTEGER NOT NULL,      -- local file size at time of last successful sync
    s3_key     TEXT    NOT NULL,      -- S3 key the file was uploaded as
    synced_at  REAL    NOT NULL,      -- unix timestamp of last successful sync
    encrypted  INTEGER NOT NULL DEFAULT 0  -- 1 if uploaded with age encryption
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
```

The DB is the **source of truth** for sync state. S3 is not queried on startup.

### State Transitions

```
File uploaded successfully → INSERT OR REPLACE INTO synced_files (path, mtime, size, ...)
File deleted from S3       → DELETE FROM synced_files WHERE path = ?
File upload fails          → DB NOT updated (will retry on next event or startup)
```

### First-Run Behavior

On first run (empty DB), all local files are treated as unsynced and uploaded. If those files already exist on S3 with identical content, boto3 will overwrite them (idempotent; only costs bandwidth once). A future `s3sync bootstrap` command could import existing S3 state into the DB without re-uploading.

---

## S3 Key Mapping

```
local root:  /Users/you/reports/
prefix:      reports/
local file:  /Users/you/reports/2024/q1.pdf
→ S3 key:    reports/2024/q1.pdf

# With encryption:
local file:  /Users/you/sensitive-docs/contract.pdf
prefix:      docs/
→ S3 key:    docs/contract.pdf.age
```

---

## CLI Commands

```
s3sync install           Write launchd plist; enable auto-start on login
s3sync uninstall         Remove plist; stop daemon

s3sync start             launchctl load (start daemon now)
s3sync stop              launchctl unload (stop daemon)
s3sync restart           stop + start

s3sync status            Show daemon state + per-watch-entry stats from the DB
s3sync sync              One-shot manual sync of all watch entries (no daemon)
s3sync logs              Tail the daemon log file (~/Library/Logs/s3sync/s3sync.log)
s3sync config            Open config file in $EDITOR

s3sync decrypt <path>    Decrypt a local .age file using age_identity_file
```

`s3sync status` queries the SQLite DB directly — no S3 calls needed. Shows: file count, total size synced, last sync time, and any files pending (local file mtime/size differs from DB).

---

## Daemon Architecture

### Singleton Enforcement
On startup, the daemon acquires a lockfile at `~/.config/s3sync/daemon.lock` (using `fcntl.flock`).
If another instance holds the lock, the new process exits immediately with an error message.

### Startup Sequence
1. Acquire daemon lockfile
2. Load + validate config (Pydantic); exit on validation errors
3. Open/migrate SQLite DB (run `CREATE TABLE IF NOT EXISTS` migrations)
4. Clean up stale temp files in `~/.config/s3sync/tmp/` older than 1 hour
5. Validate each `[[watch]]` entry: warn and skip entries where `path` does not exist
6. Validate AWS credentials (`sts.get_caller_identity()`); exit on failure
7. For each valid `[[watch]]` entry: run `InitialSync`
8. Register a `watchdog` observer on each valid watched path (recursive=True)
9. Enter event loop (blocks until SIGTERM/SIGINT)
10. On shutdown: flush pending debounced events, release lockfile

### InitialSync Algorithm (DB-backed)

```
For each valid [[watch]] entry:
  1. Walk local path recursively
  2. For each local file (after include/exclude filtering):
     a. stat(file) → current_mtime, current_size
     b. db_row = SELECT mtime, size FROM synced_files WHERE path = ?
     c. If db_row is None → file never synced → upload
     d. If current_mtime != db_row.mtime OR current_size != db_row.size → file changed → upload
     e. Otherwise → skip (DB says it's already synced)
  3. After each successful upload → INSERT OR REPLACE INTO synced_files ...

S3 is NOT listed or queried during InitialSync.
S3 orphans (objects on S3 that no longer exist locally) are NOT touched during InitialSync.
Orphan cleanup is out of scope for v1; a future `s3sync prune` command will handle it.
```

### Event Handling (watchdog events)

Debounce is **per-file**: each file path has its own 500ms timer that resets on each new event.
After 500ms of inactivity for that path, the upload is triggered.

```
File created/modified
  → apply include/exclude filters (skip if excluded)
  → if path is now a directory (file→dir transition): log warning; if delete_on_remove, delete S3 key
  → if file is a symlink: follow it (upload target content under symlink's key)
  → debounce 500ms per-file (reset timer on each new event for same path)
  → check file is stable: stat file, wait 100ms, stat again — if size changed, re-defer
  → if encrypt: stream-encrypt to temp file with pyrage
  → upload to S3 with exponential backoff (3 attempts, delays: 1s, 3s, 9s)
  → on success: INSERT OR REPLACE INTO synced_files
  → on permanent failure (all retries exhausted): log error; DO NOT update DB

File deleted
  → if delete_on_remove: delete corresponding S3 key (append .age for encrypted entries)
     then DELETE FROM synced_files WHERE path = ?
  → else: no-op (DB entry left in place; file is gone locally but S3 retains it)

Directory created
  → watchdog recursive=True handles sub-directory watching automatically; no-op

File → directory transition
  → log warning "path changed from file to directory, skipping"
  → if delete_on_remove: delete old S3 key + remove DB entry
```

### File Size and Encryption

Files ≥ 50 MB use boto3's `upload_fileobj` with `TransferConfig(multipart_threshold=50MB)` — boto3 handles multipart automatically.

For encrypted files: `pyrage` streams encryption to a temp file in `~/.config/s3sync/tmp/`, then uploads the temp file via boto3. Temp files are deleted after upload (success or permanent failure). Stale temp files (>1 hour) are removed on daemon startup.

**Max file size**: No hard limit. Files up to several GB are supported.

### Logging
- Structured log output to `~/Library/Logs/s3sync/s3sync.log`
- Rotation: 10 MB max size, keep 3 files
- `s3sync logs` runs `tail -f` on the log file

---

## launchd Integration

Plist path: `~/Library/LaunchAgents/com.s3sync.agent.plist`

Key plist properties:
- `RunAtLoad = true` — starts daemon on install
- `KeepAlive = true` — auto-restarts on crash
- `StandardOutPath` / `StandardErrorPath` → `~/Library/Logs/s3sync/`
- `ProgramArguments` → `["/path/to/s3sync", "daemon"]`

`s3sync install` generates the plist with the correct Python executable path (from `sys.executable`), creates the log directory, loads via `launchctl load`.

---

## Project Layout

```
s3sync/
├── pyproject.toml
├── s3sync/
│   ├── __init__.py
│   ├── cli.py          # Typer app — all user-facing commands
│   ├── daemon.py       # watchdog observer loop + event handlers + debounce
│   ├── sync.py         # S3 upload / delete / retry logic
│   ├── state.py        # SQLite DB: open, migrate, read, write sync records
│   ├── initial_sync.py # StartupSync: walk files, diff vs DB, enqueue uploads
│   ├── crypto.py       # age encrypt/decrypt wrappers (pyrage), streaming
│   ├── config.py       # TOML load + Pydantic model validation
│   ├── launchd.py      # plist generation + launchctl wrappers
│   └── log.py          # logging setup + rotation
└── tests/
    ├── test_sync.py         # upload/delete/retry with mocked boto3 (moto)
    ├── test_state.py        # DB open/migrate/read/write operations
    ├── test_initial_sync.py # startup diff logic (mtime/size change detection)
    ├── test_config.py       # valid + invalid config scenarios
    ├── test_crypto.py       # encrypt/decrypt round-trip; multiple recipients
    └── test_daemon.py       # per-file debounce, include/exclude, file→dir transition
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Config file missing | Exit with clear message pointing to `s3sync config` |
| Invalid config | Pydantic validation error printed, daemon does not start |
| AWS credentials invalid at startup | Exit with error (do not start daemon silently) |
| AWS auth failure mid-operation | Log error; retry on next change event |
| S3 upload failure | Log error + retry with exponential backoff (3 attempts: 1s, 3s, 9s) |
| All retries exhausted | Log error; DB NOT updated; will retry on next event or startup |
| File read error / permission denied | Log warning, skip file |
| age encryption failure | Log error, skip upload (never upload plaintext on encryption failure) |
| Watch path doesn't exist | Log warning, skip that entry; daemon continues for other entries |
| Daemon lockfile held | Exit with error: "daemon already running" |
| SQLite DB locked | Retry with short backoff (daemon is single-writer; contention is CLI reads only) |
| File→directory transition | Log warning, skip upload; optionally delete S3 key |
| Symlink | Follow symlink, upload target content |

---

## Testing Strategy

- `test_state.py`: DB creation, migration, insert/replace/delete/query operations
- `test_initial_sync.py`: new file (not in DB), unchanged file (mtime/size match), changed file (mtime differs), file removed from DB, include/exclude filtering
- `test_sync.py`: upload/delete with mocked boto3 (moto); retry backoff; multipart threshold
- `test_config.py`: valid + invalid TOML configs; missing `age_recipients` with `encrypt=true`
- `test_crypto.py`: encrypt → decrypt round-trip; multiple recipients; streaming (large file)
- `test_daemon.py`: per-file debounce coalescing; include/exclude; file→dir transition; file-stability check
- Manual E2E: `s3sync sync` against localstack (`docker run localstack/localstack`) or real S3; verify DB state + S3 objects match

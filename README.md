# s3sync

A macOS CLI + background daemon that watches local folders and syncs file changes to AWS S3. Uses a local SQLite state database to track what has been synced — no S3 listing on every start. Supports optional client-side encryption via [age](https://age-encryption.org/).

## Requirements

- macOS (uses FSEvents and launchd)
- Python 3.11+
- AWS credentials configured (`~/.aws/credentials` or environment variables)

---

## Installation

```bash
pip install .
```

Verify the install:

```bash
s3sync          # prints help to stdout
s3sync --help   # same output
```

---

## Setup

### 1. Create the config file

```bash
s3sync config
```

This opens `~/.config/s3sync/config.toml` in your `$EDITOR`. A minimal config looks like:

```toml
[aws]
profile = "default"   # AWS profile from ~/.aws/credentials
region  = "us-east-1"

[[watch]]
path             = "/Users/you/Documents"
bucket           = "my-backup-bucket"
prefix           = "documents/"
delete_on_remove = false
exclude          = [".DS_Store", "*.tmp"]
```

You can add as many `[[watch]]` entries as you like — each maps to its own S3 bucket and prefix.

**Config options per `[[watch]]` entry:**

| Field | Default | Description |
|---|---|---|
| `path` | required | Local folder to watch |
| `bucket` | required | S3 bucket name |
| `prefix` | `""` | S3 key prefix (e.g. `"backups/"`) |
| `delete_on_remove` | `false` | Delete from S3 when file is deleted locally |
| `include` | `[]` | Glob patterns to include (empty = all files) |
| `exclude` | `[]` | Glob patterns to exclude (applied after include) |
| `encrypt` | `false` | Encrypt with age before uploading |
| `age_recipients` | `[]` | Public keys for encryption (required if `encrypt = true`) |
| `age_identity_file` | `null` | Private key path for `s3sync decrypt` |

Patterns in `include`/`exclude` use Python `fnmatch` syntax applied to the **filename only** — e.g. `*.pdf`, `.DS_Store`.

### 2. Test with a one-shot sync

Before installing the daemon, verify your config is correct:

```bash
s3sync sync
```

This walks your watched folders, compares against the local DB, and uploads new or changed files. No daemon is started.

### 3. Install the daemon

```bash
s3sync install
```

This writes a launchd agent plist to `~/Library/LaunchAgents/com.s3sync.agent.plist` and starts the daemon immediately. The daemon will:

- Auto-start on login
- Auto-restart on crash
- Watch all configured folders and sync changes within 500ms

### Daemon commands

```bash
s3sync status     # daemon state + per-folder file count, size, and last sync time
s3sync logs       # tail the live log at ~/Library/Logs/s3sync/s3sync.log
s3sync stop       # stop the daemon
s3sync start      # start the daemon (after install)
s3sync restart    # stop + start
s3sync uninstall  # remove the launchd agent and stop the daemon
```

---

## Setup with Encryption

Files are encrypted client-side using [age](https://age-encryption.org/) before upload. The plaintext never leaves your machine. Encrypted files are stored on S3 with a `.age` suffix.

### 1. Generate an age key pair

If you don't have the `age` CLI:

```bash
brew install age
```

Generate a key pair:

```bash
mkdir -p ~/.config/s3sync
age-keygen -o ~/.config/s3sync/age-key.txt
```

This prints your public key and writes the private key to the file:

```
Public key: age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p
```

### 2. Add an encrypted watch entry to your config

```toml
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
]
age_identity_file = "~/.config/s3sync/age-key.txt"
```

You can list multiple `age_recipients` — the file is encrypted once for all of them, and any corresponding private key can decrypt it.

### 3. Decrypt a file

To decrypt a `.age` file downloaded from S3:

```bash
s3sync decrypt ~/Downloads/contract.pdf.age
# → decrypts to ~/Downloads/contract.pdf
```

The identity file from the first matching encrypted watch entry in your config is used automatically.

---

## Development

### Clone and set up the environment

```bash
git clone <repo>
cd s3sync
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run the tests

```bash
pytest
```

Or with verbose output:

```bash
pytest -v
```

### Test structure

| File | What it tests |
|---|---|
| `tests/test_config.py` | Config loading and Pydantic validation |
| `tests/test_state.py` | SQLite DB schema, CRUD operations |
| `tests/test_crypto.py` | age encrypt/decrypt round-trip, multiple recipients |
| `tests/test_sync.py` | S3 upload/delete/retry with moto (no real AWS needed) |
| `tests/test_initial_sync.py` | Startup diff logic — new/changed/unchanged files, filters |
| `tests/test_daemon.py` | Per-file debounce, include/exclude filtering, file→dir transition |

AWS calls in `test_sync.py` are intercepted by [moto](https://docs.getmoto.org/) — no real AWS credentials are needed to run the tests.

### Project layout

```
s3sync/
├── pyproject.toml
└── s3sync/
    ├── cli.py          # typer app — all user-facing commands
    ├── daemon.py       # watchdog observer, per-file debounce, event routing
    ├── sync.py         # S3 upload/delete/retry logic
    ├── state.py        # SQLite DB: open, migrate, read/write sync records
    ├── initial_sync.py # startup walk: diff local files vs DB, upload changed
    ├── crypto.py       # age encrypt/decrypt wrappers (pyrage)
    ├── config.py       # TOML load + Pydantic model validation
    ├── launchd.py      # plist generation, launchctl wrappers, DaemonLock
    └── log.py          # logging setup and rotation
```

### Key design decisions

**SQLite state DB** — `~/.config/s3sync/state.db` is the source of truth for sync state. S3 is never listed on startup. The daemon diffs local file mtime/size against DB records to decide what to upload.

**Debounce** — each file path gets its own 500ms timer that resets on every new event for that path. Rapid saves (e.g. auto-save) coalesce into a single upload.

**Encryption safety** — if age encryption fails for any reason, the upload is skipped entirely. Plaintext is never uploaded as a fallback.

**Retry** — S3 uploads and deletes retry up to 3 times with exponential backoff (1s, 3s, 9s). The DB is only updated after a successful upload.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Dev setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run all tests
pytest -v --tb=short

# Run a single test file or test
pytest tests/test_daemon.py -v
pytest tests/test_sync.py::test_upload_retries -v

# Run the CLI locally (after pip install -e .)
s3sync --help
```

## Release process

Push to `main` triggers two workflows:
- **CI** (`.github/workflows/ci.yml`) — runs `pytest` on Python 3.11/3.12/3.13
- **Release** (`.github/workflows/release.yml`) — skips if `v<version>` tag already exists; otherwise builds a PyInstaller `--onedir` binary on `macos-14` (arm64), publishes a GitHub Release, and auto-updates `jdsingh/homebrew-repo`

To cut a new release: bump `version` in `pyproject.toml` and push to `main`.

The `HOMEBREW_TAP_TOKEN` secret must be set on the repo (`gh secret set HOMEBREW_TAP_TOKEN --repo jdsingh/s3sync`) for the Homebrew formula update step to work.

## Architecture

The app has two runtime modes: a one-shot CLI and a persistent launchd daemon. Both share the same modules.

**Data flow:**
1. `config.py` loads `~/.config/s3sync/config.toml` (TOML → Pydantic models)
2. `state.py` opens `~/.config/s3sync/state.db` (SQLite, thread-safe with a lock)
3. On daemon start: `initial_sync.py` walks each watch path and diffs local files against DB records (by mtime + size) — S3 is never listed
4. `daemon.py` starts a watchdog `Observer` per watch entry; `FileEventHandler` debounces events (500ms per-file timer) then submits to a `ThreadPoolExecutor`
5. `sync.py` performs the actual S3 upload/delete with 3-attempt exponential backoff (1s, 3s, 9s); DB is updated only after a successful upload
6. If `encrypt=true` on a watch entry, `crypto.py` encrypts to a temp `.age` file before upload; on failure the upload is skipped entirely (no plaintext fallback)

**launchd integration:**
- `launchd.py` generates the plist at install time. It detects `sys.frozen` (PyInstaller) to emit `[binary, "daemon"]` vs `[python, "-m", "s3sync.cli", "daemon"]` — these must not be confused.
- `is_running()` has a 3s timeout on `launchctl list` to avoid hanging during crash loops.
- The `daemon` CLI command is hidden (`@app.command(hidden=True)`) — it is only invoked by launchd.

**PyInstaller distribution:**
- Binary is built with `--onedir` (not `--onefile`) to avoid per-invocation extraction overhead (~7s cold start with `--onefile`).
- The Homebrew formula installs the directory into `libexec/` and places a wrapper script in `bin/`.

## Testing notes

- `test_sync.py` uses [moto](https://docs.getmoto.org/) to intercept all boto3 S3 calls — no real AWS credentials needed.
- `conftest.py` provides `tmp_watch_dir` and `tmp_config_dir` fixtures backed by `tmp_path`.
- `tests/test_daemon.py` exercises the debounce timer and `FileEventHandler` directly without starting a watchdog observer.

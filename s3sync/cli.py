import subprocess
import sys
from pathlib import Path

import typer

from s3sync.config import AppConfig, load_config, DEFAULT_CONFIG_PATH
from s3sync import launchd

app = typer.Typer(
    name="s3sync",
    help="Sync local folders to AWS S3, with optional age encryption.",
    no_args_is_help=True,
)

CONFIG_DIR = Path.home() / ".config" / "s3sync"


def _load_cfg() -> AppConfig:
    return load_config(DEFAULT_CONFIG_PATH)


@app.command()
def install() -> None:
    """Write launchd plist and enable auto-start on login."""
    launchd.install()
    typer.echo("✓ s3sync installed as a launchd agent.")


@app.command()
def uninstall() -> None:
    """Remove launchd plist and stop the daemon."""
    launchd.uninstall()
    typer.echo("✓ s3sync uninstalled.")


@app.command()
def start() -> None:
    """Start the daemon via launchctl."""
    launchd.start()
    typer.echo("✓ daemon started.")


@app.command()
def stop() -> None:
    """Stop the daemon via launchctl."""
    launchd.stop()
    typer.echo("✓ daemon stopped.")


@app.command()
def restart() -> None:
    """Restart the daemon."""
    launchd.stop()
    launchd.start()
    typer.echo("✓ daemon restarted.")


@app.command()
def status() -> None:
    """Show daemon state and sync stats from the local DB."""
    from s3sync.state import StateDB

    import datetime

    running = launchd.is_running()
    typer.echo(f"Daemon: {'running ✓' if running else 'stopped ✗'}")

    cfg = _load_cfg()
    with StateDB(CONFIG_DIR / "state.db") as db:
        for entry in cfg.watch:
            records = db.get_by_watch_root(entry.path)
            total_size = sum(r.size for r in records)
            last_sync = (
                datetime.datetime.fromtimestamp(max(r.synced_at for r in records)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if records
                else "never"
            )
            typer.echo(
                f"\n  {entry.path}\n"
                f"    → s3://{entry.bucket}/{entry.prefix}\n"
                f"    files synced: {len(records)}, total size: {total_size:,} bytes\n"
                f"    last sync: {last_sync}"
            )


@app.command()
def sync() -> None:
    """One-shot manual sync of all watch entries (no daemon)."""
    from s3sync.state import StateDB
    from s3sync.sync import S3Syncer
    from s3sync.initial_sync import run_initial_sync
    from s3sync.log import setup_logging

    setup_logging()
    cfg = _load_cfg()
    syncer = S3Syncer(region=cfg.aws.region, profile=cfg.aws.profile)

    with StateDB(CONFIG_DIR / "state.db") as db:
        for entry in cfg.watch:
            if not entry.path.exists():
                typer.echo(f"Warning: watch path does not exist, skipping: {entry.path}")
                continue
            typer.echo(f"Syncing {entry.path} → s3://{entry.bucket}/{entry.prefix}")
            run_initial_sync(entry, db, syncer, tmp_dir=CONFIG_DIR / "tmp")

    typer.echo("✓ Sync complete.")


@app.command()
def logs() -> None:
    """Tail the daemon log file."""
    from s3sync.log import get_log_path

    log_path = get_log_path()
    if not log_path.exists():
        typer.echo(f"Log file not found: {log_path}")
        raise typer.Exit(1)
    subprocess.run(["tail", "-f", str(log_path)])


@app.command()
def config() -> None:
    """Open the config file in $EDITOR."""
    import os

    cfg_path = DEFAULT_CONFIG_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(
            '[aws]\nprofile = "default"\nregion = "us-east-1"\n\n'
            "# [[watch]]\n# path = \"/your/folder\"\n"
            '# bucket = "your-bucket"\n# prefix = ""\n'
        )
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(cfg_path)])


@app.command(hidden=True)
def daemon() -> None:
    """Internal: run the sync daemon (called by launchd)."""
    from s3sync.launchd import DaemonLock
    from s3sync.daemon import run_daemon
    from s3sync.log import setup_logging

    setup_logging()
    lock = DaemonLock()
    if not lock.acquire():
        typer.echo("Error: daemon already running.", err=True)
        raise typer.Exit(1)
    try:
        cfg = _load_cfg()
        run_daemon(cfg, CONFIG_DIR)
    finally:
        lock.release()


@app.command()
def decrypt(path: Path = typer.Argument(..., help="Path to a local .age file")) -> None:
    """Decrypt a local .age file using the configured age identity."""
    from s3sync.crypto import load_identity, decrypt_file

    cfg = _load_cfg()
    identity_file = None
    for entry in cfg.watch:
        if entry.encrypt and entry.age_identity_file:
            identity_file = entry.age_identity_file
            break

    if identity_file is None:
        typer.echo("No age_identity_file found in any encrypted watch entry.", err=True)
        raise typer.Exit(1)

    identity = load_identity(identity_file)
    out = path.with_suffix("")  # strip .age
    decrypt_file(path, identity, out)
    typer.echo(f"✓ Decrypted to {out}")


if __name__ == "__main__":
    app()

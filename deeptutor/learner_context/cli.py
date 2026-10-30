"""Command-line profile export and integration management."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone

import httpx
import typer
from rich.console import Console

from . import repository, service

app = typer.Typer(help="Inspect and export the shared learner context.", no_args_is_help=True)
console = Console()


def _api_client() -> tuple[httpx.Client, str]:
    """Use a supplied owner token or sign in with hidden terminal input."""
    base = os.environ.get("PI_TUTOR_API_URL", "http://127.0.0.1:18051").rstrip("/")
    token = os.environ.get("PI_TUTOR_AUTH_TOKEN", "")
    client = httpx.Client(timeout=20)
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
        return client, base
    username = typer.prompt("Pi Tutor username")
    password = typer.prompt("Pi Tutor password", hide_input=True)
    try:
        response = client.post(
            f"{base}/api/auth/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        client.close()
        raise typer.BadParameter(f"Pi Tutor sign-in failed: {exc}") from exc
    if not client.cookies:
        client.close()
        raise typer.BadParameter("Pi Tutor did not create a sign-in session. Check that authentication is enabled.")
    return client, base


def show(format: str = typer.Option("text", "--format", "-f", help="Export format: text or json.")):
    """Print the learner style profile for copying into a harness."""
    try:
        typer.echo(service.export_profile(format=format))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def set_enabled(enabled: bool = typer.Argument(..., help="Set true to collect and inject style context.")):
    """Enable or pause learner style collection and prompt context."""
    state = service.set_enabled(enabled)
    console.print(f"Learner context {'enabled' if state else 'paused'}.")


def delete_record(record_id: int = typer.Argument(..., help="Style record ID from the JSON profile.")):
    """Delete one learned style record."""
    if not service.delete_record(record_id):
        raise typer.BadParameter("No style record found with that ID.")
    console.print(f"Deleted style record {record_id}.")


def backup(output_dir: Path | None = typer.Option(None, "--output-dir", help="Destination for the backup file.")):
    """Create a consistent SQLite backup before a schema change or release."""
    source = repository.database_path()
    if not source.is_file():
        raise typer.BadParameter(f"Learner database does not exist: {source}")
    directory = output_dir or (source.parent / "learner-context-backups")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"chat_mastery-{stamp}.sqlite3"
    src = sqlite3.connect(source, timeout=15)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    try:
        target.chmod(0o600)
        directory.chmod(0o700)
    except OSError:
        pass
    typer.echo(str(target))


def clear(yes: bool = typer.Option(False, "--yes", help="Confirm permanent deletion of all style records.")):
    """Delete all learned style records from the current account database."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm deletion.")
    console.print(f"Deleted {service.clear_records()} style records.")


def credentials_create(
    name: str = typer.Argument(..., help="Harness name."),
    expires_in_days: int = typer.Option(30, min=1, max=90),
    read_only: bool = typer.Option(False, "--read-only", help="Do not allow interaction recording."),
):
    """Create a scoped integration token after sign-in."""
    client, base = _api_client()
    try:
        response = client.post(
            f"{base}/api/learner-context/v1/credentials",
            json={
                "name": name,
                "expires_in_days": expires_in_days,
                "scopes": ["read"] if read_only else ["read", "write"],
            },
        )
        response.raise_for_status()
        # This is the only response that contains the one-time token.
        typer.echo(response.text)
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"Credential creation failed: {exc}") from exc
    finally:
        client.close()


def credentials_list():
    """List integration credential names and expiry dates."""
    client, base = _api_client()
    try:
        response = client.get(f"{base}/api/learner-context/v1/credentials")
        response.raise_for_status()
        console.print_json(data=response.json())
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"Credential listing failed: {exc}") from exc
    finally:
        client.close()


def credentials_revoke(credential_id: str = typer.Argument(...)):
    """Revoke one integration credential by its ID."""
    client, base = _api_client()
    try:
        response = client.delete(f"{base}/api/learner-context/v1/credentials/{credential_id}")
        response.raise_for_status()
        console.print("Integration credential revoked.")
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"Credential revocation failed: {exc}") from exc
    finally:
        client.close()


def credentials_revoke_all():
    """Revoke all external harness credentials."""
    client, base = _api_client()
    try:
        response = client.delete(f"{base}/api/learner-context/v1/credentials")
        response.raise_for_status()
        console.print_json(data=response.json())
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"Credential revocation failed: {exc}") from exc
    finally:
        client.close()


def serve_mcp():
    """Run the optional FastMCP bridge for Pi, DSH, or another MCP host."""
    from .mcp_server import main

    main()


def register(target: typer.Typer) -> None:
    """Register learner-context commands in the main DeepTutor CLI."""
    target.command("show")(show)
    target.command("set-enabled")(set_enabled)
    target.command("delete-record")(delete_record)
    target.command("backup")(backup)
    target.command("clear")(clear)
    target.command("credentials-create")(credentials_create)
    target.command("credentials-list")(credentials_list)
    target.command("credentials-revoke")(credentials_revoke)
    target.command("credentials-revoke-all")(credentials_revoke_all)
    target.command("serve-mcp")(serve_mcp)


register(app)

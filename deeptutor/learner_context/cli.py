"""Command-line profile export and integration management."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

import httpx
import typer
from rich.console import Console

from . import credentials, repository, service

app = typer.Typer(help="Inspect and export the shared learner context.", no_args_is_help=True)
console = Console()


def _with_local_owner(command):
    from functools import wraps
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser, LOCAL_ADMIN_ID, LOCAL_ADMIN_USERNAME, UserScope
    from deeptutor.multi_user.paths import PROJECT_ROOT

    def local_owner():
        return CurrentUser(
            id=LOCAL_ADMIN_ID,
            username=LOCAL_ADMIN_USERNAME,
            role="admin",
            scope=UserScope(kind="admin", user_id=LOCAL_ADMIN_ID, root=PROJECT_ROOT / "data"),
        )

    @wraps(command)
    def wrapped(*args, **kwargs):
        context = set_current_user(local_owner())
        try:
            return command(*args, **kwargs)
        finally:
            reset_current_user(context)

    return wrapped

def _api_client() -> tuple[httpx.Client, str, str]:
    """Use a supplied owner token or sign in with hidden terminal input."""
    base = os.environ.get("PI_TUTOR_API_URL", "http://127.0.0.1:18051").rstrip("/")
    token = os.environ.get("PI_TUTOR_AUTH_TOKEN", "")
    client = httpx.Client(timeout=20)
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
        return client, base, token
    username = typer.prompt("Pi Tutor username")
    password = typer.prompt("Pi Tutor password", hide_input=True)
    try:
        response = client.post(
            f"{base}/api/auth/login",
            json={"username": username, "password": password, "return_access_token": True},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        client.close()
        raise typer.BadParameter(f"Pi Tutor sign-in failed: {exc}") from exc
    if not client.cookies:
        client.close()
        raise typer.BadParameter("Pi Tutor did not create a sign-in session. Check that authentication is enabled.")
    token = str(response.json().get("access_token") or "")
    if not token:
        client.close()
        raise typer.BadParameter("Pi Tutor sign-in did not return an owner access token.")
    return client, base, token


@_with_local_owner
def show(format: str = typer.Option("text", "--format", "-f", help="Export format: text or json.")):
    """Print the learner style profile for copying into a harness."""
    try:
        typer.echo(service.export_profile(format=format))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@_with_local_owner
def set_enabled(enabled: bool = typer.Argument(..., help="Set true to collect and inject style context.")):
    """Enable or pause learner style context injection."""
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, _owner_token = _api_client()
        try:
            response = client.put(f"{base}/api/learner-context/v1/settings", json={"enabled": enabled})
            response.raise_for_status()
            state = bool(response.json().get("enabled"))
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Learner context setting failed: {exc}") from exc
        finally:
            client.close()
    else:
        state = service.set_enabled(enabled)
    console.print(f"Learner context {'enabled' if state else 'paused'}.")


@_with_local_owner
def set_automatic_collection(
    enabled: bool = typer.Argument(..., help="Set true only after the account owner opts in."),
):
    """Enable or pause LLM-based style classification of Pi Tutor turns."""
    from deeptutor.services import chat_mastery
    from deeptutor.services.auth import AUTH_ENABLED
    if not AUTH_ENABLED:
        state = chat_mastery.set_style_collection_enabled(enabled)
        console.print(f"Automatic learner-style collection {'enabled' if state else 'paused'}.")
        return
    client, base, _owner_token = _api_client()
    try:
        response = client.put(
            f"{base}/api/learner-context/v1/automatic-collection",
            json={"enabled": enabled},
        )
        response.raise_for_status()
        state = bool(response.json().get("automatic_collection_enabled"))
        console.print(f"Automatic learner-style collection {'enabled' if state else 'paused'}.")
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"Automatic collection setting failed: {exc}") from exc
    finally:
        client.close()


@_with_local_owner
def delete_record(record_id: int = typer.Argument(..., help="Style record ID from the JSON profile.")):
    """Delete one learned style record."""
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, _owner_token = _api_client()
        try:
            response = client.delete(f"{base}/api/learner-context/v1/records/{record_id}")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Style record deletion failed: {exc}") from exc
        finally:
            client.close()
        console.print(f"Deleted style record {record_id}.")
        return
    deleted = service.delete_record(record_id)
    if not deleted:
        raise typer.BadParameter("No style record found with that ID.")
    console.print(f"Deleted style record {record_id}.")


@_with_local_owner
def backup(output_dir: Path | None = typer.Option(None, "--output-dir", help="Destination for the backup file.")):
    """Create a consistent SQLite backup before a schema change or release."""
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        raise typer.BadParameter("SQLite backup must run under the owner's local account context.")
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


@_with_local_owner
def clear(yes: bool = typer.Option(False, "--yes", help="Confirm permanent deletion of all style records.")):
    """Delete all learned style records from the current account database."""
    if not yes:
        raise typer.BadParameter("Pass --yes to confirm deletion.")
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, _owner_token = _api_client()
        try:
            response = client.delete(f"{base}/api/learner-context/v1/records")
            response.raise_for_status()
            count = int(response.json().get("deleted", 0))
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Style record deletion failed: {exc}") from exc
        finally:
            client.close()
    else:
        count = service.clear_records()
    console.print(f"Deleted {count} style records.")


@_with_local_owner
def credentials_create(
    name: str = typer.Argument(..., help="Harness name."),
    expires_in_days: int = typer.Option(30, min=1, max=90),
    read_only: bool = typer.Option(False, "--read-only", help="Do not allow interaction recording."),
):
    """Create a scoped local token and save it in the existing secret manager."""
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, owner_token = _api_client()
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
            created = response.json()
            console.print(f"Created {created['scopes']} credential {created['id']}.")
            console.print("Configure the signed-in owner token as `PI_TUTOR_AUTH_TOKEN` in the MCP process environment.")
            console.print("Do not print or share this token. The CLI does not display it.")
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Credential creation failed: {exc}") from exc
        finally:
            client.close()
        return

    user_id = get_current_user().id
    token = credentials.create(
        user_id,
        name,
        expires_in_days,
        ("read",) if read_only else ("read", "write"),
    )
    console.print(f"Created {token['scopes']} credential {token['id']}.")
    console.print("Token saved in the local owner secret manager. Its value was not printed.")


@_with_local_owner
def credentials_list():
    """List integration credential metadata for the owner."""
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, _owner_token = _api_client()
        try:
            response = client.get(f"{base}/api/learner-context/v1/credentials")
            response.raise_for_status()
            console.print_json(data=response.json())
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Credential listing failed: {exc}") from exc
        finally:
            client.close()
        return
    console.print_json(data={"credentials": credentials.list_for_user(get_current_user().id)})


@_with_local_owner
def credentials_revoke(credential_id: str = typer.Argument(...)):
    """Revoke one integration credential by its ID."""
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, _owner_token = _api_client()
        try:
            response = client.delete(f"{base}/api/learner-context/v1/credentials/{credential_id}")
            response.raise_for_status()
            console.print("Integration credential revoked.")
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Credential revocation failed: {exc}") from exc
        finally:
            client.close()
        return
    if not credentials.revoke(get_current_user().id, credential_id):
        raise typer.BadParameter("Credential not found or already revoked.")
    console.print("Integration credential revoked.")


@_with_local_owner
def credentials_revoke_all():
    """Revoke all integration credentials for the owner."""
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.services.auth import AUTH_ENABLED

    if AUTH_ENABLED:
        client, base, _owner_token = _api_client()
        try:
            response = client.delete(f"{base}/api/learner-context/v1/credentials")
            response.raise_for_status()
            console.print(f"Revoked {response.json().get('revoked', 0)} credentials.")
        except httpx.HTTPError as exc:
            raise typer.BadParameter(f"Credential revocation failed: {exc}") from exc
        finally:
            client.close()
        return
    revoked = credentials.revoke_all(get_current_user().id)
    console.print(f"Revoked {revoked} credentials.")


def serve_mcp():
    """Run the optional FastMCP bridge for Pi, DSH, or another MCP host."""
    from .mcp_server import main

    main()


def register(target: typer.Typer) -> None:
    """Register learner-context commands in the main DeepTutor CLI."""
    target.command("show")(show)
    target.command("set-enabled")(set_enabled)
    target.command("set-automatic-collection")(set_automatic_collection)
    target.command("delete-record")(delete_record)
    target.command("backup")(backup)
    target.command("clear")(clear)
    target.command("credentials-create")(credentials_create)
    target.command("credentials-list")(credentials_list)
    target.command("credentials-revoke")(credentials_revoke)
    target.command("credentials-revoke-all")(credentials_revoke_all)
    target.command("serve-mcp")(serve_mcp)


register(app)

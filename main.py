"""Finance Tracker entry point.

Run with ``python main.py``. Options:

    --port N        port to listen on (default: $FINANCE_PORT or 8080)
    --seed-demo     create a "Demo" user with a few years of random transactions

Environment variables:

    FINANCE_PORT            default port
    FINANCE_DB_PATH         path of the SQLite file (default: finance.db next to this file)
    FINANCE_STORAGE_SECRET  secret for per-browser storage (default: generated once and
                            kept in the database)
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
from pathlib import Path

# Keep NiceGUI's per-browser storage next to the app instead of the current directory.
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(Path(__file__).resolve().parent / ".nicegui"))

from nicegui import ui  # noqa: E402

from db import DataError, settings, users, schema, demo, core
from components import APP_TITLE  # noqa: E402

# Importing the page modules registers their routes (they use @ui.page).
import pages.categories  # noqa: E402,F401
import pages.overview  # noqa: E402,F401
import pages.transactions  # noqa: E402,F401
import pages.export  # noqa: E402,F401
import pages.import_csv  # noqa: E402,F401
import pages.backups  # noqa: E402,F401
import pages.shared  # noqa: E402,F401

log = logging.getLogger("finance_tracker")

DEFAULT_PORT = 8080
HOST = "0.0.0.0"  # listen on all interfaces so other devices on the network can connect


def get_storage_secret() -> str:
    """Secret used to sign the per-browser storage cookie.

    Taken from FINANCE_STORAGE_SECRET if set; otherwise a random value is
    generated once and kept in the settings table so it survives restarts.
    """
    env_secret = os.environ.get("FINANCE_STORAGE_SECRET")
    if env_secret:
        return env_secret
    stored = settings.get_setting("storage_secret")
    if stored:
        return stored
    new_secret = secrets.token_hex(32)
    settings.set_setting("storage_secret", new_secret)
    return new_secret


def reset_password(name: str) -> None:
    """Command-line password reset (also logs that user out on every device)."""
    import getpass

    user_list = users.list_users()
    user = next((u for u in user_list if u["name"].casefold() == name.strip().casefold()), None)
    if user is None:
        raise SystemExit(f"No user called '{name}'. Users: {', '.join(u['name'] for u in user_list)}")
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("The passwords didn't match.")
    try:
        users.set_password(user["id"], password)
    except DataError as exc:
        raise SystemExit(str(exc))
    print(f"Password updated for '{user['name']}'. They will need to log in again on every device.")


def lan_address() -> str | None:
    """Best-effort guess of this machine's address on the local network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))  # no packets are sent for UDP connect
            return sock.getsockname()[0]
    except OSError:
        return None


def parse_args() -> argparse.Namespace:
    """Read command-line options."""
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FINANCE_PORT", DEFAULT_PORT)),
        help="port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--seed-demo", action="store_true",
        help="create a 'Demo' user with a few years of random transactions",
    )
    parser.add_argument("--reset-password", metavar="NAME",
                    help="set a new password for a user, then exit")
    return parser.parse_args()


def main() -> None:
    """Prepare the database and start the web server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()

    schema.init_db()
    if args.reset_password:
        reset_password(args.reset_password)
        return
    if args.seed_demo:
        if not settings.is_base_currency_set():
            settings.set_base_currency(settings.DEFAULT_BASE_CURRENCY)
        demo.seed_demo_user()
        log.info("Demo data is ready (user 'Demo').")

    log.info("Database: %s", core.get_db_path())
    log.info("Open http://localhost:%d", args.port)
    address = lan_address()
    if address:
        log.info("From another device on your network: http://%s:%d", address, args.port)

    ui.run(
        host=HOST,
        port=args.port,
        title=APP_TITLE,
        reload=False,
        show=False,
        storage_secret=get_storage_secret(),
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()

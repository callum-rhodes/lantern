"""Backups page: save the database to a timestamped file, download, restore or delete backups.

Save as ``pages/backups.py``. All file handling lives in ``db.py`` (see the
Backups section there); this page only provides the buttons and dialogs.

Restoring replaces ALL data for ALL users, so it always asks first and saves a
"pre-restore" copy of the current data before overwriting it.
"""
from __future__ import annotations

import uuid
from typing import Any

from nicegui import ui

import components
import db


def _size(num_bytes: int) -> str:
    """Human-readable file size."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


@ui.page("/backups")
async def backups_page() -> None:
    """Render the backups page."""
    ctx = await components.build_layout("Backups")
    if ctx is None:
        return

    @ui.refreshable
    def backup_list() -> None:
        backups = db.list_backups()
        if not backups:
            components.info_message("No backups yet. Click 'Back up now' above.", "backup")
            return
        for item in backups:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label(item["name"]).classes("text-subtitle1 text-bold")
                        ui.label(
                            f"{item['modified']:%d %b %Y %H:%M:%S} · {_size(item['size'])}"
                        ).classes("text-caption text-grey-7")
                    with ui.row().classes("gap-1"):
                        ui.button(icon="download", on_click=lambda i=item: download(i)) \
                            .props("flat round dense").tooltip("Download")
                        ui.button("Restore", icon="restore", on_click=lambda i=item: restore(i)) \
                            .props("flat")
                        ui.button(icon="delete", on_click=lambda i=item: delete(i)) \
                            .props("flat round dense color=negative").tooltip("Delete")

    def back_up_now() -> None:
        try:
            name = db.create_backup()
        except db.DataError as exc:
            components.notify_error(exc)
            return
        ui.notify(f"Saved {name}", type="positive")
        backup_list.refresh()

    def download(item: dict[str, Any]) -> None:
        try:
            ui.download(db.resolve_backup(item["name"]))
        except db.DataError as exc:
            components.notify_error(exc)

    def delete(item: dict[str, Any]) -> None:
        def do_delete() -> None:
            db.delete_backup(item["name"])
            ui.notify(f"Deleted {item['name']}", type="positive")
            backup_list.refresh()

        components.confirm_dialog(
            "Delete backup", f"Delete '{item['name']}'? This cannot be undone.", "Delete", do_delete
        )

    def restore(item: dict[str, Any]) -> None:
        def do_restore() -> None:
            safety = db.restore_backup(item["name"])
            ui.notify(f"Restored {item['name']}. Previous data saved as {safety}.", type="positive")
            ui.navigate.reload()

        components.confirm_dialog(
            "Restore backup",
            f"Replace ALL current data (every user) with '{item['name']}'? "
            "Your current data is saved as a 'pre-restore' backup first, so you can undo this.",
            "Restore",
            do_restore,
        )

    async def handle_upload(event: Any) -> None:
        temp = db.get_backup_dir() / f".upload-{uuid.uuid4().hex}.tmp.db"
        try:
            temp.write_bytes(await event.file.read())
            name = db.import_backup_file(temp, event.file.name)
        except db.DataError as exc:
            components.notify_error(exc)
            return
        finally:
            temp.unlink(missing_ok=True)
        ui.notify(f"Added {name} to your backups. Use Restore to load it.", type="positive")
        upload.reset()
        backup_list.refresh()

    with components.content():
        ui.label("Backups").classes("text-h5")
        ui.label(
            f"Backups are full copies of the database, saved in {db.get_backup_dir()}. "
            "Restoring replaces the data of all users."
        ).classes("text-grey-7")

        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-6"):
                ui.button("Back up now", icon="backup", on_click=back_up_now)
                upload = ui.upload(
                    label="Add a backup file (.db)", auto_upload=True,
                    on_upload=handle_upload, max_files=1,
                ).props('accept=".db,.sqlite,.sqlite3" flat bordered').classes("grow")

        ui.label("Saved backups").classes("text-h6")
        backup_list()
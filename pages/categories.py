"""Categories page: view, add, rename, reorder and delete categories and subcategories.

Two levels only: a top-level category has a kind (income/expense) and its
subcategories inherit it. The two-level limit is enforced in ``db/categories.add_category``
(this page only offers "add subcategory" on top-level categories).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

import components
from db import DataError, categories


def _label(category: dict[str, Any]) -> str:
    """'Food' or 'Food > Groceries'."""
    parent = category.get("parent_name")
    return f"{parent} > {category['name']}" if parent else category["name"]


def _ask_name(
    title: str, label: str, initial: str, confirm_text: str, on_submit: Callable[[str], None]
) -> None:
    """Pop up a small dialog asking for a name; ``on_submit`` does the database work."""
    dialog = components.new_dialog()
    with dialog, ui.card().classes("w-80"):
        ui.label(title).classes("text-h6")
        name = ui.input(label, value=initial).props("autofocus")

        def submit() -> None:
            try:
                on_submit(name.value)
            except DataError as exc:
                components.notify_error(exc)
                return
            dialog.close()

        name.on("keydown.enter", submit)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button(confirm_text, on_click=submit)
    dialog.open()


def _delete_category(user_id: int, category: dict[str, Any], refresh: Callable[[], None]) -> None:
    """Delete a category, asking where to move its transactions if it has any."""
    usage = categories.category_usage(user_id, category["id"])
    name = _label(category)

    if usage["subcategories"]:
        ui.notify(f"'{name}' still has subcategories. Delete those first.", type="warning")
        return

    def delete(reassign_to: int | None = None) -> None:
        categories.delete_category(user_id, category["id"], reassign_to)
        ui.notify(f"Deleted '{name}'.", type="positive")
        refresh()

    if not usage["transactions"]:
        components.confirm_dialog("Delete category", f"Delete '{name}'?", "Delete", delete)
        return

    options = categories.category_options(user_id, kind=category["kind"], exclude_id=category["id"])
    if not options:
        ui.notify(
            f"'{name}' has {usage['transactions']} transaction(s) and there is no other "
            f"{category['kind']} category to move them to. Add one first.",
            type="warning",
        )
        return

    dialog = components.new_dialog()
    with dialog, ui.card().classes("w-96"):
        ui.label("Move transactions first").classes("text-h6")
        ui.label(
            f"'{name}' has {usage['transactions']} transaction(s). "
            "Choose a category to move them to, then it will be deleted."
        )
        target = ui.select(options, value=next(iter(options)), label="Move to").classes("w-full")

        def move_and_delete() -> None:
            try:
                categories.delete_category(user_id, category["id"], target.value)
            except DataError as exc:
                components.notify_error(exc)
                return
            dialog.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Move and delete", on_click=move_and_delete).props("color=negative")
    dialog.open()


def _icon_button(icon: str, tooltip: str, on_click: Callable[[], None], enabled: bool = True) -> None:
    """A small flat icon button with a tooltip."""
    button = ui.button(icon=icon, on_click=on_click).props("flat round dense").tooltip(tooltip)
    button.set_enabled(enabled)


@ui.page("/categories")
async def categories_page() -> None:
    """Render the categories page."""
    ctx = await components.build_layout("Edit Categories")
    if ctx is None:
        return
    user_id = ctx.user["id"]

    @ui.refreshable
    def category_list() -> None:
        tree = categories.get_category_tree(user_id)
        if not tree:
            components.info_message("No categories yet. Add one above.", "category")
            return

        for kind, heading in (("expense", "Expense categories"), ("income", "Income categories")):
            ui.label(heading).classes("text-h6")
            tops = [t for t in tree if t["kind"] == kind]
            if not tops:
                components.info_message(f"No {kind} categories yet.")
                continue
            for index, top in enumerate(tops):
                with ui.card().classes("w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(top["name"]).classes("text-subtitle1 text-bold")
                        with ui.row().classes("gap-1"):
                            _icon_button("arrow_upward", "Move up", lambda c=top: move(c, -1),
                                         enabled=index > 0)
                            _icon_button("arrow_downward", "Move down", lambda c=top: move(c, 1),
                                         enabled=index < len(tops) - 1)
                            _icon_button("add", "Add subcategory",
                                         lambda t=top: add_subcategory(t))
                            _icon_button("edit", "Rename", lambda c=top: rename(c))
                            _icon_button("delete", "Delete",
                                         lambda c=top: _delete_category(user_id, c, category_list.refresh))
                    if not top["children"]:
                        ui.label("No subcategories").classes("text-caption text-grey-7 q-pl-md")
                    for child_index, child in enumerate(top["children"]):
                        child = {**child, "parent_name": top["name"]}
                        protected = categories.is_other(child)
                        movable = sum(not categories.is_other(c) for c in top["children"])
                        with ui.row().classes("w-full items-center justify-between q-pl-md"):
                            ui.label(child["name"])
                            with ui.row().classes("gap-1"):
                                _icon_button("arrow_upward", "Move up", lambda c=child: move(c, -1),
                                             enabled=not protected and child_index > 0)
                                _icon_button("arrow_downward", "Move down", lambda c=child: move(c, 1),
                                             enabled=not protected and child_index < movable - 1)
                                _icon_button("edit", "Rename", lambda c=child: rename(c), enabled=not protected)
                                _icon_button("delete", "Delete",
                                             lambda c=child: _delete_category(user_id, c, category_list.refresh),
                                             enabled=not protected)

    def move(category: dict[str, Any], direction: int) -> None:
        """Move a category up (-1) or down (+1) within its group."""
        try:
            categories.move_category(user_id, category["id"], direction)
        except DataError as exc:
            components.notify_error(exc)
            return
        category_list.refresh()

    def add_subcategory(parent: dict[str, Any]) -> None:
        def submit(name: str) -> None:
            categories.add_category(user_id, name, parent_id=parent["id"])
            category_list.refresh()

        _ask_name(f"Add subcategory to '{parent['name']}'", "Name", "", "Add", submit)

    def rename(category: dict[str, Any]) -> None:
        def submit(name: str) -> None:
            categories.rename_category(user_id, category["id"], name)
            category_list.refresh()

        _ask_name("Rename category", "New name", category["name"], "Rename", submit)

    def add_top_level() -> None:
        try:
            categories.add_category(user_id, new_name.value, None, new_kind.value)
        except DataError as exc:
            components.notify_error(exc)
            return
        new_name.value = ""
        category_list.refresh()

    with components.content():
        ui.label("Categories").classes("text-h5")
        ui.label(
            "Categories have up to two levels. Subcategories use the same type "
            "(income or expense) as their parent."
        ).classes("text-grey-7")
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-4"):
                new_name = ui.input("New category").classes("grow")
                new_name.on("keydown.enter", add_top_level)
                new_kind = ui.toggle({"expense": "Expense", "income": "Income"}, value="expense")
                ui.button("Add category", icon="add", on_click=add_top_level)
        category_list()

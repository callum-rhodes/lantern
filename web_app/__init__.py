"""Web app package: everything that builds the NiceGUI interface.

Import the module you need, e.g. ``from web_app import components, state``. Nothing is
re-exported here. All SQL and business logic live in the ``db`` package; this package only
calls it and shows the results.

Modules
-------
components   page frame: header, navigation, login card, user/password dialogs,
             first-run setup, shared helpers (dialogs, notifications)
state        per-browser state: logged-in user (login, logout, rate limiting)
             and display currency
shared_ui    shared-transaction widgets: the split dialog and the pending-shares card
pages/       one module per route (overview, transactions, shared, categories,
             export, import_csv, backups)

Importing a page module registers its route (they use ``@ui.page``), so ``main.py``
imports every module in ``pages/``.
"""
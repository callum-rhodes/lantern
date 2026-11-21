# Finance Tracker

A small, locally hosted personal finance tracker. Python, [NiceGUI](https://nicegui.io) for the whole UI, SQLite for storage, Plotly for charts.

- Multiple named users, each with their own transactions and categories (no passwords)
- Two-level categories (category > subcategory), income and expense
- Everything is recorded in one base currency; view it in any currency Frankfurter publishes rates for
- Overview page with summary cards, drill-down charts, yearly trends and a month x category table

## Requirements

- Python 3.11 or newer
- Internet access is only needed for exchange rates. Without it the app still works, using cached rates or just the base currency.

## Install and run

```bash
cd finance_tracker

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

python main.py
```

Then open <http://localhost:8080>.

On first run the app asks for your **base currency** (default GBP) and the name of the first user. Pick the base currency carefully: every amount is stored in it, so changing it later would change what your recorded numbers mean.

### Try it with demo data

```bash
python main.py --seed-demo
```

This creates a user called **Demo** with about three years of random transactions so you can see the charts. It does nothing if Demo already has transactions.

## Open it from another device on your network

The server listens on all interfaces (`0.0.0.0`), so phones, tablets and other computers on the same network can use it.

1. Start the app. The log prints a line such as `From another device on your network: http://192.168.1.20:8080`.
2. Open that address on the other device.
3. If it does not load, allow incoming connections to the port in the host machine's firewall (for example `sudo ufw allow 8080/tcp` on Ubuntu).

**There is no login.** Anyone who can reach the port can see and change everything, so only run it on a network you trust and do not expose the port to the internet.

## Configuration

| Setting | How | Default |
| --- | --- | --- |
| Port | `--port 9000` or `FINANCE_PORT=9000` | `8080` |
| Database file | `FINANCE_DB_PATH=/path/to/finance.db` | `finance.db` next to `main.py` |
| Storage secret | `FINANCE_STORAGE_SECRET=...` | generated once and kept in the database |

Each browser remembers its own selected user, display currency and dark-mode choice. NiceGUI keeps that in a `.nicegui` folder next to `main.py`.

## How it works

- **Amounts** are stored as signed integer minor units (pence/cents) in the base currency: income is positive, spending is negative. The Income/Expense toggle on the form applies the sign, so you always type a positive number.
- **Currency display:** the selector in the header converts amounts at display time only. Stored data is never changed. Amounts you type into the form are always in the base currency.
- **Exchange rates** come from the free [Frankfurter](https://www.frankfurter.app) API and are cached in the database for 24 hours. If a refresh fails, the cached rates are used and a warning shows when they were last updated.
- **Deleting a category** is blocked while it has subcategories. If it has transactions, you choose another category of the same type to move them to first.

## Project structure

```
finance_tracker/
  main.py            entry point: options, route registration, ui.run()
  db.py              connection handling, schema, ALL SQL queries
  rates.py           exchange-rate fetching/caching, money parsing and formatting
  state.py           active user and display currency (per browser)
  components.py      page frame: header, user/currency pickers, first-run setup, dialogs
  pages/
    transactions.py  add/edit/delete form, filters and table   (route: /)
    overview.py      cards, charts, month x category table     (route: /overview)
    categories.py    manage categories and subcategories       (route: /categories)
  requirements.txt
  README.md
```

Conventions worth knowing when you change things:

- All SQL lives in `db.py`. Pages call its functions and never write SQL.
- Problems a user can fix raise `db.DataError` with a friendly message; pages show it with `components.notify_error`.
- Every page starts with `ctx = await components.build_layout("Name")` and puts its content inside `with components.content():`.
- Money is always formatted through `rates.format_money` (usually via `ctx.display.money(cents)`).

## Backups

Everything lives in one SQLite file, so a backup is a copy of `finance.db`. Use SQLite's own backup command, which is safe while the app is running:

```bash
sqlite3 finance.db ".backup 'finance-backup.db'"
```

## Extending the app later

- **Bank CSV import:** add a `db.import_transactions(user_id, rows)` that inserts many rows in one transaction (reuse the checks in `_check_transaction`), then a small page or dialog with `ui.upload` that parses the CSV and shows a preview before saving. Skipping duplicates by (date, amount, description) keeps repeated imports safe.
- **Multiple accounts:** add an `accounts` table (`id`, `user_id`, `name`) and an `account_id` column on `transactions`. Add an account dropdown to the form and an account filter to the transactions and overview pages. A transfer is two transactions, one on each account.
- **Automatic backups:** call SQLite's backup from a `ui.timer` (or `app.on_startup`) that writes a dated copy into a `backups/` folder and prunes old ones.
- **Budgets:** a `budgets(user_id, category_id, month, amount_cents)` table plus a progress bar per category on the overview page.
- **Recurring transactions:** a `recurring` table checked at startup, creating any transactions that have come due.
- **Schema changes:** the schema is created with `CREATE TABLE IF NOT EXISTS`, which will not alter existing tables. When you add columns, add a `PRAGMA user_version` check in `db.init_db()` that runs `ALTER TABLE` steps, or move to a migration tool such as Alembic.
- **Passwords:** if you ever need to protect it, NiceGUI's docs include a simple login example that keeps the signed-in user in `app.storage.user`; `state.py` is the place to plug it in.

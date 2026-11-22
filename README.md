# Lantern

*A light on where your money goes.*

Lantern is a small, self-hosted personal finance tracker for your home network. Python, [NiceGUI](https://nicegui.io) for the whole UI, SQLite for storage, Plotly for charts.

- Multiple named users, each with their own transactions and categories (no passwords). Each device is asked which user to view the first time it visits (skipped when there is only one user)
- Two-level categories (category > subcategory), income and expense, in an order you choose
- Everything is recorded in one base currency; view it in any currency Frankfurter publishes rates for
- Overview page with summary cards, a colour-scaled month x category table, drill-down charts and an income vs expenses chart
- Import transactions from a bank CSV, export your data as CSV, and back up or restore the whole database
- Runs directly with Python or in a Docker container

## Pages

| Page | Route | What it does |
| --- | --- | --- |
| Overview | `/` (also `/overview`) | Summary cards, month x category table, stacked monthly bars, income vs expenses, share pie |
| Add Transactions | `/transactions` | Add, filter, edit and delete transactions |
| Edit Categories | `/categories` | Add, rename, reorder and delete categories and subcategories |
| Export CSV | `/export` | Download transactions and/or month x category totals |
| Import CSV | `/import` | Load a bank CSV with automatic category matching |
| Backups | `/backups` | Save, download, upload, restore and delete database backups |

## Requirements

- Python 3.11 or newer (or just Docker, see below)
- Internet access is only needed for exchange rates. Without it the app still works, using cached rates or just the base currency.

## Install and run

```bash
cd lantern

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

python main.py
```

Then open <http://localhost:8080>.

On first run the app asks for your **base currency** (default GBP) and the name of the first user. Pick the base currency carefully: every amount is stored in it, so changing it later would change what your recorded numbers mean.

When there is more than one user, each new device or browser shows a **"Who is using the tracker on this device?"** card on its first visit. Click your name and the choice is remembered on that device. With only one user this step is skipped.

### Try it with demo data

```bash
python main.py --seed-demo
```

This creates a user called **Demo** with about three years of random transactions so you can see the charts. It does nothing if Demo already has transactions.

## Run with Docker

The repository includes a `Dockerfile`, a `docker-compose.yml` and a `.dockerignore`.

```bash
docker compose up -d --build
```

Then open `http://<server-address>:8080`.

- **Data lives in a volume.** The database, the backups folder and the per-browser storage are all kept in `/data` inside the container, which docker-compose maps to the named volume `finance-data`. Rebuilding or recreating the container does not touch your data.
- **Time zone.** Set `TZ` so "today" and the current year match your local time (default `Europe/London`): `TZ=Europe/Paris docker compose up -d`, or edit `docker-compose.yml`.
- **Port.** To use a different host port, change the left side of the `ports` entry (for example `"9000:8080"`).
- **Logs:** `docker compose logs -f finance-tracker`
- **Stop:** `docker compose down` (add `-v` only if you want to delete the data volume too).
- **Update after changing the code:** `docker compose up -d --build`

Without Compose:

```bash
docker build -t finance-tracker .
docker run -d --name finance-tracker --restart unless-stopped \
  -p 8080:8080 -e TZ=Europe/London -v finance-data:/data finance-tracker
```

Demo data in a container (stop it with Ctrl+C once it logs that the demo data is ready):

```bash
docker compose run --rm finance-tracker python main.py --seed-demo
```

If you prefer a folder on the host instead of a named volume, use a bind mount such as `./data:/data` and make it writable by user ID 1000 (`sudo chown -R 1000:1000 ./data`), because the container does not run as root.

### Backups with Docker

The Backups page saves into `/data/backups` in the volume. To copy them to the host:

```bash
docker compose cp finance-tracker:/data/backups ./backups
```

You can also make a backup from the command line:

```bash
docker compose exec finance-tracker python -c "import db; print(db.create_backup())"
```

## Open it from another device on your network

The server listens on all interfaces (`0.0.0.0`), so phones, tablets and other computers on the same network can use it.

1. Start the app. The log prints a line such as `From another device on your network: http://192.168.1.20:8080`. (Inside Docker this shows the container's address, so use the host machine's address instead.)
2. Open that address on the other device.
3. If it does not load, allow incoming connections to the port in the host machine's firewall (for example `sudo ufw allow 8080/tcp` on Ubuntu).

**There is no login.** Anyone who can reach the port can see and change everything, so only run it on a network you trust and do not expose the port to the internet. On a server, a good setup is to bind the port to localhost (`"127.0.0.1:8080:8080"` in `docker-compose.yml`) and put a reverse proxy with authentication (or a VPN) in front.

## Configuration

| Setting | How | Default |
| --- | --- | --- |
| Port | `--port 9000` or `FINANCE_PORT=9000` | `8080` |
| Database file | `FINANCE_DB_PATH=/path/to/finance.db` | `finance.db` next to `main.py` (`/data/finance.db` in Docker) |
| Backup folder | `FINANCE_BACKUP_DIR=/path/to/backups` | `backups/` next to `main.py` (`/data/backups` in Docker) |
| Per-browser storage folder | `NICEGUI_STORAGE_PATH=/path/to/.nicegui` | `.nicegui` next to `main.py` (`/data/.nicegui` in Docker) |
| Storage secret | `FINANCE_STORAGE_SECRET=...` | generated once and kept in the database |
| Time zone (Docker) | `TZ=Europe/London` | `Europe/London` in `docker-compose.yml` |

Each browser remembers its own selected user, display currency and dark-mode choice. A device you have not used before (or whose cookies were cleared) is asked to pick a user again.

## Using the app

### Overview

- Pick a **year**, an optional **category filter**, **Category/Subcategory** level and **Expenses/Income**.
- **Summary cards:** total income, total expenses, net and savings rate for the year.
- **Month x category table:** each row is colour-scaled around its own monthly average (for spending, green is low and red is high; for income the scale is reversed). Rows follow the category order from the Categories page. It also has Total income, Total expenses and Net rows at the top and bottom, and a Total column that is scaled down the column. Months that have not happened yet in the current year are not coloured.
- **Charts:** stacked monthly bars and a share pie (small slices under 3% are merged into "Other"). Click a bar segment or pie slice to drill into that category. An income vs expenses chart with a net line is shown too.

### Add Transactions

Type the amount as a positive number and use the Income/Expense toggle to set the sign. Amounts are entered in the base currency; the table shows the current display currency. If you don't choose a subcategory, the transaction goes into that category's **Other** subcategory. You can filter by date, category (including its subcategories) and description, and edit or delete rows from the table.

### Edit Categories

- Add top-level categories (income or expense) and subcategories (two levels maximum; subcategories take their parent's type).
- **Reorder** with the up/down arrows. This order is used in the dropdowns, the Overview table and the CSV export.
- Every category has a built-in **Other** subcategory, created the first time it is needed. It can't be renamed, moved or deleted on its own and always sits last.
- New users start with a default set of categories.
- A category can't be deleted while it has subcategories. If it has transactions you choose another category of the same type to move them to first.

### Export CSV

Download either or both of:

- **Transactions:** every transaction, oldest first, one row each.
- **Month x category totals:** one row per category and one column per month across all years, plus a Total column. Choose subcategory or category rows.

Amounts can be in the base currency or the current display currency. They are plain signed numbers with no symbol, and the files are UTF-8 with a BOM so Excel opens them correctly. If you tick both, two files download.

### Import CSV

1. Choose a CSV file (comma, semicolon or tab separated).
2. Check the guessed **date, amount, description and optional category columns** and the date format. Use **Flip sign** if your bank shows spending as positive.
3. **Preview.** Categories are matched from a category column in the file (if you pick one), then from descriptions you have used before (exact match first, then a known description appearing inside the new one, such as "Tesco" inside "TESCO STORES 3214"). Rows with no match are listed so you can pick a category for each or tick **Skip**; choosing one also fills other empty rows with the same description.
4. **Import.** With **Skip existing** on (the default), rows whose date, amount and category match a transaction already in the database are ignored. Rows within the file are never treated as duplicates of each other. Unreadable rows are listed and ignored.

Amounts in the file are read as the base currency. The import is all-or-nothing: if a row is invalid, nothing is added.

### Backups

- **Back up now** saves a complete copy of the database as `finance-YYYYMMDD-HHMMSS.db`, using SQLite's backup API, which is safe while the app is running.
- **Download** or **Delete** any backup, or **Add a backup file (.db)** to upload one. Uploaded files are checked first and kept as `uploaded-<name>.db`.
- **Restore** replaces the data of **all users** with the chosen backup. It first saves your current data as `pre-restore-<timestamp>.db`, so a restore can itself be undone. The signing secret is kept so open browsers stay on their chosen user and currency (a browser whose user is not in the restored backup is asked to pick again), and older backups are upgraded to the current schema.

## How it works

- **Amounts** are stored as signed integer minor units (pence/cents) in the base currency: income is positive, spending is negative. A category's type must match the sign of its transactions.
- **Choosing a user:** the active user is kept per browser in NiceGUI's `app.storage.user`, which is tied to a signed cookie. If the browser has no valid stored user and there are several users, `build_layout()` shows the user picker instead of the page. With exactly one user, that user is selected automatically. You can switch user at any time from the dropdown in the header, and the picker reappears if the stored user no longer exists (for example after restoring a backup without them).
- **Currency display:** the selector in the header converts amounts at display time only. Stored data is never changed. Amounts you type into the form, import from a file or export in base currency are always in the base currency.
- **Exchange rates** come from the free [Frankfurter](https://www.frankfurter.app) API and are cached in the database for 24 hours. If a refresh fails, the cached rates are used and a warning shows when they were last updated. After a failure it waits 10 minutes before trying again.
- **Database upgrades:** `db.init_db()` creates missing tables and then runs small upgrades for databases from older versions (adding category ordering, and moving any transactions filed directly on a top-level category into its Other subcategory).

## Project structure

```
lantern/
  main.py            entry point: options, route registration, ui.run()
  db.py              connection handling, schema, ALL SQL queries, backups, demo data
  rates.py           exchange-rate fetching/caching, money parsing and formatting
  state.py           active user (auto-selected only if there is one user) and display currency (per browser)
  components.py      page frame: header, navigation, user/currency pickers, first-run setup, first-visit user picker, dialogs
  pages/
    overview.py      cards, charts, month x category table     (routes: / and /overview)
    transactions.py  add/edit/delete form, filters and table   (route: /transactions)
    categories.py    manage and reorder categories             (route: /categories)
    export.py        CSV export                                (route: /export)
    import_csv.py    CSV import with category matching         (route: /import)
    backups.py       save, download, upload, restore backups   (route: /backups)
  requirements.txt
  Dockerfile
  docker-compose.yml
  .dockerignore
  README.md
```

Conventions worth knowing when you change things:

- All SQL lives in `db.py`. Pages call its functions and never write SQL.
- Problems a user can fix raise `db.DataError` with a friendly message; pages show it with `components.notify_error`.
- Every page starts with `ctx = await components.build_layout("Name")` and puts its content inside `with components.content():`.
- Money is always formatted through `rates.format_money` (usually via `ctx.display.money(cents)`).
- To add a page, create it in `pages/`, import it in `main.py` so its route registers, and add it to `NAV_LINKS` in `components.py`.

## Extending the app later

- **Yearly trends:** `db.yearly_totals` already returns income and expenses per year (optionally for one category). The Overview has a "Add category trend line" selector waiting for a chart that uses it.
- **Multiple accounts:** add an `accounts` table (`id`, `user_id`, `name`) and an `account_id` column on `transactions`. Add an account dropdown to the form and an account filter to the transactions and overview pages. A transfer is two transactions, one on each account.
- **Automatic backups:** call `db.create_backup()` from a `ui.timer` (or `app.on_startup`) and prune old files. Manual backups already exist on the Backups page.
- **Budgets:** a `budgets(user_id, category_id, month, amount_cents)` table plus a progress bar per category on the overview page.
- **Recurring transactions:** a `recurring` table checked at startup, creating any transactions that have come due.
- **Schema changes:** add steps to `db._migrate()` (it checks for missing columns before altering tables). For bigger changes, consider a `PRAGMA user_version` check or a migration tool such as Alembic.
- **Passwords:** if you ever need to protect it, NiceGUI's docs include a simple login example that keeps the signed-in user in `app.storage.user`; `state.py` is the place to plug it in.
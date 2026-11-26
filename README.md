# Lantern

*A light on where your money goes.*

Lantern is a small, self-hosted personal finance tracker for your home network. Built with Python, [NiceGUI](https://nicegui.io), SQLite and Plotly.

- Multiple users, each with their own password, transactions and categories
- Two-level categories (income and expense) in an order you choose
- Recurring transactions that wait in a review list for you to accept or reject
- Shared expenses: split a cost between users, track running balances and settle up
- Overview page with summary cards, a colour-scaled month x category table and charts
- Import a bank CSV, export your data, and back up or restore the database
- View amounts in any currency (exchange rates from [Frankfurter](https://www.frankfurter.app))

<img src="docs/overview.png" width="700" alt="Lantern's Overview page">

## Run with Docker

Run these from the `docker/` folder:

```bash
cd docker
docker compose up -d --build
```

Then open `http://<server-address>:8080`.

- Your data lives in the `finance-data` volume, so rebuilding never touches it.
- Set your time zone with `TZ`, e.g. `TZ=Europe/Paris docker compose up -d` (default `Europe/London`).
- Logs: `docker compose logs -f lantern-finance-tracker`
- Stop: `docker compose down` (add `-v` to also delete your data)
- Forgot a password: `docker compose exec lantern-finance-tracker python main.py --reset-password NAME`

## Run with Python

Needs Python 3.11+.

```bash
pip install -r requirements.txt
python main.py
```

Then open <http://localhost:8080>. To try it with sample data first, run `python main.py --seed-demo`.

## First run

The app asks for your **base currency** (default GBP) and the name and password of the first user. Choose the currency carefully: every amount is stored in it. Any logged-in user can add more users from the header, and each device stays logged in until you log out.

To use it from another device, open `http://<host-address>:8080`. If it doesn't load, allow port 8080 through the host's firewall.

**Security:** passwords protect your data, but the connection is plain HTTP. Only use Lantern on a network you trust and don't expose it to the internet. For a server, bind to localhost (`"127.0.0.1:8080:8080"` in `docker/docker-compose.yml`) and put an HTTPS reverse proxy or VPN in front.

## Using the app

| Page | What it does |
| --- | --- |
| **Overview** | Summary cards, month x category table and charts. Pick a year (or all years), filter by category, and click a bar or pie slice to drill in |
| **Add Transactions** | Add, filter, edit and delete transactions. Also where recurring rules and items waiting for your review live |
| **Shared Balances** | Who owes whom, settle up, and the list of shared transactions |
| **Other functions** | Edit Categories, Export CSV, Import CSV and Backups |

**Adding transactions.** Enter a positive amount and use the Income/Expense toggle. If you skip the subcategory, it goes into that category's *Other*.

**Recurring transactions.** Create a rule (every N days, weeks or months). Each due date appears in a review list on the Transactions page, where you accept (optionally changing the amount) or reject it.

**Sharing an expense.** Turn on *Share with other users*, then set who paid and who owes what, by amount or percentage. The others accept it into their own categories or reject it. Balances update automatically and *Mark as settled* clears one.

**Importing a CSV.** Upload a bank export, check the guessed columns, preview, then import. Categories are matched from descriptions you've used before, and duplicates are skipped by default.

**Backups.** *Back up now* saves a copy of the database. *Restore* replaces all users' data, after first saving a `pre-restore` copy so you can undo it. Backups contain password hashes, so keep them private.

## Configuration

| Setting | How | Default |
| --- | --- | --- |
| Port | `--port 9000` or `FINANCE_PORT` | `8080` |
| Database file | `FINANCE_DB_PATH` | `finance.db` (`/data/finance.db` in Docker) |
| Backup folder | `FINANCE_BACKUP_DIR` | `backups/` (`/data/backups` in Docker) |
| Browser storage folder | `NICEGUI_STORAGE_PATH` | `.nicegui` (`/data/.nicegui` in Docker) |
| Storage secret | `FINANCE_STORAGE_SECRET` | generated and kept in the database |

To reset a password without Docker: `python main.py --reset-password NAME`.

## Development

```
main.py        entry point
db/            all SQL and business logic
web_app/       the NiceGUI interface (pages/ holds one module per route)
tests/         pytest tests (run with `pytest`)
docker/        Dockerfile and docker-compose.yml
```

## License

MIT. See [LICENSE](LICENSE).
"""Database package.

Import the module you need, e.g. ``from db import transactions, categories``. Nothing is
re-exported here except ``DataError`` (which nearly every caller catches).

Modules
-------
core        connection handling, DataError, row helpers
schema      SCHEMA, migrations, init_db()
settings    key/value settings and the base currency
users       users and passwords
cats        categories and subcategories
txns        transactions and CSV-import helpers
recurring   recurring rules and pending items
shared      shared transactions, balances, settlements
reports     overview-page aggregations
rate_cache  exchange-rate cache table
backups     backup files and restore
demo        demo data
"""
from db.core import DataError

__all__ = ["DataError"]

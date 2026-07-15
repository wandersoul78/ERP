# Ledger — personal accounts + inventory

A small local web app for double-entry bookkeeping with inventory tracking.
Runs on your machine, stores data in a local SQLite file.

## Run it

Requires Python 3 and Flask.

```bash
pip install flask --break-system-packages   # or: pip install flask
python3 app.py
```

Open **http://127.0.0.1:5050** in your browser. Data is stored in `ledger.db`
in this folder — delete that file any time to start fresh (default accounts
Cash, Bank, Sales, Purchases, Capital are recreated automatically).

## What's in it

- **Accounts** — add your own ledgers (customers, suppliers, expense heads,
  bank accounts, etc.) with opening balances.
- **Items** — inventory items with opening quantity and rate.
- **New Voucher** — record Sales / Purchase / Payment / Receipt / Journal
  entries. Debit and credit lines must balance before saving. For Sales and
  Purchase vouchers you can also add item lines (stock in/out) — inventory
  and reports update automatically.
- **Reports**
  - *Ledger* — statement for any single account with running balance.
  - *Profit & Loss* — income vs expense accounts, computed live from vouchers.
  - *Stock summary* — opening/in/out/closing quantity per item, valued at
    weighted-average cost.

## How it's structured (if you want to extend it)

- `app.py` — Flask backend, all logic and the `/api/*` routes.
- `static/index.html` — the entire frontend (vanilla JS, no build step).
- `ledger.db` — SQLite file, created on first run.

Everything is voucher-driven: a voucher has one or more debit/credit ledger
entries (which must sum to zero) and, optionally, item movement lines. All
reports are just aggregations over these two tables — there's no separate
"balance" field stored anywhere, so the numbers can't drift out of sync.

## Adding cloud sync later

This version is local-only by design, so you can try the workflow risk-free
first. The natural next step for "data in the cloud" is:

1. Point the same schema at a hosted Postgres (e.g. Supabase or Neon).
2. Keep this SQLite file as an offline cache — write here first, then push
   new rows to Postgres in the background.
3. On startup, pull anything newer from the cloud than what's local.

Since this is single-user, you don't need real-time conflict resolution —
"last write wins" per voucher is enough. Happy to build that sync layer next
once you've used the local version for a bit and know what you actually need.

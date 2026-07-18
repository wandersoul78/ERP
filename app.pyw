import os
import sys
import threading
from datetime import date
from flask import Flask, g, jsonify, request, send_from_directory
import webview
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("SUPABASE_DB_URL")

class FakeSqlite3:
    IntegrityError = psycopg2.IntegrityError
    Error = psycopg2.Error
sqlite3 = FakeSqlite3()

def parse_float(val, default=0.0):
    if val is None:
        return default
    s = str(val).replace(",", "").strip()
    if not s:
        return default
    return float(s)


class CursorWrapper:
    def __init__(self, cur):
        self.cur = cur
        self._lastrowid = None

    def execute(self, sql, params=None):
        sql = sql.replace('?', '%s')
        is_insert = sql.strip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in sql.upper():
            sql = sql.rstrip('; ') + " RETURNING id"
            self.cur.execute(sql, params)
            try:
                row = self.cur.fetchone()
                if row:
                    if isinstance(row, dict):
                        self._lastrowid = list(row.values())[0]
                    else:
                        self._lastrowid = row[0]
            except Exception:
                pass
        else:
            self.cur.execute(sql, params)
        return self

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    @property
    def lastrowid(self):
        return self._lastrowid

    def __iter__(self):
        return iter(self.cur)

class DbWrapper:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.conn.__exit__(exc_type, exc_val, exc_tb)

    def execute(self, sql, params=None):
        sql = sql.replace('?', '%s')
        cur = self.conn.cursor()
        wrapped = CursorWrapper(cur)
        wrapped.execute(sql, params)
        return wrapped

    def executemany(self, sql, seq_of_params):
        sql = sql.replace('?', '%s')
        cur = self.conn.cursor()
        cur.executemany(sql, seq_of_params)
        return CursorWrapper(cur)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CREDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "service_account.json")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

ACCOUNT_TYPES = ("asset", "liability", "income", "expense", "equity")
VOUCHER_TYPES = ("sales", "purchase", "payment", "receipt", "journal", "production")

# ── Google Sheets sync (optional — only active when service_account.json exists) ──
_sheets = None

def _trigger_sync():
    """Schedule an immediate Sheets sync after a DB write."""
    if _sheets:
        _sheets.trigger()

@app.after_request
def _after_write(response):
    """Auto-trigger Sheets sync after any successful mutating request."""
    if request.method in ("POST", "PUT", "DELETE") and response.status_code < 300:
        _trigger_sync()
    return response

def get_db():
    if "db" not in g:
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = RealDictCursor
        g.db = DbWrapper(conn)
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK(type IN ('asset','liability','income','expense','equity')),
    opening_balance REAL NOT NULL DEFAULT 0,
    opening_side TEXT NOT NULL DEFAULT 'debit' CHECK(opening_side IN ('debit','credit'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL DEFAULT 'pcs',
    opening_qty REAL NOT NULL DEFAULT 0,
    opening_rate REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vouchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('sales','purchase','payment','receipt','journal','production')),
    reference TEXT DEFAULT '',
    narration TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS voucher_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    debit REAL NOT NULL DEFAULT 0,
    credit REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS voucher_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES items(id),
    direction TEXT NOT NULL CHECK(direction IN ('in','out')),
    qty REAL NOT NULL,
    rate REAL NOT NULL,
    gst_rate REAL NOT NULL DEFAULT 0
);
"""


def init_db():
    pass


# ---------- static ----------

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "company_name": os.getenv("COMPANY_NAME") or "Ledger",
        "password_protected": bool(os.getenv("APP_PASSWORD"))
    })


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    pwd = data.get("password")
    expected = os.getenv("APP_PASSWORD")
    if not expected:
        return jsonify({"ok": True})
    if pwd == expected:
        return jsonify({"ok": True})
    return jsonify({"error": "Incorrect password"}), 401


# ---------- accounts ----------

@app.route("/api/accounts", methods=["GET"])
def list_accounts():
    db = get_db()
    rows = db.execute("SELECT * FROM accounts ORDER BY type, name").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/accounts", methods=["POST"])
def create_account():
    try:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        acc_type = data.get("type")
        try:
            opening_balance = parse_float(data.get("opening_balance") or 0)
        except ValueError:
            return jsonify({"error": "Opening balance must be a valid number"}), 400
        opening_side = data.get("opening_side") or "debit"
        if not name or acc_type not in ACCOUNT_TYPES:
            return jsonify({"error": "name and a valid type are required"}), 400
        db = get_db()
        with db:
            cur = db.execute(
                "INSERT INTO accounts (name, type, opening_balance, opening_side) VALUES (?,?,?,?)",
                (name, acc_type, opening_balance, opening_side),
            )
    except sqlite3.IntegrityError:
        return jsonify({"error": "an account with this name already exists"}), 400
    except sqlite3.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    except Exception as err:
        return jsonify({"error": f"Error: {err}"}), 500
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    db = get_db()
    used = db.execute(
        "SELECT COUNT(*) c FROM voucher_entries WHERE account_id=?", (account_id,)
    ).fetchone()["c"]
    if used:
        return jsonify({"error": "cannot delete an account that has voucher entries"}), 400
    db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/accounts/<int:account_id>", methods=["PUT"])
def update_account(account_id):
    try:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        acc_type = data.get("type")
        try:
            opening_balance = parse_float(data.get("opening_balance") or 0)
        except ValueError:
            return jsonify({"error": "Opening balance must be a valid number"}), 400
        opening_side = data.get("opening_side") or "debit"
        if not name or acc_type not in ACCOUNT_TYPES:
            return jsonify({"error": "name and a valid type are required"}), 400
        db = get_db()
        with db:
            db.execute(
                "UPDATE accounts SET name=?, type=?, opening_balance=?, opening_side=? WHERE id=?",
                (name, acc_type, opening_balance, opening_side, account_id),
            )
    except sqlite3.IntegrityError:
        return jsonify({"error": "an account with this name already exists"}), 400
    except sqlite3.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    except Exception as err:
        return jsonify({"error": f"Error: {err}"}), 500
    return jsonify({"ok": True})


# ---------- items ----------

@app.route("/api/items", methods=["GET"])
def list_items():
    db = get_db()
    rows = db.execute("SELECT * FROM items ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/items", methods=["POST"])
def create_item():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    unit = (data.get("unit") or "pcs").strip()
    opening_qty = float(data.get("opening_qty") or 0)
    opening_rate = float(data.get("opening_rate") or 0)
    if not name:
        return jsonify({"error": "name is required"}), 400
    db = get_db()
    try:
        with db:
            cur = db.execute(
                "INSERT INTO items (name, unit, opening_qty, opening_rate) VALUES (?,?,?,?)",
                (name, unit, opening_qty, opening_rate),
            )
    except sqlite3.IntegrityError:
        return jsonify({"error": "an item with this name already exists"}), 400
    except sqlite3.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    db = get_db()
    used = db.execute(
        "SELECT COUNT(*) c FROM voucher_items WHERE item_id=?", (item_id,)
    ).fetchone()["c"]
    if used:
        return jsonify({"error": "cannot delete an item that has voucher movements"}), 400
    db.execute("DELETE FROM items WHERE id=?", (item_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/items/<int:item_id>", methods=["PUT"])
def update_item(item_id):
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    unit = (data.get("unit") or "pcs").strip()
    opening_qty = float(data.get("opening_qty") or 0)
    opening_rate = float(data.get("opening_rate") or 0)
    if not name:
        return jsonify({"error": "name is required"}), 400
    db = get_db()
    try:
        with db:
            db.execute(
                "UPDATE items SET name=?, unit=?, opening_qty=?, opening_rate=? WHERE id=?",
                (name, unit, opening_qty, opening_rate, item_id),
            )
    except sqlite3.IntegrityError:
        return jsonify({"error": "an item with this name already exists"}), 400
    except sqlite3.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    return jsonify({"ok": True})


# ---------- vouchers ----------

@app.route("/api/vouchers", methods=["GET"])
def list_vouchers():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM vouchers ORDER BY date DESC, id DESC LIMIT 200"
    ).fetchall()
    result = []
    for v in rows:
        entries = db.execute(
            """SELECT ve.*, a.name as account_name FROM voucher_entries ve
               JOIN accounts a ON a.id = ve.account_id WHERE ve.voucher_id=?""",
            (v["id"],),
        ).fetchall()
        items = db.execute(
            """SELECT vi.*, i.name as item_name, i.unit as item_unit FROM voucher_items vi
               JOIN items i ON i.id = vi.item_id WHERE vi.voucher_id=?""",
            (v["id"],),
        ).fetchall()
        result.append({
            **dict(v),
            "entries": [dict(e) for e in entries],
            "items": [dict(i) for i in items],
        })
    return jsonify(result)


@app.route("/api/vouchers", methods=["POST"])
def create_voucher():
    data = request.get_json(force=True)
    v_date = data.get("date") or str(date.today())
    v_type = data.get("type")
    reference = (data.get("reference") or "").strip()
    narration = data.get("narration") or ""
    entries = data.get("entries") or []
    items = data.get("items") or []

    if v_type not in VOUCHER_TYPES:
        return jsonify({"error": "invalid voucher type"}), 400

    if v_type == "production":
        if len(items) == 0:
            return jsonify({"error": "Production vouchers require at least one inventory entry"}), 400
        entries = []
    else:
        if len(entries) < 2:
            return jsonify({"error": "at least two ledger entries (one debit, one credit) are required"}), 400

        # Bug fix #3: reject entries that have both debit and credit filled
        for e in entries:
            e_debit = float(e.get("debit") or 0)
            e_credit = float(e.get("credit") or 0)
            if e_debit > 0 and e_credit > 0:
                return jsonify({
                    "error": "each ledger entry must be either a debit or a credit — not both"
                }), 400

        total_debit = sum(float(e.get("debit") or 0) for e in entries)
        total_credit = sum(float(e.get("credit") or 0) for e in entries)
        if round(total_debit, 2) != round(total_credit, 2):
            return jsonify({
                "error": f"entries don't balance: total debit {total_debit:.2f} vs total credit {total_credit:.2f}"
            }), 400
        if total_debit == 0:
            return jsonify({"error": "voucher amount cannot be zero"}), 400

    for it in items:
        if it.get("direction") not in ("in", "out"):
            return jsonify({"error": "each item line needs a direction of in or out"}), 400
        if float(it.get("qty") or 0) <= 0:
            return jsonify({"error": "item quantity must be greater than zero"}), 400

    # Bug fix #1: wrap all inserts in a single atomic transaction
    # Bug fix #2: catch sqlite3.Error so any DB failure returns clean JSON (not an HTML 500 trace)
    db = get_db()
    try:
        with db:
            cur = db.execute(
                "INSERT INTO vouchers (date, type, reference, narration) VALUES (?,?,?,?)",
                (v_date, v_type, reference, narration),
            )
            voucher_id = cur.lastrowid
            for e in entries:
                db.execute(
                    "INSERT INTO voucher_entries (voucher_id, account_id, debit, credit) VALUES (?,?,?,?)",
                    (voucher_id, e["account_id"], float(e.get("debit") or 0), float(e.get("credit") or 0)),
                )
            for it in items:
                db.execute(
                    "INSERT INTO voucher_items (voucher_id, item_id, direction, qty, rate, gst_rate) VALUES (?,?,?,?,?,?)",
                    (voucher_id, it["item_id"], it["direction"], float(it["qty"]), float(it.get("rate") or 0), float(it.get("gst_rate") or 0)),
                )
    except sqlite3.IntegrityError as err:
        return jsonify({"error": f"Invalid reference in entries or items: {err}"}), 400
    except sqlite3.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    return jsonify({"id": voucher_id}), 201


@app.route("/api/vouchers/<int:voucher_id>", methods=["DELETE"])
def delete_voucher(voucher_id):
    db = get_db()
    db.execute("DELETE FROM vouchers WHERE id=?", (voucher_id,))
    db.commit()
    return jsonify({"ok": True})


def _validate_and_write_voucher(db, voucher_id, data):
    """Shared validation + write logic for both POST (insert) and PUT (update)."""
    v_date   = data.get("date") or str(date.today())
    v_type   = data.get("type")
    reference = (data.get("reference") or "").strip()
    narration = data.get("narration") or ""
    entries  = data.get("entries") or []
    items    = data.get("items") or []

    if v_type not in VOUCHER_TYPES:
        return None, (jsonify({"error": "invalid voucher type"}), 400)

    if v_type == "production":
        if len(items) == 0:
            return None, (jsonify({"error": "Production vouchers require at least one inventory entry"}), 400)
        entries = []
    else:
        if len(entries) < 2:
            return None, (jsonify({"error": "at least two ledger entries (one debit, one credit) are required"}), 400)
        for e in entries:
            if float(e.get("debit") or 0) > 0 and float(e.get("credit") or 0) > 0:
                return None, (jsonify({"error": "each ledger entry must be either a debit or a credit — not both"}), 400)
        total_debit  = sum(float(e.get("debit")  or 0) for e in entries)
        total_credit = sum(float(e.get("credit") or 0) for e in entries)
        if round(total_debit, 2) != round(total_credit, 2):
            return None, (jsonify({"error": f"entries don't balance: total debit {total_debit:.2f} vs total credit {total_credit:.2f}"}), 400)
        if total_debit == 0:
            return None, (jsonify({"error": "voucher amount cannot be zero"}), 400)

    for it in items:
        if it.get("direction") not in ("in", "out"):
            return None, (jsonify({"error": "each item line needs a direction of in or out"}), 400)
        if float(it.get("qty") or 0) <= 0:
            return None, (jsonify({"error": "item quantity must be greater than zero"}), 400)

    return (v_date, v_type, reference, narration, entries, items), None


@app.route("/api/vouchers/<int:voucher_id>", methods=["PUT"])
def update_voucher(voucher_id):
    data = request.get_json(force=True)
    db = get_db()
    existing = db.execute("SELECT id FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    if not existing:
        return jsonify({"error": "voucher not found"}), 404

    parsed, err = _validate_and_write_voucher(db, voucher_id, data)
    if err:
        return err
    v_date, v_type, reference, narration, entries, items = parsed

    try:
        with db:
            db.execute(
                "UPDATE vouchers SET date=?, type=?, reference=?, narration=? WHERE id=?",
                (v_date, v_type, reference, narration, voucher_id),
            )
            db.execute("DELETE FROM voucher_entries WHERE voucher_id=?", (voucher_id,))
            db.execute("DELETE FROM voucher_items   WHERE voucher_id=?", (voucher_id,))
            for e in entries:
                db.execute(
                    "INSERT INTO voucher_entries (voucher_id, account_id, debit, credit) VALUES (?,?,?,?)",
                    (voucher_id, e["account_id"], float(e.get("debit") or 0), float(e.get("credit") or 0)),
                )
            for it in items:
                db.execute(
                    "INSERT INTO voucher_items (voucher_id, item_id, direction, qty, rate, gst_rate) VALUES (?,?,?,?,?,?)",
                    (voucher_id, it["item_id"], it["direction"], float(it["qty"]), float(it.get("rate") or 0), float(it.get("gst_rate") or 0)),
                )
    except sqlite3.IntegrityError as err:
        return jsonify({"error": f"Invalid reference in entries or items: {err}"}), 400
    except sqlite3.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    return jsonify({"ok": True})


# ---------- reports ----------

@app.route("/api/reports/ledger/<int:account_id>")
def report_ledger(account_id):
    db = get_db()
    acc = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not acc:
        return jsonify({"error": "account not found"}), 404

    from_date = request.args.get("from") or None
    to_date = request.args.get("to") or None

    is_debit_normal = acc["type"] in ("asset", "expense")
    natural_side = "debit" if is_debit_normal else "credit"
    base_opening = acc["opening_balance"] if acc["opening_side"] == natural_side else -acc["opening_balance"]

    # movement strictly before the 'from' date rolls into the opening balance
    pre_delta = 0
    if from_date:
        pre = db.execute(
            """SELECT COALESCE(SUM(ve.debit),0) d, COALESCE(SUM(ve.credit),0) c
               FROM voucher_entries ve JOIN vouchers v ON v.id = ve.voucher_id
               WHERE ve.account_id = ? AND v.date < ?""",
            (account_id, from_date),
        ).fetchone()
        pre_delta = (pre["d"] - pre["c"]) if is_debit_normal else (pre["c"] - pre["d"])

    balance = base_opening + pre_delta

    clauses = ["ve.account_id = ?"]
    params = [account_id]
    if from_date:
        clauses.append("v.date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("v.date <= ?")
        params.append(to_date)
    where = " AND ".join(clauses)

    rows = db.execute(
        f"""SELECT v.date, v.type, v.narration, v.reference, ve.debit, ve.credit, v.id as voucher_id
           FROM voucher_entries ve JOIN vouchers v ON v.id = ve.voucher_id
           WHERE {where}
           ORDER BY v.date, v.id""",
        params,
    ).fetchall()

    ledger = []
    running = balance
    for r in rows:
        delta = (r["debit"] - r["credit"]) if is_debit_normal else (r["credit"] - r["debit"])
        running += delta
        ledger.append({
            "date": r["date"], "type": r["type"], "narration": r["narration"], "reference": r["reference"],
            "voucher_id": r["voucher_id"], "debit": r["debit"], "credit": r["credit"],
            "balance": round(running, 2),
        })

    v_ids = [r["voucher_id"] for r in rows]
    unit_qty_map = {}
    if v_ids:
        placeholders = ",".join(["?"] * len(v_ids))
        items_moved = db.execute(
            f"""SELECT vi.qty, i.unit FROM voucher_items vi JOIN items i ON i.id = vi.item_id WHERE vi.voucher_id IN ({placeholders})""",
            v_ids
        ).fetchall()
        for im in items_moved:
            u = im["unit"] or "pcs"
            unit_qty_map[u] = unit_qty_map.get(u, 0.0) + im["qty"]

    tot_qty_str = ", ".join([f"{round(q, 2):,.2f} {u}" for u, q in unit_qty_map.items()]) if unit_qty_map else ""

    return jsonify({
        "account": dict(acc),
        "opening_balance": round(balance, 2),
        "closing_balance": round(running, 2),
        "total_qty": tot_qty_str,
        "entries": ledger,
    })


@app.route("/api/reports/pl")
def report_pl():
    db = get_db()
    from_date = request.args.get("from") or None
    to_date = request.args.get("to") or None

    date_clauses = []
    params = []
    if from_date:
        date_clauses.append("v.date >= ?")
        params.append(from_date)
    if to_date:
        date_clauses.append("v.date <= ?")
        params.append(to_date)
    date_where = (" AND " + " AND ".join(date_clauses)) if date_clauses else ""
    # a date-bounded view is a period P&L; only an all-time view rolls in the
    # account's opening balance (a P&L for e.g. "this month" shouldn't include it)
    include_opening = not from_date

    income_rows = db.execute(
        f"""SELECT a.id, a.name, a.opening_balance, a.opening_side,
                  (SELECT COALESCE(SUM(ve.credit),0) - COALESCE(SUM(ve.debit),0)
                   FROM voucher_entries ve JOIN vouchers v ON v.id = ve.voucher_id
                   WHERE ve.account_id = a.id {date_where}) as movement
           FROM accounts a WHERE a.type='income' ORDER BY a.name""",
        params,
    ).fetchall()
    expense_rows = db.execute(
        f"""SELECT a.id, a.name, a.opening_balance, a.opening_side,
                  (SELECT COALESCE(SUM(ve.debit),0) - COALESCE(SUM(ve.credit),0)
                   FROM voucher_entries ve JOIN vouchers v ON v.id = ve.voucher_id
                   WHERE ve.account_id = a.id {date_where}) as movement
           FROM accounts a WHERE a.type='expense' ORDER BY a.name""",
        params,
    ).fetchall()

    def line(r, natural_side):
        opening = 0
        if include_opening:
            opening = r["opening_balance"] if r["opening_side"] == natural_side else -r["opening_balance"]
        total = round(opening + (r["movement"] or 0), 2)
        return {"id": r["id"], "name": r["name"], "amount": total}

    income = [line(r, "credit") for r in income_rows]
    expense = [line(r, "debit") for r in expense_rows]
    total_income = round(sum(i["amount"] for i in income), 2)
    total_expense = round(sum(e["amount"] for e in expense), 2)

    return jsonify({
        "income": income,
        "expense": expense,
        "total_income": total_income,
        "total_expense": total_expense,
        "net_profit": round(total_income - total_expense, 2),
    })


@app.route("/api/reports/bs")
def report_bs():
    """Balance Sheet: snapshot of all asset/liability/equity account balances."""
    db = get_db()
    as_of = request.args.get("to") or None

    def account_balance(acc_id, acc_type, ob, ob_side):
        """Net balance of an account up to as_of date (or all-time)."""
        is_debit_normal = acc_type in ("asset", "expense")
        natural_side = "debit" if is_debit_normal else "credit"
        base = ob if ob_side == natural_side else -ob

        q = """SELECT COALESCE(SUM(ve.debit),0) d, COALESCE(SUM(ve.credit),0) c
               FROM voucher_entries ve JOIN vouchers v ON v.id = ve.voucher_id
               WHERE ve.account_id = ?"""
        params = [acc_id]
        if as_of:
            q += " AND v.date <= ?"
            params.append(as_of)
        row = db.execute(q, params).fetchone()
        delta = (row["d"] - row["c"]) if is_debit_normal else (row["c"] - row["d"])
        return round(base + delta, 2)

    assets      = db.execute("SELECT * FROM accounts WHERE type='asset'      ORDER BY name").fetchall()
    liabilities = db.execute("SELECT * FROM accounts WHERE type='liability'  ORDER BY name").fetchall()
    equity      = db.execute("SELECT * FROM accounts WHERE type='equity'     ORDER BY name").fetchall()

    # Fold net profit into retained earnings on the equity side
    pl_q = """SELECT
        COALESCE(SUM(CASE WHEN a.type='income'  THEN ve.credit - ve.debit  ELSE 0 END), 0) income,
        COALESCE(SUM(CASE WHEN a.type='expense' THEN ve.debit  - ve.credit ELSE 0 END), 0) expense
        FROM voucher_entries ve
        JOIN vouchers v ON v.id = ve.voucher_id
        JOIN accounts a ON a.id = ve.account_id"""
    pl_params = []
    if as_of:
        pl_q += " WHERE v.date <= ?"
        pl_params.append(as_of)
    pl = db.execute(pl_q, pl_params).fetchone()
    # Opening balance contribution for income/expense
    inc_ob = sum(
        (r["opening_balance"] if r["opening_side"] == "credit" else -r["opening_balance"])
        for r in db.execute("SELECT * FROM accounts WHERE type='income'").fetchall()
    )
    exp_ob = sum(
        (r["opening_balance"] if r["opening_side"] == "debit" else -r["opening_balance"])
        for r in db.execute("SELECT * FROM accounts WHERE type='expense'").fetchall()
    )
    net_profit = round(inc_ob + pl["income"] - exp_ob - pl["expense"], 2)

    def acc_line(r):
        bal = account_balance(r["id"], r["type"], r["opening_balance"], r["opening_side"])
        return {"id": r["id"], "name": r["name"], "balance": bal}

    asset_lines      = [acc_line(r) for r in assets]
    liability_lines  = [acc_line(r) for r in liabilities]
    equity_lines     = [acc_line(r) for r in equity]

    total_assets      = round(sum(l["balance"] for l in asset_lines), 2)
    total_liabilities = round(sum(l["balance"] for l in liability_lines), 2)
    total_equity      = round(sum(l["balance"] for l in equity_lines) + net_profit, 2)

    return jsonify({
        "as_of": as_of or "all-time",
        "assets":      asset_lines,
        "liabilities": liability_lines,
        "equity":      equity_lines,
        "net_profit":  net_profit,
        "total_assets":      total_assets,
        "total_liabilities": total_liabilities,
        "total_equity":      total_equity,
    })


@app.route("/api/reports/stock")
def report_stock():
    db = get_db()
    from_date = request.args.get("from") or None
    to_date = request.args.get("to") or None

    items = db.execute("SELECT * FROM items ORDER BY name").fetchall()
    result = []
    for it in items:
        # movement strictly before 'from' rolls into an as-of opening qty/value
        pre_in_qty = pre_in_val = pre_out_qty = pre_out_val = 0.0
        if from_date:
            pre_rows = db.execute(
                """SELECT direction, SUM(qty) q, SUM(qty*rate) val
                   FROM voucher_items vi JOIN vouchers v ON v.id = vi.voucher_id
                   WHERE vi.item_id = ? AND v.date < ? GROUP BY direction""",
                (it["id"], from_date),
            ).fetchall()
            for m in pre_rows:
                if m["direction"] == "in":
                    pre_in_qty, pre_in_val = m["q"], m["val"]
                else:
                    pre_out_qty, pre_out_val = m["q"], m["val"]

        opening_qty_asof = it["opening_qty"] + pre_in_qty - pre_out_qty
        opening_value_asof = it["opening_qty"] * it["opening_rate"] + pre_in_val - pre_out_val

        clauses = ["vi.item_id = ?"]
        params = [it["id"]]
        if from_date:
            clauses.append("v.date >= ?")
            params.append(from_date)
        if to_date:
            clauses.append("v.date <= ?")
            params.append(to_date)
        where = " AND ".join(clauses)

        moves = db.execute(
            f"""SELECT direction, SUM(qty) as qty, SUM(qty*rate) as value
               FROM voucher_items vi JOIN vouchers v ON v.id = vi.voucher_id
               WHERE {where} GROUP BY direction""",
            params,
        ).fetchall()
        qty_in = qty_out = val_in = val_out = 0.0
        for m in moves:
            if m["direction"] == "in":
                qty_in, val_in = m["qty"], m["value"]
            else:
                qty_out, val_out = m["qty"], m["value"]
        closing_qty = round(opening_qty_asof + qty_in - qty_out, 4)
        total_in_qty = opening_qty_asof + qty_in
        total_in_value = opening_value_asof + val_in
        avg_rate = (total_in_value / total_in_qty) if total_in_qty > 0 else 0
        closing_value = round(closing_qty * avg_rate, 2)
        result.append({
            "id": it["id"], "name": it["name"], "unit": it["unit"],
            "opening_qty": round(opening_qty_asof, 4), "in_qty": qty_in, "out_qty": qty_out,
            "closing_qty": closing_qty, "avg_rate": round(avg_rate, 2),
            "closing_value": closing_value,
        })
    return jsonify(result)


@app.route("/api/reports/item-ledger/<int:item_id>")
def report_item_ledger(item_id):
    db = get_db()
    it = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not it:
        return jsonify({"error": "item not found"}), 404

    from_date = request.args.get("from") or None
    to_date = request.args.get("to") or None

    pre_in_qty = pre_out_qty = 0.0
    if from_date:
        pre_rows = db.execute(
            """SELECT direction, COALESCE(SUM(qty), 0) q
               FROM voucher_items vi JOIN vouchers v ON v.id = vi.voucher_id
               WHERE vi.item_id = ? AND v.date < ? GROUP BY direction""",
            (item_id, from_date),
        ).fetchall()
        for m in pre_rows:
            if m["direction"] == "in": pre_in_qty = m["q"]
            else: pre_out_qty = m["q"]

    opening_qty = it["opening_qty"] + pre_in_qty - pre_out_qty

    clauses = ["vi.item_id = ?"]
    params = [item_id]
    if from_date:
        clauses.append("v.date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("v.date <= ?")
        params.append(to_date)
    where = " AND ".join(clauses)

    rows = db.execute(
        f"""SELECT v.date, v.type, v.narration, v.reference, vi.direction, vi.qty, vi.rate, vi.gst_rate, v.id as voucher_id
           FROM voucher_items vi JOIN vouchers v ON v.id = vi.voucher_id
           WHERE {where}
           ORDER BY v.date, v.id""",
        params,
    ).fetchall()

    ledger = []
    running = opening_qty
    for r in rows:
        direction = r["direction"]
        qty = r["qty"]
        qty_in = qty if direction == "in" else 0.0
        qty_out = qty if direction == "out" else 0.0
        running += (qty_in - qty_out)
        ledger.append({
            "date": r["date"], "type": r["type"], "narration": r["narration"], "reference": r["reference"],
            "voucher_id": r["voucher_id"], "direction": direction, "qty_in": qty_in, "qty_out": qty_out,
            "rate": r["rate"], "gst_rate": r["gst_rate"], "amount": round(qty * r["rate"], 2),
            "stock_balance": round(running, 4),
        })

    return jsonify({
        "item": dict(it),
        "opening_qty": round(opening_qty, 4),
        "closing_qty": round(running, 4),
        "rows": ledger,
    })


# ---------- Initialization and Server Orchestration ----------

def run_flask():
    """Starts the Flask server in a standard local configuration."""
    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    init_db()

    # ── Google Sheets sync (optional) ──────────────────────────────
    if os.path.exists(CREDS_PATH):
        try:
            from sheets_sync import SheetsSync
            _sheets = SheetsSync(DB_PATH, CREDS_PATH)
            url = _sheets.setup()
            _sheets.start_background_sync()      # initial sync + trigger-on-write
            print(f"[Sheets] Sync active → {url}")
        except Exception as _e:
            print(f"[Sheets] Disabled ({_e})")
            _sheets = None
    else:
        print("[Sheets] service_account.json not found — sync disabled")
        print("[Sheets] See README for setup instructions.")

    # ── Flask server ───────────────────────────────────────────────
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # ── WebView2 window ────────────────────────────────────────────
    print("Launching ERP Desktop window...")
    webview.create_window(
        title     = "Ledger — Personal Accounts & Inventory ERP",
        url       = "http://127.0.0.1:5050",
        width     = 1200,
        height    = 850,
        resizable = True,
    )
    webview.start()
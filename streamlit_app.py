"""
streamlit_app.py  -  ERP Ledger on Streamlit Cloud
===================================================
Full cloud ERP: Accounts, Items, Vouchers, Reports.
Backend: Google Sheets (via gspread).
Deploy to Streamlit Cloud. Add credentials in st.secrets.
"""

import streamlit as st
import pandas as pd
from datetime import date, datetime
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("SUPABASE_DB_URL") or st.secrets.get("supabase_db_url")

SHEET_NAME  = "Supabase Cloud"
ACCOUNT_TYPES = ["asset", "liability", "income", "expense", "equity"]
VOUCHER_TYPES = ["sales", "purchase", "payment", "receipt", "journal", "production"]

# ================================================================
#  CONNECTION & DATA LOADING (OPTIMIZED WITH POOLING)
# ================================================================
from psycopg2 import pool

@st.cache_resource
def get_db_pool(db_url: str):
    return pool.ThreadedConnectionPool(
        1, 10, db_url,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5
    )

def get_connection():
    if not DATABASE_URL:
        return None
    try:
        p = get_db_pool(DATABASE_URL)
        return p.getconn()
    except Exception:
        return psycopg2.connect(DATABASE_URL)

def release_connection(conn):
    if not DATABASE_URL or conn is None:
        return
    try:
        p = get_db_pool(DATABASE_URL)
        p.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass

def _df(name: str) -> pd.DataFrame:
    if not DATABASE_URL:
        st.error("Database connection URL not set. Please set SUPABASE_DB_URL in .env or st.secrets.")
        return pd.DataFrame()

    conn = get_connection()
    if not conn:
        return pd.DataFrame()

    try:
        if name == "Accounts":
            query = "SELECT * FROM accounts"
            col_map = {"id": "ID", "name": "Name", "type": "Type", "opening_balance": "Opening Balance", "opening_side": "Opening Side"}
        elif name == "Items":
            query = "SELECT * FROM items"
            col_map = {"id": "ID", "name": "Name", "unit": "Unit", "opening_qty": "Opening Qty", "opening_rate": "Opening Rate"}
        elif name == "Vouchers":
            query = "SELECT * FROM vouchers"
            col_map = {"id": "ID", "date": "Date", "type": "Type", "reference": "Reference", "narration": "Narration", "created_at": "Created At"}
        elif name == "Entries":
            query = """
                SELECT ve.id, ve.voucher_id, ve.account_id, a.name AS account_name, ve.debit, ve.credit
                FROM voucher_entries ve
                JOIN accounts a ON a.id = ve.account_id
            """
            col_map = {"id": "ID", "voucher_id": "Voucher ID", "account_id": "Account ID", "account_name": "Account Name", "debit": "Debit", "credit": "Credit"}
        elif name == "Stock Lines":
            query = """
                SELECT vi.id, vi.voucher_id, vi.item_id, i.name AS item_name, vi.direction, vi.qty, vi.rate, vi.gst_rate
                FROM voucher_items vi
                JOIN items i ON i.id = vi.item_id
            """
            col_map = {"id": "ID", "voucher_id": "Voucher ID", "item_id": "Item ID", "item_name": "Item Name", "direction": "Direction", "qty": "Qty", "rate": "Rate", "gst_rate": "GST Rate"}
        else:
            return pd.DataFrame()

        df = pd.read_sql_query(query, conn)
        df.rename(columns=col_map, inplace=True)
        return df
    finally:
        release_connection(conn)

def load_data():
    if not DATABASE_URL:
        st.error("Database connection URL not set. Please set SUPABASE_DB_URL in .env or st.secrets.")
        return

    conn = get_connection()
    if not conn:
        return

    try:
        # Accounts
        df_acc = pd.read_sql_query("SELECT * FROM accounts", conn)
        df_acc.rename(columns={"id": "ID", "name": "Name", "type": "Type", "opening_balance": "Opening Balance", "opening_side": "Opening Side"}, inplace=True)
        st.session_state["accounts"] = df_acc

        # Items
        df_it = pd.read_sql_query("SELECT * FROM items", conn)
        df_it.rename(columns={"id": "ID", "name": "Name", "unit": "Unit", "opening_qty": "Opening Qty", "opening_rate": "Opening Rate"}, inplace=True)
        st.session_state["items"] = df_it

        # Vouchers
        df_v = pd.read_sql_query("SELECT * FROM vouchers", conn)
        df_v.rename(columns={"id": "ID", "date": "Date", "type": "Type", "reference": "Reference", "narration": "Narration", "created_at": "Created At"}, inplace=True)
        st.session_state["vouchers"] = df_v

        # Entries
        df_e = pd.read_sql_query("""
            SELECT ve.id, ve.voucher_id, ve.account_id, a.name AS account_name, ve.debit, ve.credit
            FROM voucher_entries ve
            JOIN accounts a ON a.id = ve.account_id
        """, conn)
        df_e.rename(columns={"id": "ID", "voucher_id": "Voucher ID", "account_id": "Account ID", "account_name": "Account Name", "debit": "Debit", "credit": "Credit"}, inplace=True)
        st.session_state["entries"] = df_e

        # Stock Lines
        df_sl = pd.read_sql_query("""
            SELECT vi.id, vi.voucher_id, vi.item_id, i.name AS item_name, vi.direction, vi.qty, vi.rate, vi.gst_rate
            FROM voucher_items vi
            JOIN items i ON i.id = vi.item_id
        """, conn)
        df_sl.rename(columns={"id": "ID", "voucher_id": "Voucher ID", "item_id": "Item ID", "item_name": "Item Name", "direction": "Direction", "qty": "Qty", "rate": "Rate", "gst_rate": "GST Rate"}, inplace=True)
        st.session_state["stock_lines"] = df_sl

        # Formulas
        ensure_formulas_table_conn(conn)
        try:
            df_f = pd.read_sql_query("""
                SELECT f.id, f.finished_item_id, fi.name AS finished_item_name,
                       f.raw_item_id, ri.name AS raw_item_name, ri.unit AS raw_unit, f.qty_required
                FROM item_formulas f
                JOIN items fi ON fi.id = f.finished_item_id
                JOIN items ri ON ri.id = f.raw_item_id
            """, conn)
            st.session_state["formulas"] = df_f
        except Exception:
            st.session_state["formulas"] = pd.DataFrame()

        st.session_state["loaded_at"] = datetime.now().strftime("%H:%M:%S")
    except Exception as err:
        st.error(f"Error loading database tables: {err}")
    finally:
        release_connection(conn)

def ensure_formulas_table_conn(conn):
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS item_formulas (
                id SERIAL PRIMARY KEY,
                finished_item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                raw_item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                qty_required REAL NOT NULL DEFAULT 0,
                CONSTRAINT unique_finished_raw UNIQUE (finished_item_id, raw_item_id)
            );
        """)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

def save_formula_for_item(finished_item_id: int, raw_materials_list: list):
    if not DATABASE_URL:
        return
    conn = get_connection()
    if not conn:
        return
    try:
        ensure_formulas_table_conn(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM item_formulas WHERE finished_item_id = %s", (finished_item_id,))
        for rm in raw_materials_list:
            raw_id = rm["raw_item_id"]
            qty_req = rm["qty_required"]
            if raw_id and qty_req > 0:
                cur.execute(
                    "INSERT INTO item_formulas (finished_item_id, raw_item_id, qty_required) VALUES (%s, %s, %s) "
                    "ON CONFLICT (finished_item_id, raw_item_id) DO UPDATE SET qty_required = EXCLUDED.qty_required",
                    (finished_item_id, raw_id, qty_req)
                )
        conn.commit()
    except Exception as err:
        conn.rollback()
        raise err
    finally:
        release_connection(conn)

def ensure_loaded():
    if "accounts" not in st.session_state:
        load_data()

def accounts()    -> pd.DataFrame: return st.session_state.get("accounts", pd.DataFrame())
def items()       -> pd.DataFrame: return st.session_state.get("items", pd.DataFrame())
def vouchers()    -> pd.DataFrame: return st.session_state.get("vouchers", pd.DataFrame())
def entries()     -> pd.DataFrame: return st.session_state.get("entries", pd.DataFrame())
def stock_lines() -> pd.DataFrame: return st.session_state.get("stock_lines", pd.DataFrame())
def formulas()    -> pd.DataFrame: return st.session_state.get("formulas", pd.DataFrame())

# ================================================================
#  WRITE HELPERS (BATCHED & POOLED)
# ================================================================
def _next_id(tab_or_df, col: str = "ID") -> int:
    tab_map = {
        "Accounts": "accounts",
        "Items": "items",
        "Vouchers": "vouchers",
        "Entries": "voucher_entries",
        "Stock Lines": "voucher_items"
    }
    table_name = None
    if isinstance(tab_or_df, str):
        table_name = tab_map.get(tab_or_df, tab_or_df.lower())
    elif isinstance(tab_or_df, pd.DataFrame):
        df = tab_or_df
        if df.empty or col not in df.columns or len(df) == 0:
            return 1
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        return int(vals.max()) + 1 if len(vals) > 0 else 1

    if table_name and DATABASE_URL:
        conn = get_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")
                row = cur.fetchone()
                return int(row[0] or 0) + 1
            except Exception:
                pass
            finally:
                release_connection(conn)
            
    return 1

def _append(tab: str, row: list):
    if not DATABASE_URL:
        return
    conn = get_connection()
    if not conn:
        return
    cur = conn.cursor()
    try:
        if tab == "Accounts":
            cur.execute("SELECT setval('accounts_id_seq', COALESCE((SELECT MAX(id) FROM accounts), 0))")
            cur.execute(
                "INSERT INTO accounts (id, name, type, opening_balance, opening_side) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, type=EXCLUDED.type, opening_balance=EXCLUDED.opening_balance, opening_side=EXCLUDED.opening_side",
                row
            )
            cur.execute("SELECT setval('accounts_id_seq', COALESCE((SELECT MAX(id) FROM accounts), 0))")
        elif tab == "Items":
            cur.execute("SELECT setval('items_id_seq', COALESCE((SELECT MAX(id) FROM items), 0))")
            cur.execute(
                "INSERT INTO items (id, name, unit, opening_qty, opening_rate) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, unit=EXCLUDED.unit, opening_qty=EXCLUDED.opening_qty, opening_rate=EXCLUDED.opening_rate",
                row
            )
            cur.execute("SELECT setval('items_id_seq', COALESCE((SELECT MAX(id) FROM items), 0))")
        elif tab == "Vouchers":
            cur.execute("SELECT setval('vouchers_id_seq', COALESCE((SELECT MAX(id) FROM vouchers), 0))")
            cur.execute(
                "INSERT INTO vouchers (id, date, type, reference, narration, created_at) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET date=EXCLUDED.date, type=EXCLUDED.type, reference=EXCLUDED.reference, narration=EXCLUDED.narration",
                row
            )
            cur.execute("SELECT setval('vouchers_id_seq', COALESCE((SELECT MAX(id) FROM vouchers), 0))")
        elif tab == "Entries":
            val = [row[0], row[1], row[2], row[4], row[5]] if len(row) == 6 else row
            cur.execute("SELECT setval('voucher_entries_id_seq', COALESCE((SELECT MAX(id) FROM voucher_entries), 0))")
            cur.execute(
                "INSERT INTO voucher_entries (id, voucher_id, account_id, debit, credit) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET debit=EXCLUDED.debit, credit=EXCLUDED.credit",
                val
            )
            cur.execute("SELECT setval('voucher_entries_id_seq', COALESCE((SELECT MAX(id) FROM voucher_entries), 0))")
        elif tab == "Stock Lines":
            val = [row[0], row[1], row[2], row[4], row[5], row[6], row[7]] if len(row) == 8 else row
            cur.execute("SELECT setval('voucher_items_id_seq', COALESCE((SELECT MAX(id) FROM voucher_items), 0))")
            cur.execute(
                "INSERT INTO voucher_items (id, voucher_id, item_id, direction, qty, rate, gst_rate) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET qty=EXCLUDED.qty, rate=EXCLUDED.rate, gst_rate=EXCLUDED.gst_rate",
                val
            )
            cur.execute("SELECT setval('voucher_items_id_seq', COALESCE((SELECT MAX(id) FROM voucher_items), 0))")
        conn.commit()
    except Exception as err:
        conn.rollback()
        raise err
    finally:
        release_connection(conn)

def _save_voucher_transaction(v_date, v_type, reference, narration, entry_data, stock_entries) -> int:
    if not DATABASE_URL:
        return 0
    conn = get_connection()
    if not conn:
        return 0
    cur = conn.cursor()
    try:
        cur.execute("SELECT setval('vouchers_id_seq', COALESCE((SELECT MAX(id) FROM vouchers), 0))")
        cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM vouchers")
        v_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO vouchers (id, date, type, reference, narration, created_at) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET date=EXCLUDED.date, type=EXCLUDED.type, reference=EXCLUDED.reference, narration=EXCLUDED.narration",
            [v_id, str(v_date), v_type, reference, narration, datetime.now().isoformat()]
        )
        cur.execute("SELECT setval('vouchers_id_seq', COALESCE((SELECT MAX(id) FROM vouchers), 0))")

        valid_entries = [e for e in entry_data if e["debit"] > 0 or e["credit"] > 0]
        if valid_entries:
            cur.execute("SELECT setval('voucher_entries_id_seq', COALESCE((SELECT MAX(id) FROM voucher_entries), 0))")
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM voucher_entries")
            e_id = cur.fetchone()[0]

            entry_tuples = [
                (e_id + idx, v_id, e["account_id"], e["debit"], e["credit"])
                for idx, e in enumerate(valid_entries)
            ]
            cur.executemany(
                "INSERT INTO voucher_entries (id, voucher_id, account_id, debit, credit) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET debit=EXCLUDED.debit, credit=EXCLUDED.credit",
                entry_tuples
            )
            cur.execute("SELECT setval('voucher_entries_id_seq', COALESCE((SELECT MAX(id) FROM voucher_entries), 0))")

        if stock_entries:
            cur.execute("SELECT setval('voucher_items_id_seq', COALESCE((SELECT MAX(id) FROM voucher_items), 0))")
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM voucher_items")
            sl_id = cur.fetchone()[0]

            stock_tuples = [
                (sl_id + idx, v_id, s["item_id"], s["direction"], s["qty"], s["rate"], s["gst_rate"])
                for idx, s in enumerate(stock_entries)
            ]
            cur.executemany(
                "INSERT INTO voucher_items (id, voucher_id, item_id, direction, qty, rate, gst_rate) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET qty=EXCLUDED.qty, rate=EXCLUDED.rate, gst_rate=EXCLUDED.gst_rate",
                stock_tuples
            )
            cur.execute("SELECT setval('voucher_items_id_seq', COALESCE((SELECT MAX(id) FROM voucher_items), 0))")

        conn.commit()
        return v_id
    except Exception as err:
        conn.rollback()
        raise err
    finally:
        release_connection(conn)

def _update_voucher_transaction(v_id, ev_date, v_type, ev_ref, ev_nar, ev_entries_data, ev_stock_entries):
    if not DATABASE_URL:
        return
    conn = get_connection()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE vouchers SET date = %s, type = %s, reference = %s, narration = %s WHERE id = %s",
            (str(ev_date), v_type, ev_ref, ev_nar, v_id)
        )

        cur.execute("DELETE FROM voucher_entries WHERE voucher_id = %s", (v_id,))
        valid_entries = [e for e in ev_entries_data if e["debit"] > 0 or e["credit"] > 0]
        if valid_entries:
            cur.execute("SELECT setval('voucher_entries_id_seq', COALESCE((SELECT MAX(id) FROM voucher_entries), 0))")
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM voucher_entries")
            e_id = cur.fetchone()[0]
            entry_tuples = [
                (e_id + idx, v_id, e["account_id"], e["debit"], e["credit"])
                for idx, e in enumerate(valid_entries)
            ]
            cur.executemany(
                "INSERT INTO voucher_entries (id, voucher_id, account_id, debit, credit) VALUES (%s, %s, %s, %s, %s)",
                entry_tuples
            )
            cur.execute("SELECT setval('voucher_entries_id_seq', COALESCE((SELECT MAX(id) FROM voucher_entries), 0))")

        cur.execute("DELETE FROM voucher_items WHERE voucher_id = %s", (v_id,))
        if ev_stock_entries:
            cur.execute("SELECT setval('voucher_items_id_seq', COALESCE((SELECT MAX(id) FROM voucher_items), 0))")
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM voucher_items")
            sl_id = cur.fetchone()[0]
            stock_tuples = [
                (sl_id + idx, v_id, s["item_id"], s["direction"], s["qty"], s["rate"], s["gst_rate"])
                for idx, s in enumerate(stock_entries)
            ]
            cur.executemany(
                "INSERT INTO voucher_items (id, voucher_id, item_id, direction, qty, rate, gst_rate) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                stock_tuples
            )
            cur.execute("SELECT setval('voucher_items_id_seq', COALESCE((SELECT MAX(id) FROM voucher_items), 0))")

        conn.commit()
    except Exception as err:
        conn.rollback()
        raise err
    finally:
        release_connection(conn)

def _delete_rows_where(tab: str, col: str, value):
    if not DATABASE_URL:
        return
    conn = get_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        col_map = {"ID": "id", "Voucher ID": "voucher_id"}
        db_col = col_map.get(col, col.lower())
        
        if tab == "Accounts":
            cur.execute(f"DELETE FROM accounts WHERE {db_col} = %s", (value,))
        elif tab == "Items":
            cur.execute(f"DELETE FROM items WHERE {db_col} = %s", (value,))
        elif tab == "Vouchers":
            cur.execute(f"DELETE FROM vouchers WHERE {db_col} = %s", (value,))
        elif tab == "Entries":
            cur.execute(f"DELETE FROM voucher_entries WHERE {db_col} = %s", (value,))
        elif tab == "Stock Lines":
            cur.execute(f"DELETE FROM voucher_items WHERE {db_col} = %s", (value,))
        conn.commit()
    finally:
        release_connection(conn)

def _update_row_where(tab: str, col_id: str, id_val, new_values: list):
    if not DATABASE_URL:
        return
    conn = get_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        if tab == "Accounts":
            cur.execute("UPDATE accounts SET name = %s, type = %s, opening_balance = %s, opening_side = %s WHERE id = %s",
                        (new_values[1], new_values[2], new_values[3], new_values[4], id_val))
        elif tab == "Items":
            cur.execute("UPDATE items SET name = %s, unit = %s, opening_qty = %s, opening_rate = %s WHERE id = %s",
                        (new_values[1], new_values[2], new_values[3], new_values[4], id_val))
        elif tab == "Vouchers":
            cur.execute("UPDATE vouchers SET date = %s, type = %s, reference = %s, narration = %s WHERE id = %s",
                        (new_values[1], new_values[2], new_values[3], new_values[4], id_val))
        conn.commit()
    finally:
        release_connection(conn)

# ================================================================
#  REPORT COMPUTATIONS
# ================================================================
def _merge_entries_vouchers(from_d=None, to_d=None):
    e = entries().copy()
    v = vouchers()
    if e.empty or v.empty:
        return pd.DataFrame()
    merged = e.merge(
        v[["ID", "Date"]].rename(columns={"ID": "VID"}),
        left_on="Voucher ID", right_on="VID",
    )
    if from_d:
        merged = merged[merged["Date"] >= str(from_d)]
    if to_d:
        merged = merged[merged["Date"] <= str(to_d)]
    for col in ("Debit", "Credit"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)
    return merged

def compute_pl(from_d=None, to_d=None):
    accs   = accounts()
    merged = _merge_entries_vouchers(from_d, to_d)
    include_opening = from_d is None

    def line(acc_row, natural_side):
        ae = merged[merged["Account ID"] == acc_row["ID"]] if not merged.empty else pd.DataFrame()
        if natural_side == "credit":
            mvt = (ae["Credit"].sum() - ae["Debit"].sum()) if not ae.empty else 0
        else:
            mvt = (ae["Debit"].sum() - ae["Credit"].sum()) if not ae.empty else 0
        ob = float(acc_row.get("Opening Balance", 0) or 0)
        ob_side = str(acc_row.get("Opening Side", "debit"))
        base = ob if ob_side == natural_side else -ob
        return round((base if include_opening else 0) + mvt, 2)

    income  = [{"Name": r["Name"], "Amount": line(r, "credit")}
               for _, r in accs[accs["Type"] == "income"].iterrows()] if not accs.empty else []
    expense = [{"Name": r["Name"], "Amount": line(r, "debit")}
               for _, r in accs[accs["Type"] == "expense"].iterrows()] if not accs.empty else []

    ti = round(sum(i["Amount"] for i in income), 2)
    te = round(sum(e["Amount"] for e in expense), 2)
    return income, expense, ti, te, round(ti - te, 2)

def compute_bs(as_of=None):
    accs   = accounts()
    merged = _merge_entries_vouchers(to_d=as_of)

    def acc_bal(acc_row):
        is_dr = acc_row["Type"] in ("asset", "expense")
        ae = merged[merged["Account ID"] == acc_row["ID"]] if not merged.empty else pd.DataFrame()
        d = ae["Debit"].sum() if not ae.empty else 0
        c = ae["Credit"].sum() if not ae.empty else 0
        delta = (d - c) if is_dr else (c - d)
        natural = "debit" if is_dr else "credit"
        ob = float(acc_row.get("Opening Balance", 0) or 0)
        ob_side = str(acc_row.get("Opening Side", "debit"))
        base = ob if ob_side == natural else -ob
        return round(base + delta, 2)

    def section(typ):
        if accs.empty: return []
        return [{"Name": r["Name"], "Balance": acc_bal(r)}
                for _, r in accs[accs["Type"] == typ].iterrows()]

    _, _, _, _, net_profit = compute_pl(to_d=as_of)
    assets      = section("asset")
    liabilities = section("liability")
    equity_rows = section("equity")
    ta = round(sum(a["Balance"] for a in assets), 2)
    tl = round(sum(l["Balance"] for l in liabilities), 2)
    te = round(sum(e["Balance"] for e in equity_rows) + net_profit, 2)
    return assets, liabilities, equity_rows, net_profit, ta, tl, te

def compute_stock(from_d=None, to_d=None):
    it_df = items()
    sl    = stock_lines()
    v_df  = vouchers()
    if it_df.empty: return []
    result = []
    for _, it in it_df.iterrows():
        if sl.empty or v_df.empty:
            lines = pd.DataFrame()
        else:
            sl2 = sl[sl["Item ID"] == it["ID"]]
            if sl2.empty:
                lines = pd.DataFrame()
            else:
                lines = sl2.merge(
                    v_df[["ID", "Date"]].rename(columns={"ID": "VID"}),
                    left_on="Voucher ID", right_on="VID",
                )
                if from_d: lines = lines[lines["Date"] >= str(from_d)]
                if to_d:   lines = lines[lines["Date"] <= str(to_d)]
                if "Qty" in lines.columns:
                    lines["Qty"] = pd.to_numeric(lines["Qty"], errors="coerce").fillna(0)

        in_l  = lines[lines["Direction"] == "in"]  if not lines.empty else pd.DataFrame()
        out_l = lines[lines["Direction"] == "out"] if not lines.empty else pd.DataFrame()
        q_in  = float(in_l["Qty"].sum())  if not in_l.empty else 0
        q_out = float(out_l["Qty"].sum()) if not out_l.empty else 0

        oq = float(it.get("Opening Qty",  0) or 0)
        cq = round(oq + q_in - q_out, 4)
        result.append({
            "Item": it["Name"], "Unit": it["Unit"],
            "Opening": round(oq, 2), "In": round(q_in, 2), "Out": round(q_out, 2),
            "Closing": cq,
        })
    return result

# ================================================================
#  PAGES
# ================================================================

# ── Dashboard ─────────────────────────────────────────────────────
def page_dashboard():
    st.title("📊 Dashboard")
    ensure_loaded()

    accs = accounts()
    v_df = vouchers()
    e_df = entries()

    # Calculate P&L & Cash Balance
    _, _, ti, te, np_ = compute_pl()

    cash_bank_bal = 0.0
    if not accs.empty and not e_df.empty:
        merged = _merge_entries_vouchers()
        for _, r in accs.iterrows():
            if r["Name"].lower() in ("cash", "bank"):
                ae = merged[merged["Account ID"] == r["ID"]] if not merged.empty else pd.DataFrame()
                d = ae["Debit"].sum() if not ae.empty else 0
                c = ae["Credit"].sum() if not ae.empty else 0
                ob = float(r.get("Opening Balance", 0) or 0)
                base = ob if r.get("Opening Side") == "debit" else -ob
                cash_bank_bal += (base + d - c)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vouchers Recorded", len(v_df) if not v_df.empty else 0)
    c2.metric("Total Accounts", len(accs) if not accs.empty else 0)
    c3.metric("Net Profit / Loss", f"₹{np_:,.2f}")
    c4.metric("Liquid Cash / Bank", f"₹{cash_bank_bal:,.2f}")

    st.caption(f"Data as of: {st.session_state.get('loaded_at', '—')}  ·  Sheet: {SHEET_NAME}")
    st.divider()

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("🏦 Account Ledgers & Closing Balances")
        if not accs.empty:
            merged = _merge_entries_vouchers()
            acc_rows = []
            for _, r in accs.iterrows():
                is_dr = r["Type"] in ("asset", "expense")
                ae = merged[merged["Account ID"] == r["ID"]] if not merged.empty else pd.DataFrame()
                d = ae["Debit"].sum() if not ae.empty else 0
                c = ae["Credit"].sum() if not ae.empty else 0
                delta = (d - c) if is_dr else (c - d)
                natural = "debit" if is_dr else "credit"
                ob = float(r.get("Opening Balance", 0) or 0)
                ob_side = str(r.get("Opening Side", "debit"))
                base = ob if ob_side == natural else -ob
                closing_bal = round(base + delta, 2)
                
                side_str = "Dr" if closing_bal >= 0 and is_dr else ("Cr" if closing_bal >= 0 else ("Dr" if not is_dr else "Cr"))
                
                acc_rows.append({
                    "Account Name": r["Name"],
                    "Category": r["Type"].capitalize(),
                    "Closing Balance": f"₹{abs(closing_bal):,.2f} {side_str}"
                })
            df_acc_summary = pd.DataFrame(acc_rows)
            st.dataframe(df_acc_summary, use_container_width=True, hide_index=True)
        else:
            st.info("No accounts found.")

    with col_right:
        st.subheader("📦 Stock Inventory Quick-View")
        stock_summary = compute_stock()
        if stock_summary:
            df_stock = pd.DataFrame(stock_summary)[["Item", "Unit", "Closing"]]
            st.dataframe(df_stock, use_container_width=True, hide_index=True)
        else:
            st.info("No stock items recorded.")

    if st.button("🔄 Refresh"):
        load_data(); st.rerun()

# ── Accounts ─────────────────────────────────────────────────────
def page_accounts():
    st.title("🏦 Accounts")
    ensure_loaded()
    accs = accounts()

    if not accs.empty:
        st.dataframe(accs, use_container_width=True, hide_index=True)
    else:
        st.info("No accounts yet.")

    with st.expander("➕ Add Account"):
        with st.form("f_acc"):
            name    = st.text_input("Name")
            typ     = st.selectbox("Type", ACCOUNT_TYPES)
            ob      = st.number_input("Opening Balance", min_value=0.0, step=0.01, format="%.2f")
            ob_side = st.radio("Opening Side", ["debit", "credit"], horizontal=True)
            if st.form_submit_button("Create"):
                if not name.strip():
                    st.error("Name required.")
                elif not accs.empty and name.strip() in accs["Name"].values:
                    st.error("Account already exists.")
                else:
                    nid = _next_id("Accounts")
                    _append("Accounts", [nid, name.strip(), typ, ob, ob_side])
                    st.success(f"Account '{name}' created (ID {nid}).")
                    load_data(); st.rerun()

    if not accs.empty:
        with st.expander("✏️ Edit / Delete Account"):
            acc_list = list(accs["Name"].values)
            sel_acc_name = st.selectbox("Select Account to Modify", acc_list)
            
            acc_row = accs[accs["Name"] == sel_acc_name].iloc[0]
            acc_id = int(acc_row["ID"])
            
            with st.form("f_edit_acc"):
                new_name = st.text_input("Name", value=acc_row["Name"])
                new_typ = st.selectbox("Type", ACCOUNT_TYPES, index=ACCOUNT_TYPES.index(acc_row["Type"]))
                new_ob = st.number_input("Opening Balance", min_value=0.0, step=0.01, value=float(acc_row["Opening Balance"]), format="%.2f")
                new_ob_side = st.radio("Opening Side", ["debit", "credit"], index=0 if acc_row["Opening Side"] == "debit" else 1, horizontal=True)
                
                c1, c2 = st.columns(2)
                save_clicked = c1.form_submit_button("Save Changes")
                delete_clicked = c2.form_submit_button("❌ Delete Account")
                
                if save_clicked:
                    if not new_name.strip():
                        st.error("Name required.")
                    elif new_name.strip() != acc_row["Name"] and new_name.strip() in accs["Name"].values:
                        st.error("Account name already exists.")
                    else:
                        _update_row_where("Accounts", "ID", acc_id, [acc_id, new_name.strip(), new_typ, new_ob, new_ob_side])
                        st.success(f"Account '{new_name}' updated.")
                        load_data(); st.rerun()
                
                if delete_clicked:
                    ents = entries()
                    used = not ents.empty and acc_id in ents["Account ID"].values
                    if used:
                        st.error("Cannot delete an account that has voucher entries.")
                    else:
                        _delete_rows_where("Accounts", "ID", acc_id)
                        st.success(f"Account '{sel_acc_name}' deleted.")
                        load_data(); st.rerun()

# ── Items ─────────────────────────────────────────────────────────
def page_items():
    st.title("📦 Items")
    ensure_loaded()
    it_df = items()

    if not it_df.empty:
        st.dataframe(it_df, use_container_width=True, hide_index=True)
    else:
        st.info("No items yet.")

    with st.expander("➕ Add Item"):
        with st.form("f_item"):
            name = st.text_input("Name")
            unit = st.text_input("Unit", value="pcs")
            oq   = st.number_input("Opening Qty",  min_value=0.0, step=0.01, format="%.2f")
            if st.form_submit_button("Create"):
                if not name.strip():
                    st.error("Name required.")
                else:
                    nid = _next_id("Items")
                    _append("Items", [nid, name.strip(), unit.strip() or "pcs", oq, 0.0])
                    st.success(f"Item '{name}' created.")
                    load_data(); st.rerun()

    if not it_df.empty:
        with st.expander("✏️ Edit / Delete Item"):
            it_names = list(it_df["Name"].values)
            sel_it_name = st.selectbox("Select Item to Modify", it_names)
            
            it_row = it_df[it_df["Name"] == sel_it_name].iloc[0]
            it_id = int(it_row["ID"])
            
            with st.form("f_edit_item"):
                new_name = st.text_input("Name", value=it_row["Name"])
                new_unit = st.text_input("Unit", value=it_row["Unit"])
                new_oq   = st.number_input("Opening Qty", min_value=0.0, step=0.01, value=float(it_row["Opening Qty"]), format="%.2f")
                new_or   = st.number_input("Opening Rate", min_value=0.0, step=0.01, value=float(it_row["Opening Rate"]), format="%.2f")
                
                c1, c2 = st.columns(2)
                save_clicked = c1.form_submit_button("Save Changes")
                delete_clicked = c2.form_submit_button("❌ Delete Item")
                
                if save_clicked:
                    if not new_name.strip():
                        st.error("Name required.")
                    elif new_name.strip() != it_row["Name"] and new_name.strip() in it_df["Name"].values:
                        st.error("Item name already exists.")
                    else:
                        _update_row_where("Items", "ID", it_id, [it_id, new_name.strip(), new_unit.strip() or "pcs", new_oq, new_or])
                        st.success(f"Item '{new_name}' updated.")
                        load_data(); st.rerun()
                
                if delete_clicked:
                    sl_all = stock_lines()
                    used = not sl_all.empty and it_id in sl_all["Item ID"].values
                    if used:
                        st.error("Cannot delete an item that has inventory movements.")
                    else:
                        _delete_rows_where("Items", "ID", it_id)
                        st.success(f"Item '{sel_it_name}' deleted.")
                        load_data(); st.rerun()

        with st.expander("🧪 Set / Edit Bill of Materials (BOM) Formula"):
            st.caption("Define standard raw material ratios required to produce 1 Lot / Unit of a finished product. Updating a formula does not alter past vouchers.")
            it_names = list(it_df["Name"].values)
            sel_f_name = st.selectbox("Select Finished Product Item", it_names, key="bom_sel_f")
            f_id = int(it_df[it_df["Name"] == sel_f_name].iloc[0]["ID"])

            conn = get_connection()
            existing_formula = []
            if conn:
                try:
                    ensure_formulas_table_conn(conn)
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT f.raw_item_id, i.name, f.qty_required FROM item_formulas f JOIN items i ON i.id = f.raw_item_id WHERE f.finished_item_id = %s",
                        (f_id,)
                    )
                    existing_formula = cur.fetchall()
                except Exception:
                    pass
                finally:
                    release_connection(conn)

            st.markdown(f"**Formula Ingredients for 1 Lot / Unit of `{sel_f_name}`:**")
            
            with st.form(f"form_bom_{f_id}"):
                raw_inputs = []
                rm_options = ["-- None --"] + [n for n in it_names if n != sel_f_name]

                for idx in range(7):
                    c_rm, c_qty = st.columns([3, 2])
                    
                    def_rm = "-- None --"
                    def_qty = 0.0
                    if idx < len(existing_formula):
                        ex_id, ex_name, ex_qty = existing_formula[idx]
                        if ex_name in rm_options:
                            def_rm = ex_name
                            def_qty = float(ex_qty)

                    rm_sel = c_rm.selectbox(f"Raw Material #{idx+1}", rm_options, index=rm_options.index(def_rm) if def_rm in rm_options else 0, key=f"bom_rm_{f_id}_{idx}")
                    rm_qty = c_qty.number_input(f"Qty per 1 Lot #{idx+1}", min_value=0.0, step=0.01, value=def_qty, format="%.2f", key=f"bom_qty_{f_id}_{idx}")

                    if rm_sel != "-- None --" and rm_qty > 0:
                        raw_id = int(it_df[it_df["Name"] == rm_sel].iloc[0]["ID"])
                        raw_inputs.append({"raw_item_id": raw_id, "qty_required": rm_qty})

                if st.form_submit_button("💾 Save Formula (BOM)", type="primary"):
                    try:
                        save_formula_for_item(f_id, raw_inputs)
                        st.success(f"Formula saved for '{sel_f_name}' with {len(raw_inputs)} raw material(s).")
                        load_data(); st.rerun()
                    except Exception as err:
                        st.error(f"Error saving formula: {err}")

# ── Vouchers ──────────────────────────────────────────────────────
def page_vouchers():
    st.title("📑 Vouchers")
    ensure_loaded()
    v_df = vouchers()
    e_df = entries()
    accs = accounts()
    it_df = items()

    if not v_df.empty:
        st.subheader("Voucher List")
        search = st.text_input("Search (type / reference / narration)", "")
        disp = v_df.copy()
        if search:
            mask = disp.apply(lambda r: search.lower() in str(r).lower(), axis=1)
            disp = disp[mask]
        st.dataframe(disp, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Create New Voucher")

    if accs.empty:
        st.warning("No accounts found. Create accounts first.")
        return

    acc_map   = {r["Name"]: r["ID"] for _, r in accs.iterrows()}
    acc_names = list(acc_map.keys())
    it_map    = {r["Name"]: r["ID"] for _, r in it_df.iterrows()} if not it_df.empty else {}

    # Initialize v_type in session state to handle change detection properly
    if "v_type_val" not in st.session_state:
        st.session_state["v_type_val"] = "sales"

    v_type    = st.selectbox("Type", VOUCHER_TYPES, key="v_type_val")
    v_date    = st.date_input("Date",        value=date.today())
    reference = st.text_input("Reference No.")
    narration = st.text_input("Narration")

    # Detect voucher type changes to reset and pre-populate states
    if "v_type_prev" not in st.session_state:
        st.session_state["v_type_prev"] = v_type
        # Clear entries & inv row keys
        st.session_state.pop("n_rows", None)
        st.session_state.pop("inv_rows", None)
        st.session_state.pop("show_stock_v_val", None)
    elif st.session_state["v_type_prev"] != v_type:
        st.session_state["v_type_prev"] = v_type
        # Clean up dynamically created session state row variables
        for i in range(st.session_state.get("n_rows", 2)):
            st.session_state.pop(f"v_acc_{i}", None)
            st.session_state.pop(f"v_side_{i}", None)
            st.session_state.pop(f"v_amt_{i}", None)
        for j in range(st.session_state.get("inv_rows", 1)):
            st.session_state.pop(f"v_item_{j}", None)
            st.session_state.pop(f"v_dir_{j}", None)
            st.session_state.pop(f"v_qty_{j}", None)
            st.session_state.pop(f"v_rate_{j}", None)
            st.session_state.pop(f"v_gst_{j}", None)
        st.session_state.pop("n_rows", None)
        st.session_state.pop("inv_rows", None)
        st.session_state.pop("show_stock_v_val", None)
        st.session_state.pop("auto_sync_amounts", None)

    # Initialize defaults if not present
    if "n_rows" not in st.session_state:
        st.session_state.n_rows = 2
    if "inv_rows" not in st.session_state:
        st.session_state.inv_rows = 1 if v_type in ("sales", "purchase", "production") and not it_df.empty else 0
    if "show_stock_v_val" not in st.session_state:
        st.session_state.show_stock_v_val = v_type in ("sales", "purchase")

    # Set up defaults for ledger rows
    for i in range(st.session_state.n_rows):
        acc_key = f"v_acc_{i}"
        side_key = f"v_side_{i}"
        amt_key = f"v_amt_{i}"

        if acc_key not in st.session_state:
            if v_type == "sales":
                if i == 0:
                    cash_idx = next((idx for idx, name in enumerate(acc_names) if name.lower() == "cash"), 0)
                    st.session_state[acc_key] = acc_names[cash_idx]
                elif i == 1:
                    sales_idx = next((idx for idx, name in enumerate(acc_names) if name.lower() == "sales"), 0)
                    st.session_state[acc_key] = acc_names[sales_idx]
                else:
                    st.session_state[acc_key] = acc_names[0]
            elif v_type == "purchase":
                if i == 0:
                    p_idx = next((idx for idx, name in enumerate(acc_names) if name.lower() == "purchases"), 0)
                    st.session_state[acc_key] = acc_names[p_idx]
                elif i == 1:
                    cash_idx = next((idx for idx, name in enumerate(acc_names) if name.lower() == "cash"), 0)
                    st.session_state[acc_key] = acc_names[cash_idx]
                else:
                    st.session_state[acc_key] = acc_names[0]
            else:
                st.session_state[acc_key] = acc_names[min(i, len(acc_names)-1)]

        if side_key not in st.session_state:
            if v_type in ("sales", "purchase"):
                st.session_state[side_key] = "Debit" if i == 0 else "Credit"
            else:
                st.session_state[side_key] = "Debit" if i % 2 == 0 else "Credit"

        if amt_key not in st.session_state:
            st.session_state[amt_key] = 0.0

    # Set up defaults for inventory rows
    if not it_df.empty:
        for j in range(st.session_state.inv_rows):
            item_key = f"v_item_{j}"
            dir_key  = f"v_dir_{j}"
            qty_key  = f"v_qty_{j}"
            rate_key = f"v_rate_{j}"
            gst_key  = f"v_gst_{j}"

            if item_key not in st.session_state:
                st.session_state[item_key] = list(it_map.keys())[0]
            if dir_key not in st.session_state:
                st.session_state[dir_key] = "out" if v_type == "sales" else "in"
            if qty_key not in st.session_state:
                st.session_state[qty_key] = 0.0
            if rate_key not in st.session_state:
                st.session_state[rate_key] = 0.0
            if gst_key not in st.session_state:
                st.session_state[gst_key] = 0.0

    # Calculate inventory total for auto-fill functionality
    is_prod = v_type == "production"
    show_stock = is_prod or st.session_state.show_stock_v_val

    inv_total = 0.0
    if show_stock and not it_df.empty:
        for j in range(st.session_state.inv_rows):
            qty  = st.session_state.get(f"v_qty_{j}", 0.0)
            rate = st.session_state.get(f"v_rate_{j}", 0.0)
            gst  = st.session_state.get(f"v_gst_{j}", 0.0)
            inv_total += qty * rate * (1 + gst / 100)

    # Auto-sync ledger amounts from inventory if checkbox is checked
    if v_type in ("sales", "purchase") and show_stock:
        if "auto_sync_amounts" not in st.session_state:
            st.session_state.auto_sync_amounts = True
        
        # If enabled and we have inventory lines total, force set amounts of first two rows
        if st.session_state.auto_sync_amounts and inv_total > 0:
            if st.session_state.n_rows >= 2:
                st.session_state["v_amt_0"] = round(inv_total, 2)
                st.session_state["v_amt_1"] = round(inv_total, 2)

    # ── PRODUCTION: only stock movements ──────────────────────────
    if is_prod:
        st.markdown("**Stock Movements** (In / Out)")
        if it_df.empty:
            st.warning("No items found. Add items first."); return

        with st.expander("🧪 Auto-Fill Production Lines from BOM Formula", expanded=True):
            bom_items = list(it_map.keys())
            c_bi, c_bl, c_btn = st.columns([3, 2, 2])
            sel_bom_item = c_bi.selectbox("Select Output Finished Product", bom_items, key="bom_prod_item")
            batch_lots = c_bl.number_input("Batch / Lot Qty Produced", min_value=0.01, step=1.0, value=1.0, key="bom_prod_lots")
            
            if c_btn.button("⚡ Auto-Fill Rows", key="btn_autofill_bom", type="primary"):
                finished_id = it_map[sel_bom_item]
                conn = get_connection()
                df_bom = pd.DataFrame()
                if conn:
                    try:
                        ensure_formulas_table_conn(conn)
                        df_bom = pd.read_sql_query(
                            "SELECT f.raw_item_id, i.name AS raw_name, f.qty_required FROM item_formulas f JOIN items i ON i.id = f.raw_item_id WHERE f.finished_item_id = %s",
                            conn, params=(finished_id,)
                        )
                    except Exception:
                        pass
                    finally:
                        release_connection(conn)

                if df_bom.empty:
                    st.warning(f"No BOM formula set for '{sel_bom_item}'. Set formula under Items -> Set Formula first.")
                else:
                    # Clean up old row keys
                    for j in range(st.session_state.get("inv_rows", 1)):
                        st.session_state.pop(f"v_item_{j}", None)
                        st.session_state.pop(f"v_dir_{j}", None)
                        st.session_state.pop(f"v_qty_{j}", None)
                        st.session_state.pop(f"v_rate_{j}", None)
                        st.session_state.pop(f"v_gst_{j}", None)

                    total_inv_rows = 1 + len(df_bom)
                    st.session_state.inv_rows = total_inv_rows

                    # Row 0: Finished Product IN
                    st.session_state["v_item_0"] = sel_bom_item
                    st.session_state["v_dir_0"] = "in"
                    st.session_state["v_qty_0"] = float(batch_lots)
                    st.session_state["v_rate_0"] = 0.0
                    st.session_state["v_gst_0"] = 0.0

                    # Rows 1..N: Raw Materials OUT
                    for r_idx, r_row in df_bom.iterrows():
                        row_num = r_idx + 1
                        st.session_state[f"v_item_{row_num}"] = r_row["raw_name"]
                        st.session_state[f"v_dir_{row_num}"] = "out"
                        st.session_state[f"v_qty_{row_num}"] = round(float(r_row["qty_required"]) * float(batch_lots), 2)
                        st.session_state[f"v_rate_{row_num}"] = 0.0
                        st.session_state[f"v_gst_{row_num}"] = 0.0

                    st.success(f"Auto-filled {len(df_bom)} raw material line(s) for {batch_lots} lot(s) of '{sel_bom_item}'.")
                    st.rerun()

        stock_entries = []
        for j in range(st.session_state.inv_rows):
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            itm  = c1.selectbox("Item",      list(it_map.keys()), key=f"v_item_{j}")
            dire = c2.selectbox("Direction", ["in", "out"],        key=f"v_dir_{j}")
            qty  = c3.number_input("Qty",  min_value=0.01, step=0.01, key=f"v_qty_{j}", format="%.2f")
            rate = c4.number_input("Rate", min_value=0.0,   step=0.01,  key=f"v_rate_{j}", format="%.2f")
            stock_entries.append({"item_id": it_map[itm], "item_name": itm,
                                   "direction": dire, "qty": qty, "rate": rate, "gst_rate": 0})

        col1, col2 = st.columns(2)
        if col1.button("+ Row", key="prod_add"):
            st.session_state.inv_rows += 1
            j = st.session_state.inv_rows - 1
            st.session_state[f"v_item_{j}"] = list(it_map.keys())[0]
            st.session_state[f"v_dir_{j}"] = "in"
            st.session_state[f"v_qty_{j}"] = 0.0
            st.session_state[f"v_rate_{j}"] = 0.0
            st.session_state[f"v_gst_{j}"] = 0.0
            st.rerun()
        if col2.button("- Row", key="prod_rm") and st.session_state.inv_rows > 1:
            j = st.session_state.inv_rows - 1
            st.session_state.pop(f"v_item_{j}", None)
            st.session_state.pop(f"v_dir_{j}", None)
            st.session_state.pop(f"v_qty_{j}", None)
            st.session_state.pop(f"v_rate_{j}", None)
            st.session_state.pop(f"v_gst_{j}", None)
            st.session_state.inv_rows -= 1
            st.rerun()

        if st.button("✅ Save Production Voucher", type="primary"):
            v_id  = _next_id(v_df)
            sl_id = _next_id(stock_lines())
            _append("Vouchers", [v_id, str(v_date), v_type, reference, narration, datetime.now().isoformat()])
            for i, s in enumerate(stock_entries):
                _append("Stock Lines", [sl_id+i, v_id, s["item_id"], s["item_name"],
                                        s["direction"], s["qty"], s["rate"], s["gst_rate"]])
            st.success(f"Production voucher #{v_id} saved.")
            # Clear state
            for j in range(st.session_state.inv_rows):
                st.session_state.pop(f"v_item_{j}", None)
                st.session_state.pop(f"v_dir_{j}", None)
                st.session_state.pop(f"v_qty_{j}", None)
                st.session_state.pop(f"v_rate_{j}", None)
                st.session_state.pop(f"v_gst_{j}", None)
            st.session_state.pop("inv_rows", None)
            load_data(); st.rerun()

    # ── ALL OTHER TYPES: ledger entries + optional inventory ───────
    else:
        # ── Section 1: Ledger entries ──────────────────────────────
        st.markdown("**Ledger Entries** — one side per row, debit total must equal credit total")

        # Checkbox to let the user auto-sync ledger amounts from inventory
        if v_type in ("sales", "purchase") and show_stock:
            st.checkbox("Auto-sync ledger amounts from inventory total", key="auto_sync_amounts")

        entry_data = []
        for i in range(st.session_state.n_rows):
            c1, c2, c3 = st.columns([4, 2, 2])
            acc  = c1.selectbox("Account", acc_names, key=f"v_acc_{i}")
            side = c2.selectbox("Side", ["Debit", "Credit"], key=f"v_side_{i}")
            amt  = c3.number_input("Amount", min_value=0.0, step=0.01, key=f"v_amt_{i}", format="%.2f")
            dr = amt if side == "Debit"  else 0.0
            cr = amt if side == "Credit" else 0.0
            entry_data.append({"account_id": acc_map[acc], "account_name": acc, "debit": dr, "credit": cr})

        col1, col2 = st.columns(2)
        if col1.button("+ Ledger Row"):
            st.session_state.n_rows += 1
            i = st.session_state.n_rows - 1
            st.session_state[f"v_acc_{i}"] = acc_names[0]
            st.session_state[f"v_side_{i}"] = "Credit" if i % 2 == 1 else "Debit"
            st.session_state[f"v_amt_{i}"] = 0.0
            st.rerun()
        if col2.button("- Ledger Row") and st.session_state.n_rows > 2:
            i = st.session_state.n_rows - 1
            st.session_state.pop(f"v_acc_{i}", None)
            st.session_state.pop(f"v_side_{i}", None)
            st.session_state.pop(f"v_amt_{i}", None)
            st.session_state.n_rows -= 1
            st.rerun()

        total_dr = round(sum(e["debit"]  for e in entry_data), 2)
        total_cr = round(sum(e["credit"] for e in entry_data), 2)
        bal_ok   = total_dr == total_cr and total_dr > 0

        bal_color = "green" if bal_ok else "red"
        st.markdown(
            f"<span style='color:{bal_color};font-weight:600'>"
            f"Dr ₹{total_dr:,.2f}  |  Cr ₹{total_cr:,.2f}"
            f"{'  ✅ Balanced' if bal_ok else '  ⚠️ Not balanced'}</span>",
            unsafe_allow_html=True,
        )

        # ── Section 2: Inventory movements (optional) ─────────────
        st.markdown("---")
        st.checkbox("Include inventory movement (items in/out)", key="show_stock_v_val")

        stock_entries = []
        if show_stock:
            if it_df.empty:
                st.warning("No items found. Add items first.")
            else:
                st.markdown("**Inventory Movement**")

                for j in range(st.session_state.inv_rows):
                    c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
                    itm  = c1.selectbox("Item",      list(it_map.keys()), key=f"v_item_{j}")
                    dire = c2.selectbox("Direction", ["in", "out"],        key=f"v_dir_{j}")
                    qty  = c3.number_input("Qty",      min_value=0.01, step=0.01, key=f"v_qty_{j}", format="%.2f")
                    rate = c4.number_input("Rate",     min_value=0.0,   step=0.01,  key=f"v_rate_{j}", format="%.2f")
                    gst  = c5.number_input("GST %",   min_value=0.0,   step=0.5,   key=f"v_gst_{j}", format="%.1f")
                    stock_entries.append({"item_id": it_map[itm], "item_name": itm,
                                          "direction": dire, "qty": qty, "rate": rate, "gst_rate": gst})

                ic1, ic2 = st.columns(2)
                if ic1.button("+ Item Row"):
                    st.session_state.inv_rows += 1
                    j = st.session_state.inv_rows - 1
                    st.session_state[f"v_item_{j}"] = list(it_map.keys())[0]
                    st.session_state[f"v_dir_{j}"] = "out" if v_type == "sales" else "in"
                    st.session_state[f"v_qty_{j}"] = 0.0
                    st.session_state[f"v_rate_{j}"] = 0.0
                    st.session_state[f"v_gst_{j}"] = 0.0
                    st.rerun()
                if ic2.button("- Item Row") and st.session_state.inv_rows > 1:
                    j = st.session_state.inv_rows - 1
                    st.session_state.pop(f"v_item_{j}", None)
                    st.session_state.pop(f"v_dir_{j}", None)
                    st.session_state.pop(f"v_qty_{j}", None)
                    st.session_state.pop(f"v_rate_{j}", None)
                    st.session_state.pop(f"v_gst_{j}", None)
                    st.session_state.inv_rows -= 1
                    st.rerun()
        else:
            # Clear inv_rows keys when section is hidden
            for j in range(st.session_state.get("inv_rows", 1)):
                st.session_state.pop(f"v_item_{j}", None)
                st.session_state.pop(f"v_dir_{j}", None)
                st.session_state.pop(f"v_qty_{j}", None)
                st.session_state.pop(f"v_rate_{j}", None)
                st.session_state.pop(f"v_gst_{j}", None)
            st.session_state.pop("inv_rows", None)

        st.markdown("---")
        if st.button("✅ Save Voucher", type="primary", disabled=not bal_ok):
            v_id = _save_voucher_transaction(v_date, v_type, reference, narration, entry_data, stock_entries)
            st.success(f"Voucher #{v_id} saved!")
            # Clear session state
            for i in range(st.session_state.n_rows):
                st.session_state.pop(f"v_acc_{i}", None)
                st.session_state.pop(f"v_side_{i}", None)
                st.session_state.pop(f"v_amt_{i}", None)
            for j in range(st.session_state.get("inv_rows", 1)):
                st.session_state.pop(f"v_item_{j}", None)
                st.session_state.pop(f"v_dir_{j}", None)
                st.session_state.pop(f"v_qty_{j}", None)
                st.session_state.pop(f"v_rate_{j}", None)
                st.session_state.pop(f"v_gst_{j}", None)
            st.session_state.pop("n_rows", None)
            st.session_state.pop("inv_rows", None)
            st.session_state.pop("show_stock_v_val", None)
            st.session_state.pop("auto_sync_amounts", None)
            load_data(); st.rerun()

        if not bal_ok and total_dr > 0:
            st.error(f"Entries don't balance: Dr ₹{total_dr:,.2f} vs Cr ₹{total_cr:,.2f}")

    if not v_df.empty:
        st.divider()
        with st.expander("✏️ Modify / Delete Existing Voucher"):
            # Select voucher
            v_options = [f"#{r['ID']} - {r['Type'].upper()} - {r['Date']} ({r['Narration'][:30]})" for _, r in v_df.iterrows()]
            sel_v_option = st.selectbox("Select Voucher to Modify", v_options)
            
            # Extract voucher ID
            sel_v_id = int(sel_v_option.split(" ")[0][1:])
            v_row = v_df[v_df["ID"] == sel_v_id].iloc[0]
            
            st.write(f"Modifying Voucher **#{sel_v_id}**")
            
            # Local helper to initialize/force edit voucher state in session state keys
            def init_edit_voucher_state(v_id, v_r, e_d, sl_all, force=False):
                v_ents = e_d[e_d["Voucher ID"] == v_id] if not e_d.empty else pd.DataFrame()
                v_sl = sl_all[sl_all["Voucher ID"] == v_id] if not sl_all.empty else pd.DataFrame()
                
                n_rows_key = f"ev_n_rows_{v_id}"
                inv_rows_key = f"ev_inv_rows_{v_id}"
                
                if n_rows_key not in st.session_state or force:
                    st.session_state[n_rows_key] = len(v_ents) if not v_ents.empty else 0
                if inv_rows_key not in st.session_state or force:
                    st.session_state[inv_rows_key] = len(v_sl) if not v_sl.empty else 0

                if not v_ents.empty:
                    for i, (_, r) in enumerate(v_ents.iterrows()):
                        acc_key = f"ev_acc_{v_id}_{i}"
                        side_key = f"ev_side_{v_id}_{i}"
                        amt_key = f"ev_amt_{v_id}_{i}"
                        
                        if acc_key not in st.session_state or force:
                            st.session_state[acc_key] = r["Account Name"]
                        if side_key not in st.session_state or force:
                            st.session_state[side_key] = "Debit" if float(r["Debit"]) > 0 else "Credit"
                        if amt_key not in st.session_state or force:
                            st.session_state[amt_key] = float(r["Debit"]) if float(r["Debit"]) > 0 else float(r["Credit"])

                if not v_sl.empty:
                    for j, (_, r) in enumerate(v_sl.iterrows()):
                        item_key = f"ev_item_{v_id}_{j}"
                        dir_key  = f"ev_dir_{v_id}_{j}"
                        qty_key  = f"ev_qty_{v_id}_{j}"
                        rate_key = f"ev_rate_{v_id}_{j}"
                        gst_key  = f"ev_gst_{v_id}_{j}"
                        
                        if item_key not in st.session_state or force:
                            st.session_state[item_key] = r["Item Name"]
                        if dir_key not in st.session_state or force:
                            st.session_state[dir_key] = r["Direction"]
                        if qty_key not in st.session_state or force:
                            st.session_state[qty_key] = float(r["Qty"])
                        if rate_key not in st.session_state or force:
                            st.session_state[rate_key] = float(r["Rate"])
                        if gst_key not in st.session_state or force:
                            st.session_state[gst_key] = float(r.get("GST Rate", 0) or 0)

            # Change detection to load correct voucher state
            if "ev_sel_id_prev" not in st.session_state:
                st.session_state.ev_sel_id_prev = sel_v_id
                init_edit_voucher_state(sel_v_id, v_row, e_df, stock_lines(), force=True)
            elif st.session_state.ev_sel_id_prev != sel_v_id:
                st.session_state.ev_sel_id_prev = sel_v_id
                init_edit_voucher_state(sel_v_id, v_row, e_df, stock_lines(), force=True)

            ev_date = st.date_input("Date", value=datetime.strptime(str(v_row["Date"]), "%Y-%m-%d").date(), key=f"ev_date_val_{sel_v_id}")
            ev_ref  = st.text_input("Reference No.", value=str(v_row["Reference"]) if pd.notna(v_row["Reference"]) else "", key=f"ev_ref_val_{sel_v_id}")
            ev_nar  = st.text_input("Narration", value=str(v_row["Narration"]) if pd.notna(v_row["Narration"]) else "", key=f"ev_nar_val_{sel_v_id}")

            # ── Edit Section 1: Ledger Entries ─────────────────────────
            is_prod = v_row["Type"] == "production"
            ev_entries_data = []
            
            if not is_prod:
                st.markdown("**Ledger Entries** — debit total must equal credit total")
                ev_n_rows = st.session_state[f"ev_n_rows_{sel_v_id}"]
                
                for i in range(ev_n_rows):
                    c1, c2, c3 = st.columns([4, 2, 2])
                    acc  = c1.selectbox("Account", acc_names, key=f"ev_acc_{sel_v_id}_{i}")
                    side = c2.selectbox("Side", ["Debit", "Credit"], key=f"ev_side_{sel_v_id}_{i}")
                    amt  = c3.number_input("Amount", min_value=0.0, step=0.01, key=f"ev_amt_{sel_v_id}_{i}", format="%.2f")
                    dr = amt if side == "Debit"  else 0.0
                    cr = amt if side == "Credit" else 0.0
                    ev_entries_data.append({"account_id": acc_map[acc], "account_name": acc, "debit": dr, "credit": cr})
                
                col1, col2 = st.columns(2)
                if col1.button("+ Edit Ledger Row", key=f"ev_add_dr_{sel_v_id}"):
                    st.session_state[f"ev_n_rows_{sel_v_id}"] += 1
                    i = st.session_state[f"ev_n_rows_{sel_v_id}"] - 1
                    st.session_state[f"ev_acc_{sel_v_id}_{i}"] = acc_names[0]
                    st.session_state[f"ev_side_{sel_v_id}_{i}"] = "Credit" if i % 2 == 1 else "Debit"
                    st.session_state[f"ev_amt_{sel_v_id}_{i}"] = 0.0
                    st.rerun()
                if col2.button("- Edit Ledger Row", key=f"ev_rm_dr_{sel_v_id}") and ev_n_rows > 2:
                    i = st.session_state[f"ev_n_rows_{sel_v_id}"] - 1
                    st.session_state.pop(f"ev_acc_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_side_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_amt_{sel_v_id}_{i}", None)
                    st.session_state[f"ev_n_rows_{sel_v_id}"] -= 1
                    st.rerun()

            # ── Edit Section 2: Inventory Movements ────────────────────
            ev_stock_entries = []
            show_ev_stock = is_prod or v_row["Type"] in ("sales", "purchase")
            
            if show_ev_stock and not it_df.empty:
                st.markdown("---")
                st.markdown("**Inventory Movement**")
                ev_inv_rows = st.session_state[f"ev_inv_rows_{sel_v_id}"]
                
                for j in range(ev_inv_rows):
                    c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
                    itm  = c1.selectbox("Item",      list(it_map.keys()), key=f"ev_item_{sel_v_id}_{j}")
                    dire = c2.selectbox("Direction", ["in", "out"],        key=f"ev_dir_{sel_v_id}_{j}")
                    qty  = c3.number_input("Qty",      min_value=0.01, step=0.01, key=f"ev_qty_{sel_v_id}_{j}", format="%.2f")
                    rate = c4.number_input("Rate",     min_value=0.0,   step=0.01,  key=f"ev_rate_{sel_v_id}_{j}", format="%.2f")
                    gst  = c5.number_input("GST %",   min_value=0.0,   step=0.5,   key=f"ev_gst_{sel_v_id}_{j}", format="%.1f")
                    ev_stock_entries.append({"item_id": it_map[itm], "item_name": itm,
                                          "direction": dire, "qty": qty, "rate": rate, "gst_rate": gst})

                ic1, ic2 = st.columns(2)
                if ic1.button("+ Edit Item Row", key=f"ev_add_it_{sel_v_id}"):
                    st.session_state[f"ev_inv_rows_{sel_v_id}"] += 1
                    j = st.session_state[f"ev_inv_rows_{sel_v_id}"] - 1
                    st.session_state[f"ev_item_{sel_v_id}_{j}"] = list(it_map.keys())[0]
                    st.session_state[f"ev_dir_{sel_v_id}_{j}"] = "out" if v_row["Type"] == "sales" else "in"
                    st.session_state[f"ev_qty_{sel_v_id}_{j}"] = 0.0
                    st.session_state[f"ev_rate_{sel_v_id}_{j}"] = 0.0
                    st.session_state[f"ev_gst_{sel_v_id}_{j}"] = 0.0
                    st.rerun()
                if ic2.button("- Edit Item Row", key=f"ev_rm_it_{sel_v_id}") and ev_inv_rows > (1 if is_prod else 0):
                    j = st.session_state[f"ev_inv_rows_{sel_v_id}"] - 1
                    st.session_state.pop(f"ev_item_{sel_v_id}_{j}", None)
                    st.session_state.pop(f"ev_dir_{sel_v_id}_{j}", None)
                    st.session_state.pop(f"ev_qty_{sel_v_id}_{j}", None)
                    st.session_state.pop(f"ev_rate_{sel_v_id}_{j}", None)
                    st.session_state.pop(f"ev_gst_{sel_v_id}_{j}", None)
                    st.session_state[f"ev_inv_rows_{sel_v_id}"] -= 1
                    st.rerun()

            # Balance calculation and verification
            total_dr = round(sum(e["debit"]  for e in ev_entries_data), 2)
            total_cr = round(sum(e["credit"] for e in ev_entries_data), 2)
            
            if is_prod:
                bal_ok = len(ev_stock_entries) > 0
            else:
                bal_ok = total_dr == total_cr and total_dr > 0

            st.markdown("---")
            c1, c2 = st.columns(2)
            save_clicked   = c1.button("💾 Save Voucher Changes", type="primary", key=f"ev_save_btn_{sel_v_id}", disabled=not bal_ok)
            delete_clicked = c2.button("❌ Delete Voucher Entirely", key=f"ev_del_btn_{sel_v_id}")

            if save_clicked:
                _update_voucher_transaction(sel_v_id, ev_date, v_row["Type"], ev_ref.strip(), ev_nar.strip(), ev_entries_data, ev_stock_entries)
                st.success(f"Voucher #{sel_v_id} updated successfully!")
                
                # Cleanup session states
                st.session_state.pop(f"ev_n_rows_{sel_v_id}", None)
                st.session_state.pop(f"ev_inv_rows_{sel_v_id}", None)
                st.session_state.pop("ev_sel_id_prev", None)
                for i in range(100):
                    st.session_state.pop(f"ev_acc_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_side_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_amt_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_item_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_dir_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_qty_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_rate_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_gst_{sel_v_id}_{i}", None)
                
                load_data(); st.rerun()

            if delete_clicked:
                _delete_rows_where("Vouchers", "ID", sel_v_id)
                _delete_rows_where("Entries", "Voucher ID", sel_v_id)
                _delete_rows_where("Stock Lines", "Voucher ID", sel_v_id)
                st.success(f"Voucher #{sel_v_id} deleted successfully.")
                
                # Cleanup session states
                st.session_state.pop(f"ev_n_rows_{sel_v_id}", None)
                st.session_state.pop(f"ev_inv_rows_{sel_v_id}", None)
                st.session_state.pop("ev_sel_id_prev", None)
                for i in range(100):
                    st.session_state.pop(f"ev_acc_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_side_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_amt_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_item_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_dir_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_qty_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_rate_{sel_v_id}_{i}", None)
                    st.session_state.pop(f"ev_gst_{sel_v_id}_{i}", None)
                
                load_data(); st.rerun()

# ── PRINTING & REPORT HELPERS ──────────────────────────────────────
import base64

def show_print_link(title, subtitle, html_table_content):
    # Strip newlines from content to prevent syntax errors
    clean_content = "".join([line.strip() for line in html_table_content.split("\n")])
    
    html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: 'Courier New', Courier, monospace;
            color: #000;
            background: #fff;
            margin: 30px;
            font-size: 13px;
            line-height: 1.4;
        }}
        h1 {{
            font-size: 18px;
            margin: 0 0 4px 0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .subtitle {{
            font-size: 12px;
            color: #333;
            margin-bottom: 20px;
            border-bottom: 2px solid #000;
            padding-bottom: 10px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }}
        th {{
            border-bottom: 2px solid #000;
            border-top: 2px solid #000;
            text-align: left;
            padding: 6px 8px;
            font-weight: bold;
        }}
        td {{
            padding: 6px 8px;
            border-bottom: 1px dashed #ccc;
            vertical-align: top;
            white-space: pre-line;
        }}
        .num {{
            text-align: right;
        }}
        .total-row td {{
            font-weight: bold;
            border-top: 1.5px solid #000;
            border-bottom: 2px double #000;
        }}
        .muted {{
            color: #555;
            font-size: 12px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 40px;
        }}
        @media print {{
            body {{ margin: 0; }}
            .no-print {{ display: none !important; }}
        }}
    </style>
</head>
<body onload="window.print()">
    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
        <div style="flex-grow: 1;">
            <h1>{title}</h1>
            <div class="subtitle">{subtitle}</div>
        </div>
        <button onclick="window.print()" class="no-print" style="
            background: #000; color: #fff; border: none; padding: 6px 15px;
            font-family: monospace; font-weight: bold; cursor: pointer;
        ">PRINT</button>
    </div>
    {clean_content}
</body>
</html>"""
    
    filename = f"{title.replace(' ', '_').lower()}.html"
    
    # Render native Streamlit download button
    st.download_button(
        label="📥 Download & Print Report",
        data=html_template,
        file_name=filename,
        mime="text/html",
        key=f"dl_btn_{title.replace(' ', '_').lower()}_{subtitle.replace(' ', '_').lower()}"
    )

INLINE_CSS = """
<style>
    .report-table-wrapper {
        background-color: #0c0f16;
        border: 1px solid #1f2937;
        border-radius: 4px;
        padding: 16px;
        margin-bottom: 20px;
    }
    .report-table-wrapper table {
        width: 100%;
        border-collapse: collapse;
        color: #d6e4f7;
        font-family: monospace;
        font-size: 13.5px;
    }
    .report-table-wrapper th {
        border-bottom: 2px solid #374151;
        text-align: left;
        padding: 8px;
        color: #f5a623;
        font-weight: 600;
    }
    .report-table-wrapper td {
        border-bottom: 1px dashed #1f2937;
        padding: 8px;
        vertical-align: top;
        white-space: pre-line;
    }
    .report-table-wrapper tr.total-row td {
        font-weight: bold;
        border-top: 1.5px solid #374151;
        border-bottom: 2px double #374151;
    }
    .report-table-wrapper .num {
        text-align: right;
    }
    .report-table-wrapper .badge {
        text-transform: uppercase;
        font-size: 10px;
        padding: 2px 6px;
        background: #1e293b;
        border-radius: 2px;
        color: #f5a623;
        font-weight: 600;
    }
    .report-table-wrapper .item-line {
        color: #9ca3af;
        font-size: 12px;
        padding-top: 3px;
        display: block;
    }
    .report-table-wrapper .grid-container {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 20px;
    }
    @media (max-width: 768px) {
        .report-table-wrapper .grid-container {
            grid-template-columns: 1fr;
        }
    }
</style>
"""


# ── P&L ───────────────────────────────────────────────────────────
def page_pl():
    st.title("📈 Profit & Loss")
    ensure_loaded()

    use_filter = st.checkbox("Filter by date range")
    from_d = to_d = None
    if use_filter:
        c1, c2 = st.columns(2)
        from_d = c1.date_input("From")
        to_d   = c2.date_input("To", value=date.today())

    income, expense, ti, te, np_ = compute_pl(from_d, to_d)
    
    # Format subtitle / range
    subtitle_lbl = "All time"
    if from_d and to_d: subtitle_lbl = f"Period: {from_d} to {to_d}"
    elif from_d: subtitle_lbl = f"From {from_d} onward"
    elif to_d: subtitle_lbl = f"Up to {to_d}"

    # Build P&L HTML for both screen and printing
    def build_pl_table_html(is_printable=False):
        grid_class = "grid" if is_printable else "grid-container"
        border_style = "border-bottom: 1px dashed #ccc;" if is_printable else "border-bottom: 1px dashed #1f2937;"
        top_border = "border-top: 1.5px solid #000;" if is_printable else "border-top: 1.5px solid #374151;"
        double_border = "border-top: 2px solid #000; border-bottom: 2px double #000;" if is_printable else "border-top: 2px solid #374151; border-bottom: 2px double #374151;"
        
        html = f"""
        <div class="{grid_class}">
            <div>
                <h3 style="border-bottom: 2px solid {'#000' if is_printable else '#f5a623'}; padding-bottom: 5px; margin-bottom: 10px; text-transform: uppercase; font-size: 14px;">Income</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tbody>
        """
        for inc in income:
            html += f"""
                        <tr style="{border_style}">
                            <td style="padding: 6px 8px;">{inc['Name']}</td>
                            <td style="padding: 6px 8px; text-align: right;">{inc['Amount']:,.2f}</td>
                        </tr>
            """
        html += f"""
                        <tr style="font-weight: bold; {top_border}">
                            <td style="padding: 6px 8px;">Total Income</td>
                            <td style="padding: 6px 8px; text-align: right;">{ti:,.2f}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            
            <div>
                <h3 style="border-bottom: 2px solid {'#000' if is_printable else '#f5a623'}; padding-bottom: 5px; margin-bottom: 10px; text-transform: uppercase; font-size: 14px;">Expenses</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tbody>
        """
        for exp in expense:
            html += f"""
                        <tr style="{border_style}">
                            <td style="padding: 6px 8px;">{exp['Name']}</td>
                            <td style="padding: 6px 8px; text-align: right;">{exp['Amount']:,.2f}</td>
                        </tr>
            """
        html += f"""
                        <tr style="font-weight: bold; {top_border}">
                            <td style="padding: 6px 8px;">Total Expense</td>
                            <td style="padding: 6px 8px; text-align: right;">{te:,.2f}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
        <div style="margin-top: 20px; padding: 10px; {double_border} font-size: 15px; font-weight: bold; text-align: right;">
            Net {"Profit" if np_ >= 0 else "Loss"}: ₹{abs(np_):,.2f}
        </div>
        """
        return html

    show_print_link("Profit & Loss Statement", subtitle_lbl, build_pl_table_html(is_printable=True))

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Income",  f"₹{ti:,.2f}")
    c2.metric("Total Expense", f"₹{te:,.2f}")
    c3.metric("Net Profit" if np_ >= 0 else "Net Loss", f"₹{abs(np_):,.2f}",
              delta=("Profit ✅" if np_ >= 0 else "Loss ❌"), delta_color="normal" if np_ >= 0 else "inverse")

    st.markdown(INLINE_CSS, unsafe_allow_html=True)
    clean_pl_html = "".join([line.strip() for line in build_pl_table_html(is_printable=False).split("\n")])
    st.markdown(f'<div class="report-table-wrapper">{clean_pl_html}</div>', unsafe_allow_html=True)


# ── Balance Sheet ─────────────────────────────────────────────────
def page_bs():
    st.title("📊 Balance Sheet")
    ensure_loaded()

    as_of = st.date_input("As of", value=date.today())
    assets, liabs, eq_rows, np_, ta, tl, te = compute_bs(as_of)
    
    subtitle_lbl = f"As of {as_of}"

    def build_bs_table_html(is_printable=False):
        grid_class = "grid" if is_printable else "grid-container"
        border_style = "border-bottom: 1px dashed #ccc;" if is_printable else "border-bottom: 1px dashed #1f2937;"
        sub_border = "border-top: 1.5px solid #ccc;" if is_printable else "border-top: 1.5px solid #1f2937;"
        double_border = "border-top: 2px solid #000; border-bottom: 2px double #000;" if is_printable else "border-top: 2px solid #374151; border-bottom: 2px double #374151;"
        
        html = f"""
        <div class="{grid_class}">
            <div>
                <h3 style="border-bottom: 2px solid {'#000' if is_printable else '#f5a623'}; padding-bottom: 5px; margin-bottom: 10px; text-transform: uppercase; font-size: 14px;">Assets</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tbody>
        """
        for a in assets:
            html += f"""
                        <tr style="{border_style}">
                            <td style="padding: 6px 8px;">{a['Name']}</td>
                            <td style="padding: 6px 8px; text-align: right;">{a['Balance']:,.2f} Dr</td>
                        </tr>
            """
        html += f"""
                        <tr style="font-weight: bold; {double_border}">
                            <td style="padding: 6px 8px;">Total Assets</td>
                            <td style="padding: 6px 8px; text-align: right;">{ta:,.2f} Dr</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            
            <div>
                <h3 style="border-bottom: 2px solid {'#000' if is_printable else '#f5a623'}; padding-bottom: 5px; margin-bottom: 10px; text-transform: uppercase; font-size: 14px;">Liabilities & Equity</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tbody>
                        <tr><td colspan="2" style="font-weight: bold; padding: 4px 8px; text-decoration: underline; font-size: 11px;">LIABILITIES</td></tr>
        """
        for l in liabs:
            html += f"""
                        <tr style="{border_style}">
                            <td style="padding: 6px 8px; padding-left: 20px;">{l['Name']}</td>
                            <td style="padding: 6px 8px; text-align: right;">{l['Balance']:,.2f} Cr</td>
                        </tr>
            """
        html += f"""
                        <tr style="font-weight: bold; {sub_border}">
                            <td style="padding: 6px 8px;">Total Liabilities</td>
                            <td style="padding: 6px 8px; text-align: right;">{tl:,.2f} Cr</td>
                        </tr>
                        <tr><td colspan="2" style="font-weight: bold; padding: 12px 8px 4px; text-decoration: underline; font-size: 11px;">EQUITY</td></tr>
        """
        for eq in eq_rows:
            html += f"""
                        <tr style="{border_style}">
                            <td style="padding: 6px 8px; padding-left: 20px;">{eq['Name']}</td>
                            <td style="padding: 6px 8px; text-align: right;">{eq['Balance']:,.2f} Cr</td>
                        </tr>
            """
        html += f"""
                        <tr style="{border_style}">
                            <td style="padding: 6px 8px; padding-left: 20px;">Net Profit / (Loss)</td>
                            <td style="padding: 6px 8px; text-align: right;">{np_:,.2f} Cr</td>
                        </tr>
                        <tr style="font-weight: bold; {sub_border}">
                            <td style="padding: 6px 8px;">Total Equity</td>
                            <td style="padding: 6px 8px; text-align: right;">{te:,.2f} Cr</td>
                        </tr>
                        <tr style="font-weight: bold; {double_border}">
                            <td style="padding: 6px 8px;">Total Liab. & Equity</td>
                            <td style="padding: 6px 8px; text-align: right;">{tl + te:,.2f} Cr</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
        """
        return html

    show_print_link("Balance Sheet", subtitle_lbl, build_bs_table_html(is_printable=True))

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Assets",      f"₹{ta:,.2f}")
    c2.metric("Total Liabilities", f"₹{tl:,.2f}")
    c3.metric("Total Equity (incl. P&L)", f"₹{te:,.2f}")

    if round(ta, 2) == round(tl + te, 2):
        st.success("✅ Balance Sheet balances")
    else:
        st.warning(f"⚠️ Difference: ₹{abs(ta - tl - te):,.2f}")

    st.markdown(INLINE_CSS, unsafe_allow_html=True)
    clean_bs_html = "".join([line.strip() for line in build_bs_table_html(is_printable=False).split("\n")])
    st.markdown(f'<div class="report-table-wrapper">{clean_bs_html}</div>', unsafe_allow_html=True)


# ── Stock ─────────────────────────────────────────────────────────
def page_stock():
    st.title("📦 Stock Report")
    ensure_loaded()

    use_filter = st.checkbox("Filter by date range", key="stock_filter")
    from_d = to_d = None
    if use_filter:
        c1, c2 = st.columns(2)
        from_d = c1.date_input("From", key="sf")
        to_d   = c2.date_input("To", value=date.today(), key="st")

    stock = compute_stock(from_d, to_d)
    
    subtitle_lbl = "All time"
    if from_d and to_d: subtitle_lbl = f"Period: {from_d} to {to_d}"
    elif from_d: subtitle_lbl = f"From {from_d} onward"
    elif to_d: subtitle_lbl = f"Up to {to_d}"

    if not stock:
        st.info("No stock data.")
        return

    def build_stock_table_html(is_printable=False):
        border_style = "border-bottom: 1px dashed #ccc;" if is_printable else "border-bottom: 1px dashed #1f2937;"
        top_border = "border-bottom: 2px solid #000; border-top: 2px solid #000;" if is_printable else "border-bottom: 2px solid #374151;"
        
        html = f"""
        <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="{top_border}">
                    <th style="padding: 6px 8px; text-align: left;">Item</th>
                    <th style="padding: 6px 8px; text-align: right;">Opening</th>
                    <th style="padding: 6px 8px; text-align: right;">In</th>
                    <th style="padding: 6px 8px; text-align: right;">Out</th>
                    <th style="padding: 6px 8px; text-align: right;">Closing</th>
                </tr>
            </thead>
            <tbody>
        """
        for s in stock:
            html += f"""
                <tr style="{border_style}">
                    <td style="padding: 6px 8px;">{s['Item']} <span style="font-size:11px; opacity:0.75;">({s['Unit']})</span></td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Opening']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['In']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Out']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Closing']:,.2f}</td>
                </tr>
            """
        html += f"""
            </tbody>
        </table>
        """
        return html

    show_print_link("Stock Inventory Summary", subtitle_lbl, build_stock_table_html(is_printable=True))
    
    st.markdown(INLINE_CSS, unsafe_allow_html=True)
    clean_stock_html = "".join([line.strip() for line in build_stock_table_html(is_printable=False).split("\n")])
    st.markdown(f'<div class="report-table-wrapper">{clean_stock_html}</div>', unsafe_allow_html=True)


# ── Account Ledger ────────────────────────────────────────────────
def page_ledger():
    st.title("📒 Account Ledger")
    ensure_loaded()

    accs = accounts()
    if accs.empty: st.info("No accounts found."); return

    acc_map = {r["Name"]: r["ID"] for _, r in accs.iterrows()}
    sel     = st.selectbox("Select Account", list(acc_map.keys()))

    use_filter = st.checkbox("Filter by date range", key="led_filter")
    from_d = to_d = None
    if use_filter:
        c1, c2 = st.columns(2)
        from_d = c1.date_input("From", key="lf")
        to_d   = c2.date_input("To", value=date.today(), key="lt")

    acc_id  = acc_map[sel]
    acc_row = accs[accs["ID"] == acc_id].iloc[0]
    is_dr   = acc_row["Type"] in ("asset", "expense")
    natural = "debit" if is_dr else "credit"

    ob_val  = float(acc_row.get("Opening Balance", 0) or 0)
    ob_side = str(acc_row.get("Opening Side", "debit"))
    base    = ob_val if ob_side == natural else -ob_val

    e_df = entries(); v_df = vouchers(); sl_all = stock_lines()
    if e_df.empty or v_df.empty:
        st.info("No transactions."); return

    ae = e_df[e_df["Account ID"] == acc_id].copy()
    for col in ("Debit", "Credit"): ae[col] = pd.to_numeric(ae[col], errors="coerce").fillna(0)
    ae = ae.merge(
        v_df[["ID","Date","Type","Narration","Reference"]].rename(columns={"ID":"VID"}),
        left_on="Voucher ID", right_on="VID",
    ).sort_values("Date")

    if from_d: ae = ae[ae["Date"] >= str(from_d)]
    if to_d:   ae = ae[ae["Date"] <= str(to_d)]

    subtitle_lbl = f"Account: {sel} — "
    if from_d and to_d: subtitle_lbl += f"Period: {from_d} to {to_d}"
    elif from_d: subtitle_lbl += f"From {from_d} onward"
    elif to_d: subtitle_lbl += f"Up to {to_d}"
    else: subtitle_lbl += "All time"

    rows, running = [], base
    tot_dr = 0.0
    tot_cr = 0.0
    unit_qty_map = {}

    for _, r in ae.iterrows():
        dr_val = float(r["Debit"]) if r["Debit"] > 0 else 0.0
        cr_val = float(r["Credit"]) if r["Credit"] > 0 else 0.0
        tot_dr += dr_val
        tot_cr += cr_val

        delta   = (dr_val - cr_val) if is_dr else (cr_val - dr_val)
        running += delta
        
        # Pull item movements for this specific voucher
        vid = r["Voucher ID"]
        items_lines = []
        if not sl_all.empty:
            v_items = sl_all[sl_all["Voucher ID"] == vid]
            for _, itm in v_items.iterrows():
                gst = f" + {itm['GST Rate']}%" if float(itm.get("GST Rate", 0) or 0) > 0 else ""
                rate_str = f" @ ₹{float(itm['Rate']):,.2f}" if float(itm.get("Rate", 0) or 0) > 0 else ""
                qty_val = float(itm.get("Qty", 0) or 0)
                unit_val = str(itm.get("Unit", "pcs") or "pcs")
                line = f"{itm['Item Name']} — {qty_val:,.2f} {unit_val}{rate_str}{gst}"
                items_lines.append(line)

                unit_qty_map[unit_val] = unit_qty_map.get(unit_val, 0.0) + qty_val
        
        rows.append({
            "Date": r["Date"],
            "Type": r["Type"],
            "Reference": r["Reference"],
            "Narration": r["Narration"],
            "Items": items_lines,
            "Debit": dr_val,
            "Credit": cr_val,
            "Balance": round(running, 2)
        })

    tot_qty_label = ", ".join([f"{q:,.2f} {u}" for u, q in unit_qty_map.items()]) if unit_qty_map else ""

    def build_ledger_table_html(is_printable=False):
        border_style = "border-bottom: 1px dashed #ccc;" if is_printable else "border-bottom: 1px dashed #1f2937;"
        double_border = "border-top: 2px solid #000; border-bottom: 2px double #000;" if is_printable else "border-top: 2px solid #374151; border-bottom: 2px double #374151;"
        top_border = "border-bottom: 2px solid #000; border-top: 2px solid #000;" if is_printable else "border-bottom: 2px solid #374151;"
        
        html = f"""
        <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="{top_border}">
                    <th style="padding: 6px 8px; text-align: left;">Date</th>
                    <th style="padding: 6px 8px; text-align: left;">Type</th>
                    <th style="padding: 6px 8px; text-align: left;">Narration & Item Details</th>
                    <th style="padding: 6px 8px; text-align: right;">Debit</th>
                    <th style="padding: 6px 8px; text-align: right;">Credit</th>
                    <th style="padding: 6px 8px; text-align: right;">Balance</th>
                </tr>
            </thead>
            <tbody>
                <tr style="{border_style}">
                    <td></td>
                    <td></td>
                    <td style="color: {'#555' if is_printable else '#7a93b0'}; font-style: italic;">Opening Balance</td>
                    <td></td>
                    <td></td>
                    <td style="text-align: right; font-weight: bold;">{abs(base):,.2f} {"Dr" if base >= 0 else "Cr"}</td>
                </tr>
        """
        for r in rows:
            dr_str = f"{r['Debit']:,.2f}" if r['Debit'] > 0 else ""
            cr_str = f"{r['Credit']:,.2f}" if r['Credit'] > 0 else ""
            bal_str = f"{abs(r['Balance']):,.2f} {'Dr' if r['Balance'] >= 0 else 'Cr'}"
            
            # Format Narration cell
            narr_parts = []
            if r["Reference"]: narr_parts.append(f"<b>Ref: {r['Reference']}</b>")
            if r["Narration"]: narr_parts.append(r["Narration"])
            narr_line = " — ".join(narr_parts) if narr_parts else "—"
            
            # Add item lines
            item_lines_html = ""
            for itm in r["Items"]:
                if is_printable:
                    item_lines_html += f'<div style="font-size: 11px; color: #555; padding-left: 15px;">└─ {itm}</div>'
                else:
                    item_lines_html += f'<span class="item-line">└─ {itm}</span>'
            
            badge_style = "" if is_printable else 'class="badge"'
            
            html += f"""
                <tr style="{border_style}">
                    <td style="white-space: nowrap;">{r['Date']}</td>
                    <td><span {badge_style}>{r['Type']}</span></td>
                    <td style="line-height: 1.4;">
                        {narr_line}
                        {item_lines_html}
                    </td>
                    <td style="text-align: right;">{dr_str}</td>
                    <td style="text-align: right;">{cr_str}</td>
                    <td style="text-align: right;">{bal_str}</td>
                </tr>
            """
            
        cb_side = "Dr" if running >= 0 else "Cr"
        qty_footer_note = f' <span style="font-size:12px; font-weight:normal; opacity:0.85;">(Total Qty: {tot_qty_label})</span>' if tot_qty_label else ''
        html += f"""
                <tr style="font-weight: bold; {double_border}">
                    <td></td>
                    <td></td>
                    <td>Closing Balance{qty_footer_note}</td>
                    <td style="text-align: right;">{tot_dr:,.2f}</td>
                    <td style="text-align: right;">{tot_cr:,.2f}</td>
                    <td style="text-align: right;">{abs(running):,.2f} {cb_side}</td>
                </tr>
            </tbody>
        </table>
        """
        return html

    show_print_link("Account Ledger Statement", subtitle_lbl, build_ledger_table_html(is_printable=True))

    c1, c2, c3 = st.columns(3)
    c1.metric("Opening Balance", f"₹{base:,.2f}")
    if tot_qty_label:
        c2.metric("Total Quantity Transacted", tot_qty_label)
    else:
        c2.metric("Total Transactions", f"{len(rows)} entries")
    c3.metric("Closing Balance", f"₹{running:,.2f}")

    st.markdown(INLINE_CSS, unsafe_allow_html=True)
    clean_ledger_html = "".join([line.strip() for line in build_ledger_table_html(is_printable=False).split("\n")])
    st.markdown(f'<div class="report-table-wrapper">{clean_ledger_html}</div>', unsafe_allow_html=True)


# ── Item Ledger ───────────────────────────────────────────────────
def page_item_ledger():
    st.title("📦 Item Ledger")
    ensure_loaded()

    it_df = items()
    if it_df.empty:
        st.info("No items found.")
        return

    item_map = {r["Name"]: r["ID"] for _, r in it_df.iterrows()}
    sel      = st.selectbox("Select Item", list(item_map.keys()))

    use_filter = st.checkbox("Filter by date range", key="item_led_filter")
    from_d = to_d = None
    if use_filter:
        c1, c2 = st.columns(2)
        from_d = c1.date_input("From", key="il_f")
        to_d   = c2.date_input("To", value=date.today(), key="il_t")

    item_id  = item_map[sel]
    item_row = it_df[it_df["ID"] == item_id].iloc[0]
    unit_str = item_row.get("Unit", "pcs") or "pcs"
    oq_val   = float(item_row.get("Opening Qty", 0) or 0)

    sl_all = stock_lines(); v_df = vouchers(); e_all = entries()

    item_sl = pd.DataFrame()
    if not sl_all.empty and not v_df.empty:
        sl_sub = sl_all[sl_all["Item ID"] == item_id]
        if not sl_sub.empty:
            item_sl = sl_sub.merge(
                v_df[["ID", "Date", "Type", "Reference", "Narration"]].rename(columns={"ID": "VID"}),
                left_on="Voucher ID", right_on="VID",
            ).sort_values(["Date", "VID"])

            item_sl["Qty"] = pd.to_numeric(item_sl["Qty"], errors="coerce").fillna(0)
            item_sl["Rate"] = pd.to_numeric(item_sl["Rate"], errors="coerce").fillna(0)
            item_sl["GST Rate"] = pd.to_numeric(item_sl["GST Rate"], errors="coerce").fillna(0)

    # Calculate opening stock balance prior to from_d
    pre_in = 0.0
    pre_out = 0.0
    if not item_sl.empty and from_d:
        pre_sl = item_sl[item_sl["Date"] < str(from_d)]
        if not pre_sl.empty:
            pre_in  = float(pre_sl[pre_sl["Direction"] == "in"]["Qty"].sum())
            pre_out = float(pre_sl[pre_sl["Direction"] == "out"]["Qty"].sum())

    base_qty = oq_val + pre_in - pre_out

    # Filter by date range for display
    display_sl = item_sl.copy() if not item_sl.empty else pd.DataFrame()
    if not display_sl.empty:
        if from_d: display_sl = display_sl[display_sl["Date"] >= str(from_d)]
        if to_d:   display_sl = display_sl[display_sl["Date"] <= str(to_d)]

    subtitle_lbl = f"Item: {sel} ({unit_str}) — "
    if from_d and to_d: subtitle_lbl += f"Period: {from_d} to {to_d}"
    elif from_d: subtitle_lbl += f"From {from_d} onward"
    elif to_d: subtitle_lbl += f"Up to {to_d}"
    else: subtitle_lbl += "All time"

    rows = []
    running_qty = base_qty
    tot_in_period = 0.0
    tot_out_period = 0.0

    if not display_sl.empty:
        for _, r in display_sl.iterrows():
            qty = float(r["Qty"])
            rate = float(r["Rate"])
            gst_r = float(r["GST Rate"])
            direction = str(r["Direction"]).lower()

            qty_in  = qty if direction == "in" else 0.0
            qty_out = qty if direction == "out" else 0.0
            tot_in_period += qty_in
            tot_out_period += qty_out

            running_qty += (qty_in - qty_out)
            line_val = qty * rate

            vid = r["Voucher ID"]
            party_str = ""
            if not e_all.empty:
                v_entries = e_all[e_all["Voucher ID"] == vid]
                if not v_entries.empty and "Account Name" in v_entries.columns:
                    account_names = [a for a in v_entries["Account Name"].unique() if a]
                    if account_names:
                        party_str = ", ".join(account_names)

            rows.append({
                "Date": r["Date"],
                "Type": r["Type"],
                "Reference": r["Reference"],
                "Narration": r["Narration"],
                "Party": party_str,
                "Direction": direction,
                "QtyIn": qty_in,
                "QtyOut": qty_out,
                "Rate": rate,
                "GSTRate": gst_r,
                "Amount": line_val,
                "BalanceQty": round(running_qty, 4)
            })

    def build_item_ledger_table_html(is_printable=False):
        border_style = "border-bottom: 1px dashed #ccc;" if is_printable else "border-bottom: 1px dashed #1f2937;"
        double_border = "border-top: 2px solid #000; border-bottom: 2px double #000;" if is_printable else "border-top: 2px solid #374151; border-bottom: 2px double #374151;"
        top_border = "border-bottom: 2px solid #000; border-top: 2px solid #000;" if is_printable else "border-bottom: 2px solid #374151;"
        
        html = f"""
        <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="{top_border}">
                    <th style="padding: 6px 8px; text-align: left;">Date</th>
                    <th style="padding: 6px 8px; text-align: left;">Type</th>
                    <th style="padding: 6px 8px; text-align: left;">Reference / Party & Narration</th>
                    <th style="padding: 6px 8px; text-align: right;">In Qty</th>
                    <th style="padding: 6px 8px; text-align: right;">Out Qty</th>
                    <th style="padding: 6px 8px; text-align: right;">Stock Balance ({unit_str})</th>
                </tr>
            </thead>
            <tbody>
                <tr style="{border_style}">
                    <td></td>
                    <td></td>
                    <td style="color: {'#555' if is_printable else '#7a93b0'}; font-style: italic;">Opening Balance</td>
                    <td></td>
                    <td></td>
                    <td style="text-align: right; font-weight: bold;">{base_qty:,.2f} {unit_str}</td>
                </tr>
        """
        for r in rows:
            in_str   = f"{r['QtyIn']:,.2f}" if r['QtyIn'] > 0 else ""
            out_str  = f"{r['QtyOut']:,.2f}" if r['QtyOut'] > 0 else ""
            bal_str  = f"{r['BalanceQty']:,.2f} {unit_str}"

            narr_parts = []
            if r["Reference"]: narr_parts.append(f"<b>Ref: {r['Reference']}</b>")
            if r["Party"]: narr_parts.append(f"Account: {r['Party']}")
            if r["Narration"]: narr_parts.append(r["Narration"])
            narr_line = " — ".join(narr_parts) if narr_parts else "—"

            badge_style = "" if is_printable else 'class="badge"'
            
            html += f"""
                <tr style="{border_style}">
                    <td style="white-space: nowrap;">{r['Date']}</td>
                    <td><span {badge_style}>{r['Type']}</span></td>
                    <td style="line-height: 1.4;">{narr_line}</td>
                    <td style="text-align: right; color: {'#008000' if is_printable else '#4ade80'};">{in_str}</td>
                    <td style="text-align: right; color: {'#cc0000' if is_printable else '#f87171'};">{out_str}</td>
                    <td style="text-align: right; font-weight: 600;">{bal_str}</td>
                </tr>
            """
            
        html += f"""
                <tr style="font-weight: bold; {double_border}">
                    <td></td>
                    <td></td>
                    <td>Closing Stock Balance</td>
                    <td style="text-align: right; color: {'#008000' if is_printable else '#4ade80'};">{tot_in_period:,.2f}</td>
                    <td style="text-align: right; color: {'#cc0000' if is_printable else '#f87171'};">{tot_out_period:,.2f}</td>
                    <td style="text-align: right;">{running_qty:,.2f} {unit_str}</td>
                </tr>
            </tbody>
        </table>
        """
        return html

    show_print_link("Item Ledger Statement", subtitle_lbl, build_item_ledger_table_html(is_printable=True))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Opening Stock", f"{base_qty:,.2f} {unit_str}")
    c2.metric("Total Inward", f"{tot_in_period:,.2f} {unit_str}")
    c3.metric("Total Outward", f"{tot_out_period:,.2f} {unit_str}")
    c4.metric("Closing Stock", f"{running_qty:,.2f} {unit_str}")

    st.markdown(INLINE_CSS, unsafe_allow_html=True)
    clean_item_led_html = "".join([line.strip() for line in build_item_ledger_table_html(is_printable=False).split("\n")])
    st.markdown(f'<div class="report-table-wrapper">{clean_item_led_html}</div>', unsafe_allow_html=True)

# ================================================================
#  MAIN
# ================================================================
def main():
    # Load settings
    company_name = os.getenv("COMPANY_NAME") or st.secrets.get("company_name") or "ERP Ledger"
    app_password = os.getenv("APP_PASSWORD") or st.secrets.get("app_password")

    st.set_page_config(
        page_title=company_name,
        page_icon="📒",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Password lock
    if app_password:
        if "authenticated" not in st.session_state:
            st.session_state["authenticated"] = False
            
        if not st.session_state["authenticated"]:
            st.markdown("<br><br>", unsafe_allow_html=True)
            c1, c2, c3 = st.columns([1, 2, 1])
            with c2:
                st.markdown(f"<h2 style='text-align: center;'>🔒 {company_name}</h2>", unsafe_allow_html=True)
                st.write("Please enter the login password to access this system.")
                typed = st.text_input("Password", type="password", key="app_pwd_input")
                if st.button("Unlock System", use_container_width=True, type="primary"):
                    if typed == app_password:
                        st.session_state["authenticated"] = True
                        st.rerun()
                    else:
                        st.error("Incorrect password. Please try again.")
            return

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { background: #0f1623; }
    .stMetric label { font-size: 11px; color: #7a93b0; }
    .stMetric [data-testid="stMetricValue"] { font-size: 22px; font-weight: 700; }
    </style>""", unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(f"## 📒 {company_name}")
        st.caption(f"Database: Supabase Cloud")
        st.divider()
        page = st.radio("", [
            "📊 Dashboard",
            "📑 Vouchers",
            "🏦 Accounts",
            "📦 Items",
            "── Reports ──",
            "📈 P & L",
            "📊 Balance Sheet",
            "📦 Stock",
            "📒 Account Ledger",
            "📦 Item Ledger",
        ], label_visibility="collapsed")
        st.divider()
        if st.button("🔄 Refresh Data", use_container_width=True):
            for k in ("accounts","items","vouchers","entries","stock_lines"):
                st.session_state.pop(k, None)
            st.rerun()
        st.caption(f"Loaded: {st.session_state.get('loaded_at','—')}")

    pages = {
        "📊 Dashboard":      page_dashboard,
        "📑 Vouchers":       page_vouchers,
        "🏦 Accounts":       page_accounts,
        "📦 Items":          page_items,
        "📈 P & L":          page_pl,
        "📊 Balance Sheet":  page_bs,
        "📦 Stock":          page_stock,
        "📒 Account Ledger": page_ledger,
        "📦 Item Ledger":    page_item_ledger,
    }
    fn = pages.get(page)
    if fn: fn()

if __name__ == "__main__":
    main()

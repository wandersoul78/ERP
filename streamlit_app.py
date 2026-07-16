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
#  CONNECTION & DATA LOADING
# ================================================================
def _df(name: str) -> pd.DataFrame:
    if not DATABASE_URL:
        st.error("Database connection URL not set. Please set SUPABASE_DB_URL in .env or st.secrets.")
        return pd.DataFrame()

    conn = psycopg2.connect(DATABASE_URL)
    
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
        conn.close()
        return pd.DataFrame()

    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Rename columns to match the old format
    df.rename(columns=col_map, inplace=True)
    return df

def load_data():
    st.session_state["accounts"]    = _df("Accounts")
    st.session_state["items"]       = _df("Items")
    st.session_state["vouchers"]    = _df("Vouchers")
    st.session_state["entries"]     = _df("Entries")
    st.session_state["stock_lines"] = _df("Stock Lines")
    st.session_state["loaded_at"]   = datetime.now().strftime("%H:%M:%S")

def ensure_loaded():
    if "accounts" not in st.session_state:
        load_data()

def accounts()    -> pd.DataFrame: return st.session_state.get("accounts", pd.DataFrame())
def items()       -> pd.DataFrame: return st.session_state.get("items", pd.DataFrame())
def vouchers()    -> pd.DataFrame: return st.session_state.get("vouchers", pd.DataFrame())
def entries()     -> pd.DataFrame: return st.session_state.get("entries", pd.DataFrame())
def stock_lines() -> pd.DataFrame: return st.session_state.get("stock_lines", pd.DataFrame())

# ================================================================
#  WRITE HELPERS
# ================================================================
def _next_id(df: pd.DataFrame, col: str = "ID") -> int:
    if df.empty or col not in df.columns or len(df) == 0:
        return 1
    vals = pd.to_numeric(df[col], errors="coerce").dropna()
    return int(vals.max()) + 1 if len(vals) > 0 else 1

def _append(tab: str, row: list):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    if tab == "Accounts":
        cur.execute("INSERT INTO accounts (id, name, type, opening_balance, opening_side) VALUES (%s, %s, %s, %s, %s)", row)
    elif tab == "Items":
        cur.execute("INSERT INTO items (id, name, unit, opening_qty, opening_rate) VALUES (%s, %s, %s, %s, %s)", row)
    elif tab == "Vouchers":
        cur.execute("INSERT INTO vouchers (id, date, type, reference, narration, created_at) VALUES (%s, %s, %s, %s, %s, %s)", row)
    elif tab == "Entries":
        # skip Account Name (row[3])
        val = [row[0], row[1], row[2], row[4], row[5]]
        cur.execute("INSERT INTO voucher_entries (id, voucher_id, account_id, debit, credit) VALUES (%s, %s, %s, %s, %s)", val)
    elif tab == "Stock Lines":
        # skip Item Name (row[3])
        val = [row[0], row[1], row[2], row[4], row[5], row[6], row[7]]
        cur.execute("INSERT INTO voucher_items (id, voucher_id, item_id, direction, qty, rate, gst_rate) VALUES (%s, %s, %s, %s, %s, %s, %s)", val)
    conn.commit()
    conn.close()

def _delete_rows_where(tab: str, col: str, value):
    conn = psycopg2.connect(DATABASE_URL)
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
    conn.close()

def _update_row_where(tab: str, col_id: str, id_val, new_values: list):
    conn = psycopg2.connect(DATABASE_URL)
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
    conn.close()

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
                for col in ("Qty", "Rate"):
                    lines[col] = pd.to_numeric(lines[col], errors="coerce").fillna(0)

        in_l  = lines[lines["Direction"] == "in"]  if not lines.empty else pd.DataFrame()
        out_l = lines[lines["Direction"] == "out"] if not lines.empty else pd.DataFrame()
        q_in  = float(in_l["Qty"].sum())  if not in_l.empty else 0
        q_out = float(out_l["Qty"].sum()) if not out_l.empty else 0
        v_in  = float((in_l["Qty"] * in_l["Rate"]).sum()) if not in_l.empty else 0

        oq = float(it.get("Opening Qty",  0) or 0)
        or_ = float(it.get("Opening Rate", 0) or 0)
        ov  = oq * or_
        tot_q = oq + q_in
        tot_v = ov + v_in
        avg_r = tot_v / tot_q if tot_q > 0 else 0
        cq    = round(oq + q_in - q_out, 4)
        result.append({
            "Item": it["Name"], "Unit": it["Unit"],
            "Opening": round(oq, 2), "In": round(q_in, 2), "Out": round(q_out, 2),
            "Closing": cq, "Avg Rate": round(avg_r, 2), "Value": round(cq * avg_r, 2),
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

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vouchers", len(v_df) if not v_df.empty else 0)
    c2.metric("Accounts", len(accs) if not accs.empty else 0)

    _, _, ti, te, np_ = compute_pl()
    c3.metric("Net Profit / Loss", f"₹{np_:,.2f}")

    # Cash balance
    if not accs.empty and not e_df.empty:
        cash = accs[accs["Name"].str.lower() == "cash"]
        if not cash.empty:
            cr = cash.iloc[0]
            ae = e_df[e_df["Account ID"] == cr["ID"]]
            for col in ("Debit", "Credit"):
                ae = ae.copy()
                ae[col] = pd.to_numeric(ae[col], errors="coerce").fillna(0)
            ob = float(cr.get("Opening Balance", 0) or 0)
            base = ob if cr.get("Opening Side") == "debit" else -ob
            cash_bal = round(base + ae["Debit"].sum() - ae["Credit"].sum(), 2)
            c4.metric("Cash Balance", f"₹{cash_bal:,.2f}")

    st.caption(f"Data as of: {st.session_state.get('loaded_at', '—')}  ·  Sheet: {SHEET_NAME}")

    if not v_df.empty:
        st.subheader("Recent Vouchers")
        st.dataframe(v_df.head(10), use_container_width=True, hide_index=True)

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
                    nid = _next_id(accs)
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
            oq   = st.number_input("Opening Qty",  min_value=0.0, step=0.001, format="%.3f")
            or_  = st.number_input("Opening Rate", min_value=0.0, step=0.01,  format="%.2f")
            if st.form_submit_button("Create"):
                if not name.strip():
                    st.error("Name required.")
                else:
                    nid = _next_id(it_df)
                    _append("Items", [nid, name.strip(), unit.strip() or "pcs", oq, or_])
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
                new_oq   = st.number_input("Opening Qty", min_value=0.0, step=0.001, value=float(it_row["Opening Qty"]), format="%.3f")
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

        stock_entries = []
        for j in range(st.session_state.inv_rows):
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            itm  = c1.selectbox("Item",      list(it_map.keys()), key=f"v_item_{j}")
            dire = c2.selectbox("Direction", ["in", "out"],        key=f"v_dir_{j}")
            qty  = c3.number_input("Qty",  min_value=0.001, step=0.001, key=f"v_qty_{j}", format="%.3f")
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
                    qty  = c3.number_input("Qty",      min_value=0.001, step=0.001, key=f"v_qty_{j}", format="%.3f")
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
            v_id  = _next_id(v_df)
            e_id  = _next_id(e_df)
            sl_id = _next_id(stock_lines())
            _append("Vouchers", [v_id, str(v_date), v_type, reference, narration, datetime.now().isoformat()])
            ei = 0
            for e in entry_data:
                if e["debit"] > 0 or e["credit"] > 0:
                    _append("Entries", [e_id+ei, v_id, e["account_id"], e["account_name"],
                                        e["debit"], e["credit"]])
                    ei += 1
            for i, s in enumerate(stock_entries):
                _append("Stock Lines", [sl_id+i, v_id, s["item_id"], s["item_name"],
                                        s["direction"], s["qty"], s["rate"], s["gst_rate"]])
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
                    qty  = c3.number_input("Qty",      min_value=0.001, step=0.001, key=f"ev_qty_{sel_v_id}_{j}", format="%.3f")
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
                # 1. Update Vouchers worksheet
                _update_row_where("Vouchers", "ID", sel_v_id, [sel_v_id, str(ev_date), v_row["Type"], ev_ref.strip(), ev_nar.strip(), v_row["Created At"]])
                
                # 2. Update Entries
                _delete_rows_where("Entries", "Voucher ID", sel_v_id)
                new_entries = []
                next_e_id = _next_id(e_df)
                for idx, e in enumerate(ev_entries_data):
                    if e["debit"] > 0 or e["credit"] > 0:
                        new_entries.append([next_e_id + idx, sel_v_id, e["account_id"], e["account_name"], e["debit"], e["credit"]])
                if new_entries:
                    _ws("Entries").append_rows(new_entries, value_input_option="USER_ENTERED")
                    
                # 3. Update Stock Lines
                _delete_rows_where("Stock Lines", "Voucher ID", sel_v_id)
                new_stock = []
                next_sl_id = _next_id(stock_lines())
                for idx, s in enumerate(ev_stock_entries):
                    new_stock.append([next_sl_id + idx, sel_v_id, s["item_id"], s["item_name"], s["direction"], s["qty"], s["rate"], s["gst_rate"]])
                if new_stock:
                    _ws("Stock Lines").append_rows(new_stock, value_input_option="USER_ENTERED")
                    
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
        double_border = "border-top: 2px solid #000; border-bottom: 2px double #000;" if is_printable else "border-top: 2px solid #374151; border-bottom: 2px double #374151;"
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
                    <th style="padding: 6px 8px; text-align: right;">Avg Rate</th>
                    <th style="padding: 6px 8px; text-align: right;">Value</th>
                </tr>
            </thead>
            <tbody>
        """
        total_val = 0.0
        for s in stock:
            total_val += s['Value']
            html += f"""
                <tr style="{border_style}">
                    <td style="padding: 6px 8px;">{s['Item']} <span style="font-size:11px; opacity:0.75;">({s['Unit']})</span></td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Opening']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['In']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Out']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Closing']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Avg Rate']:,.2f}</td>
                    <td style="padding: 6px 8px; text-align: right;">{s['Value']:,.2f}</td>
                </tr>
            """
        html += f"""
                <tr style="font-weight: bold; {double_border}">
                    <td colspan="6" style="padding: 6px 8px;">Total stock value</td>
                    <td style="padding: 6px 8px; text-align: right;">{total_val:,.2f}</td>
                </tr>
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
    for _, r in ae.iterrows():
        delta   = (r["Debit"]-r["Credit"]) if is_dr else (r["Credit"]-r["Debit"])
        running += delta
        
        # Pull item movements for this specific voucher
        vid = r["Voucher ID"]
        items_lines = []
        if not sl_all.empty:
            v_items = sl_all[sl_all["Voucher ID"] == vid]
            for _, itm in v_items.iterrows():
                gst = f" + {itm['GST Rate']}%" if float(itm.get("GST Rate", 0) or 0) > 0 else ""
                rate_str = f" @ ₹{float(itm['Rate']):,.2f}" if float(itm.get("Rate", 0) or 0) > 0 else ""
                line = f"{itm['Item Name']} — {float(itm['Qty']):,.3f} {itm.get('Unit', '')}{rate_str}{gst}"
                items_lines.append(line)
        
        rows.append({
            "Date": r["Date"],
            "Type": r["Type"],
            "Reference": r["Reference"],
            "Narration": r["Narration"],
            "Items": items_lines,
            "Debit": r["Debit"] if r["Debit"] > 0 else 0,
            "Credit": r["Credit"] if r["Credit"] > 0 else 0,
            "Balance": round(running, 2)
        })

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
        html += f"""
                <tr style="font-weight: bold; {double_border}">
                    <td></td>
                    <td></td>
                    <td>Closing Balance</td>
                    <td></td>
                    <td></td>
                    <td style="text-align: right;">{abs(running):,.2f} {cb_side}</td>
                </tr>
            </tbody>
        </table>
        """
        return html

    show_print_link("Account Ledger Statement", subtitle_lbl, build_ledger_table_html(is_printable=True))

    st.metric("Opening Balance", f"₹{base:,.2f}")
    
    st.markdown(INLINE_CSS, unsafe_allow_html=True)
    clean_ledger_html = "".join([line.strip() for line in build_ledger_table_html(is_printable=False).split("\n")])
    st.markdown(f'<div class="report-table-wrapper">{clean_ledger_html}</div>', unsafe_allow_html=True)
    
    st.metric("Closing Balance", f"₹{running:,.2f}")

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
    }
    fn = pages.get(page)
    if fn: fn()

if __name__ == "__main__":
    main()

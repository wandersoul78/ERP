"""
streamlit_app.py  -  ERP Ledger on Streamlit Cloud
===================================================
Full cloud ERP: Accounts, Items, Vouchers, Reports.
Backend: Google Sheets (via gspread).
Deploy to Streamlit Cloud. Add credentials in st.secrets.
"""

import streamlit as st
import gspread
import pandas as pd
from datetime import date, datetime
from google.oauth2.service_account import Credentials

SHEET_NAME  = st.secrets.get("sheet_name", "ERP Ledger") if hasattr(st, "secrets") else "ERP Ledger"
ACCOUNT_TYPES = ["asset", "liability", "income", "expense", "equity"]
VOUCHER_TYPES = ["sales", "purchase", "payment", "receipt", "journal", "production"]
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# ================================================================
#  CONNECTION
# ================================================================
@st.cache_resource(show_spinner="Connecting to Google Sheets…")
def get_spreadsheet():
    creds_info = dict(st.secrets["gcp_service_account"])
    # TOML stores \n as literal backslash-n; RSA key needs real newlines.
    if "private_key" in creds_info:
        creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open(SHEET_NAME)

# ================================================================
#  DATA LOADING  (session-state cache, refresh on demand)
# ================================================================
def _ws(name: str):
    return get_spreadsheet().worksheet(name)

def _df(name: str) -> pd.DataFrame:
    records = _ws(name).get_all_records()
    return pd.DataFrame(records) if records else pd.DataFrame()

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
    _ws(tab).append_row(row, value_input_option="USER_ENTERED")

def _delete_rows_where(tab: str, col: str, value):
    ws = _ws(tab)
    records = ws.get_all_records()
    idxs = [i + 2 for i, r in enumerate(records) if str(r.get(col, "")) == str(value)]
    for idx in reversed(idxs):
        ws.delete_rows(idx)

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

# ── Vouchers ──────────────────────────────────────────────────────
def page_vouchers():
    st.title("📑 Vouchers")
    ensure_loaded()
    v_df = vouchers()
    e_df = entries()
    accs = accounts()

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

    acc_map  = {r["Name"]: r["ID"] for _, r in accs.iterrows()}
    acc_names = list(acc_map.keys())

    v_type    = st.selectbox("Type",      VOUCHER_TYPES)
    v_date    = st.date_input("Date",     value=date.today())
    reference = st.text_input("Reference No.")
    narration = st.text_input("Narration")

    if v_type == "production":
        st.info("Production vouchers: record stock movements (In/Out) only.")
        it_df  = items()
        if it_df.empty:
            st.warning("No items found. Add items first."); return
        it_map  = {r["Name"]: r["ID"] for _, r in it_df.iterrows()}

        if "prod_rows" not in st.session_state:
            st.session_state.prod_rows = 1

        stock_entries = []
        for i in range(st.session_state.prod_rows):
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            itm  = c1.selectbox("Item",      list(it_map.keys()), key=f"pi_{i}")
            dire = c2.selectbox("Direction", ["in", "out"],        key=f"pd_{i}")
            qty  = c3.number_input("Qty",  min_value=0.001, step=0.001, key=f"pq_{i}", format="%.3f")
            rate = c4.number_input("Rate", min_value=0.0,   step=0.01,  key=f"pr_{i}", format="%.2f")
            stock_entries.append({"item_id": it_map[itm], "item_name": itm,
                                   "direction": dire, "qty": qty, "rate": rate, "gst_rate": 0})

        col1, col2 = st.columns(2)
        if col1.button("+ Row", key="prod_add"): st.session_state.prod_rows += 1; st.rerun()
        if col2.button("- Row", key="prod_rm") and st.session_state.prod_rows > 1:
            st.session_state.prod_rows -= 1; st.rerun()

        if st.button("✅ Save Production Voucher", type="primary"):
            v_id   = _next_id(v_df)
            sl_all = stock_lines()
            sl_id  = _next_id(sl_all)
            _append("Vouchers", [v_id, str(v_date), v_type, reference, narration, datetime.now().isoformat()])
            for i, s in enumerate(stock_entries):
                _append("Stock Lines", [sl_id+i, v_id, s["item_id"], s["item_name"],
                                        s["direction"], s["qty"], s["rate"], s["gst_rate"]])
            st.success(f"Production voucher #{v_id} saved.")
            del st.session_state["prod_rows"]
            load_data(); st.rerun()

    else:
        st.write("**Ledger Entries** — debit total must equal credit total")

        if "n_rows" not in st.session_state:
            st.session_state.n_rows = 2

        entry_data = []
        for i in range(st.session_state.n_rows):
            c1, c2, c3 = st.columns([3, 2, 2])
            acc = c1.selectbox("Account", acc_names, key=f"ea_{i}")
            dr  = c2.number_input("Debit",  min_value=0.0, step=0.01, key=f"ed_{i}", format="%.2f")
            cr  = c3.number_input("Credit", min_value=0.0, step=0.01, key=f"ec_{i}", format="%.2f")
            entry_data.append({"account_id": acc_map[acc], "account_name": acc, "debit": dr, "credit": cr})

        col1, col2 = st.columns(2)
        if col1.button("+ Row"): st.session_state.n_rows += 1; st.rerun()
        if col2.button("- Row") and st.session_state.n_rows > 2:
            st.session_state.n_rows -= 1; st.rerun()

        total_dr = sum(e["debit"]  for e in entry_data)
        total_cr = sum(e["credit"] for e in entry_data)
        bal_ok   = round(total_dr, 2) == round(total_cr, 2) and total_dr > 0

        bal_col = "normal" if bal_ok else "inverse"
        st.metric("Total Debit", f"₹{total_dr:,.2f}", delta=f"Cr: ₹{total_cr:,.2f}",
                  delta_color=("normal" if bal_ok else "inverse"))

        if st.button("✅ Save Voucher", type="primary", disabled=not bal_ok):
            v_id   = _next_id(v_df)
            e_id   = _next_id(e_df)
            _append("Vouchers", [v_id, str(v_date), v_type, reference, narration, datetime.now().isoformat()])
            ei = 0
            for e in entry_data:
                if e["debit"] > 0 or e["credit"] > 0:
                    _append("Entries", [e_id+ei, v_id, e["account_id"], e["account_name"],
                                        e["debit"], e["credit"]])
                    ei += 1
            st.success(f"Voucher #{v_id} saved!")
            del st.session_state["n_rows"]
            load_data(); st.rerun()

        if not bal_ok and total_dr > 0:
            st.error(f"Entries don't balance: Dr ₹{total_dr:,.2f} vs Cr ₹{total_cr:,.2f}")

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

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Income",  f"₹{ti:,.2f}")
    c2.metric("Total Expense", f"₹{te:,.2f}")
    c3.metric("Net Profit" if np_ >= 0 else "Net Loss", f"₹{abs(np_):,.2f}",
              delta=("Profit ✅" if np_ >= 0 else "Loss ❌"), delta_color="normal" if np_ >= 0 else "inverse")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Income")
        if income: st.dataframe(pd.DataFrame(income), use_container_width=True, hide_index=True)
        else: st.info("No income entries.")
    with col2:
        st.subheader("Expense")
        if expense: st.dataframe(pd.DataFrame(expense), use_container_width=True, hide_index=True)
        else: st.info("No expense entries.")

# ── Balance Sheet ─────────────────────────────────────────────────
def page_bs():
    st.title("📊 Balance Sheet")
    ensure_loaded()

    as_of = st.date_input("As of", value=date.today())
    assets, liabs, eq_rows, np_, ta, tl, te = compute_bs(as_of)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Assets",      f"₹{ta:,.2f}")
    c2.metric("Total Liabilities", f"₹{tl:,.2f}")
    c3.metric("Total Equity (incl. P&L)", f"₹{te:,.2f}")

    if round(ta, 2) == round(tl + te, 2):
        st.success("✅ Balance Sheet balances")
    else:
        st.warning(f"⚠️ Difference: ₹{abs(ta - tl - te):,.2f}")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Assets")
        if assets: st.dataframe(pd.DataFrame(assets), use_container_width=True, hide_index=True)
    with col2:
        st.subheader("Liabilities")
        if liabs: st.dataframe(pd.DataFrame(liabs), use_container_width=True, hide_index=True)
        st.subheader("Equity")
        if eq_rows: st.dataframe(pd.DataFrame(eq_rows), use_container_width=True, hide_index=True)
        st.metric("P&L (Net Profit)", f"₹{np_:,.2f}")

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
    if stock:
        df = pd.DataFrame(stock)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.metric("Total Stock Value", f"₹{df['Value'].sum():,.2f}")
    else:
        st.info("No stock data.")

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

    e_df = entries(); v_df = vouchers()
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

    st.metric("Opening Balance", f"₹{base:,.2f}")
    rows, running = [], base
    for _, r in ae.iterrows():
        delta   = (r["Debit"]-r["Credit"]) if is_dr else (r["Credit"]-r["Debit"])
        running += delta
        rows.append({"Date": r["Date"], "Type": r["Type"], "Reference": r["Reference"],
                     "Narration": r["Narration"], "Debit": r["Debit"], "Credit": r["Credit"],
                     "Balance": round(running, 2)})

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.metric("Closing Balance", f"₹{running:,.2f}")
    else:
        st.info("No transactions in the selected period.")

# ================================================================
#  MAIN
# ================================================================
def main():
    st.set_page_config(
        page_title="ERP Ledger",
        page_icon="📒",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { background: #0f1623; }
    .stMetric label { font-size: 11px; color: #7a93b0; }
    .stMetric [data-testid="stMetricValue"] { font-size: 22px; font-weight: 700; }
    </style>""", unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("## 📒 ERP Ledger")
        st.caption(f"Sheet: {SHEET_NAME}")
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

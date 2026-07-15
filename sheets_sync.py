"""
sheets_sync.py  -  Google Sheets sync for ERP Ledger (local to Sheets)
Auto-creates the ERP Ledger spreadsheet on first run.
Syncs on every DB write (trigger-only, no periodic overwrite).
"""

import sqlite3
import threading
import logging
import os
from datetime import datetime

log = logging.getLogger("SheetsSync")

TABS = {
    "Accounts":    ["ID", "Name", "Type", "Opening Balance", "Opening Side"],
    "Items":       ["ID", "Name", "Unit", "Opening Qty", "Opening Rate"],
    "Vouchers":    ["ID", "Date", "Type", "Reference", "Narration", "Created At"],
    "Entries":     ["ID", "Voucher ID", "Account ID", "Account Name", "Debit", "Credit"],
    "Stock Lines": ["ID", "Voucher ID", "Item ID", "Item Name", "Direction", "Qty", "Rate", "GST Rate"],
}


class SheetsSync:
    def __init__(self, db_path, creds_path, sheet_name="ERP Ledger"):
        self.db_path    = db_path
        self.creds_path = creds_path
        self.sheet_name = sheet_name
        self._gc        = None
        self._ss        = None
        self._url       = ""
        self._pending   = threading.Event()
        self._stop      = threading.Event()

    def setup(self):
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds    = Credentials.from_service_account_file(self.creds_path, scopes=scopes)
        self._gc = gspread.authorize(creds)
        try:
            self._ss = self._gc.open(self.sheet_name)
            log.info(f"[Sheets] Opened: {self.sheet_name}")
        except gspread.SpreadsheetNotFound:
            self._ss = self._gc.create(self.sheet_name)
            self._ss.share(None, perm_type="anyone", role="reader")
            log.info(f"[Sheets] Created: {self.sheet_name}")

        existing = {ws.title for ws in self._ss.worksheets()}
        for tab, headers in TABS.items():
            if tab not in existing:
                ws = self._ss.add_worksheet(title=tab, rows=5000, cols=len(headers)+2)
                ws.update([headers])
        for dummy in ("Sheet1",):
            if dummy in existing:
                try: self._ss.del_worksheet(self._ss.worksheet(dummy))
                except: pass

        self._url = self._ss.url
        log.info(f"[Sheets] Ready -> {self._url}")
        return self._url

    def sync_all(self):
        if not self._ss: return
        try:
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            self._write_tab("Accounts", TABS["Accounts"],
                [[r["id"],r["name"],r["type"],r["opening_balance"],r["opening_side"]]
                 for r in con.execute("SELECT * FROM accounts ORDER BY type,name")])
            self._write_tab("Items", TABS["Items"],
                [[r["id"],r["name"],r["unit"],r["opening_qty"],r["opening_rate"]]
                 for r in con.execute("SELECT * FROM items ORDER BY name")])
            self._write_tab("Vouchers", TABS["Vouchers"],
                [[r["id"],r["date"],r["type"],r["reference"],r["narration"],r["created_at"]]
                 for r in con.execute("SELECT * FROM vouchers ORDER BY date DESC,id DESC")])
            self._write_tab("Entries", TABS["Entries"],
                [[r["id"],r["voucher_id"],r["account_id"],r["account_name"],r["debit"],r["credit"]]
                 for r in con.execute("""SELECT ve.id,ve.voucher_id,ve.account_id,
                     a.name account_name,ve.debit,ve.credit
                     FROM voucher_entries ve JOIN accounts a ON a.id=ve.account_id
                     ORDER BY ve.voucher_id,ve.id""")])
            self._write_tab("Stock Lines", TABS["Stock Lines"],
                [[r["id"],r["voucher_id"],r["item_id"],r["item_name"],r["direction"],r["qty"],r["rate"],r["gst_rate"]]
                 for r in con.execute("""SELECT vi.id,vi.voucher_id,vi.item_id,
                     i.name item_name,vi.direction,vi.qty,vi.rate,vi.gst_rate
                     FROM voucher_items vi JOIN items i ON i.id=vi.item_id
                     ORDER BY vi.voucher_id,vi.id""")])
            con.close()
            log.info(f"[Sheets] Synced at {datetime.now().strftime('%H:%M:%S')}")
        except Exception as exc:
            log.error(f"[Sheets] Sync error: {exc}")

    def _write_tab(self, tab, headers, data):
        ws = self._ss.worksheet(tab)
        ws.clear()
        ws.update([headers]+data, value_input_option="USER_ENTERED")

    def trigger(self):
        self._pending.set()

    def start_background_sync(self):
        def _loop():
            self.sync_all()
            while not self._stop.is_set():
                self._pending.wait()
                self._pending.clear()
                if not self._stop.is_set(): self.sync_all()
        threading.Thread(target=_loop, daemon=True, name="sheets-sync").start()
        log.info("[Sheets] Background sync thread started")

    def stop(self):
        self._stop.set()
        self._pending.set()

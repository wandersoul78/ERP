const API = "/api";
let STATE = { accounts: [], items: [], vouchers: [] };
let activeTab = "dashboard";

async function api(path, opts){
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(data.error || "request failed");
  return data;
}

function fmt(n){
  n = Number(n||0);
  return n.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
}

function el(html){
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

async function loadAll(){
  const [accounts, items, vouchers, formulas] = await Promise.all([
    api('/accounts'), api('/items'), api('/vouchers'), api('/formulas').catch(()=>[])
  ]);
  STATE.accounts = accounts; STATE.items = items; STATE.vouchers = vouchers; STATE.formulas = formulas || [];
}

function switchTab(tab){
  activeTab = tab;
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('active', b.dataset.tab===tab));
  render();
}

document.getElementById('tabs').addEventListener('click', e=>{
  if(e.target.tagName==='BUTTON') switchTab(e.target.dataset.tab);
});

function render(){
  const app = document.getElementById('app');
  app.innerHTML = '';
  if(activeTab==='dashboard') app.appendChild(renderDashboard());
  if(activeTab==='voucher') app.appendChild(renderVoucherForm());
  if(activeTab==='vouchers') app.appendChild(renderVouchers());
  if(activeTab==='accounts') app.appendChild(renderAccounts());
  if(activeTab==='items') app.appendChild(renderItems());
  if(activeTab==='reports') app.appendChild(renderReports());
}

// ---------- Dashboard ----------
function renderDashboard(){
  const wrap = el(`<div></div>`);
  const stat = el(`
    <div class="statgrid">
      <div class="card stat">
        <div class="l">Account Ledgers</div>
        <div class="v">${STATE.accounts.length}</div>
      </div>
      <div class="card stat">
        <div class="l">Items Tracked</div>
        <div class="v">${STATE.items.length}</div>
      </div>
      <div class="card stat">
        <div class="l">Vouchers Recorded</div>
        <div class="v">${STATE.vouchers.length}</div>
      </div>
    </div>
  `);
  wrap.appendChild(stat);

  const ledgerCard = el(`<div class="card"><h2>Account Ledgers &amp; Closing Balances</h2></div>`);
  if(STATE.accounts.length === 0){
    ledgerCard.appendChild(el(`<div class="empty">No accounts added yet.</div>`));
  } else {
    const tbl = el(`<table>
      <thead><tr><th>Account Name</th><th>Category</th><th class="num">Closing Balance</th></tr></thead>
      <tbody></tbody></table>`);
    const tbody = tbl.querySelector('tbody');

    STATE.accounts.forEach(a => {
      let dr = 0, cr = 0;
      STATE.vouchers.forEach(v => {
        if(v.entries){
          v.entries.forEach(e => {
            if(e.account_id === a.id || e.account_name === a.name){
              dr += Number(e.debit || 0);
              cr += Number(e.credit || 0);
            }
          });
        }
      });

      const isDr = a.type === 'asset' || a.type === 'expense';
      const ob = Number(a.opening_balance || 0);
      const obSide = a.opening_side || 'debit';
      const base = obSide === (isDr ? 'debit' : 'credit') ? ob : -ob;
      const net = base + (isDr ? (dr - cr) : (cr - dr));
      const sideStr = net >= 0 ? (isDr ? 'Dr' : 'Cr') : (isDr ? 'Cr' : 'Dr');

      tbody.appendChild(el(`<tr>
        <td><strong>${a.name}</strong></td>
        <td><span class="pill ${a.type}">${a.type}</span></td>
        <td class="num"><strong>₹${fmt(Math.abs(net))}</strong> <span class="muted">${sideStr}</span></td>
      </tr>`));
    });
    ledgerCard.appendChild(tbl);
  }
  wrap.appendChild(ledgerCard);
  return wrap;
}

// ---------- Voucher form ----------
function renderVoucherForm(){
  const wrap = el(`<div></div>`);
  const card = el(`<div class="card"><h2>New voucher</h2></div>`);
  const form = el(`
    <div>
      <div id="voucher-msg"></div>
      <div class="grid2">
        <div><label>Date</label><input type="date" id="v-date" value="${new Date().toISOString().slice(0,10)}"></div>
        <div><label>Voucher type</label>
          <select id="v-type">
            <option value="sales">Sales</option>
            <option value="purchase">Purchase</option>
            <option value="payment">Payment</option>
            <option value="receipt">Receipt</option>
            <option value="journal">Journal</option>
            <option value="production">Production Log (Inventory Only)</option>
          </select>
        </div>
      </div>
      <div class="grid2">
        <div><label>Invoice / Slip No. <span class="muted" style="font-weight:400;">(optional)</span></label><input type="text" id="v-reference" placeholder="e.g. INV-104"></div>
        <div><label>Narration</label><input type="text" id="v-narration" placeholder="e.g. Sold 200 pipes to Ramesh Traders"></div>
      </div>

      <div id="ledger-section">
        <h2 style="font-size:14px; margin-top:18px;">Ledger entries <span class="muted" style="font-weight:400;">(debit = credit)</span></h2>
        <div id="entries"></div>
        <button class="btn secondary" id="add-entry" type="button">+ Add entry</button>
        <div class="totals" id="totals"></div>
      </div>

      <div id="inventory-section">
        <h2 style="font-size:14px; margin-top:22px;">Inventory movement</h2>
        <div id="bom-autofill-box" style="display:none; background:rgba(168,121,47,0.08); padding:14px; border-radius:4px; margin-bottom:14px; border:1px solid var(--paper-line);">
          <h3 style="font-size:13px; margin:0 0 10px; font-family:'IBM Plex Mono',monospace;">🧪 Auto-Fill Raw Materials &amp; Finished Product from BOM Formula</h3>
          <div class="grid2">
            <div><label>Finished Product</label><select id="bom-prod-item-sel"></select></div>
            <div><label>Lots / Batch Qty Produced</label><input type="number" step="1" min="1" id="bom-prod-lots" value="1"></div>
          </div>
          <button type="button" class="btn secondary" id="btn-apply-bom-prod" style="margin-top:8px;">⚡ Auto-Populate Rows</button>
        </div>
        <div id="itemlines"></div>
        <button class="btn secondary" id="add-item" type="button">+ Add item line</button>
      </div>

      <div style="margin-top:20px;">
        <button class="btn" id="save-voucher" type="button">Save voucher</button>
      </div>
    </div>
  `);
  card.appendChild(form);
  wrap.appendChild(card);

  const entriesDiv = form.querySelector('#entries');
  const itemsDiv = form.querySelector('#itemlines');
  const vTypeSelect = form.querySelector('#v-type');
  const ledgerSection = form.querySelector('#ledger-section');
  const inventorySection = form.querySelector('#inventory-section');

  function accountOptions(){
    return STATE.accounts.map(a=>`<option value="${a.id}">${a.name} (${a.type})</option>`).join('');
  }
  function itemOptions(){
    return STATE.items.map(i=>`<option value="${i.id}">${i.name}</option>`).join('');
  }

  function autoSide(voucherType, account){
    if(!account) return 'debit';
    const name = account.name.toLowerCase();
    const type = account.type;
    if(voucherType === 'payment'){
      if(name === 'cash' || name === 'bank') return 'credit';
      return 'debit';
    }
    if(voucherType === 'receipt'){
      if(name === 'cash' || name === 'bank') return 'debit';
      return 'credit';
    }
    return (type === 'asset' || type === 'expense') ? 'debit' : 'credit';
  }

  function accountForRow(row){
    const id = Number(row.querySelector('.e-account').value);
    return STATE.accounts.find(a => a.id === id);
  }

  function addEntryRow(amount = '', side = null, accountId = null){
    const row = el(`
      <div class="entry-row">
        <select class="e-account">${accountOptions()}</select>
        <input type="number" step="0.01" class="e-amount" placeholder="Amount" value="${amount}">
        <button type="button" class="side-toggle"></button>
        <button class="btn ghost" type="button">✕</button>
      </div>
    `);

    const accountSel = row.querySelector('.e-account');
    const amountInput = row.querySelector('.e-amount');
    const sideBtn     = row.querySelector('.side-toggle');

    if(accountId) accountSel.value = String(accountId);

    function computeSide(){
      const acc = accountForRow(row);
      return acc ? autoSide(vTypeSelect.value, acc) : 'debit';
    }

    function applySide(s){
      sideBtn.textContent = s === 'debit' ? 'Dr' : 'Cr';
      sideBtn.className = 'side-toggle ' + (s === 'debit' ? 'dr' : 'cr');
      sideBtn.dataset.side = s;
    }

    applySide(side !== null ? side : computeSide());
    amountInput.dataset.auto = 'true';

    sideBtn.addEventListener('click', () => {
      const next = sideBtn.dataset.side === 'debit' ? 'credit' : 'debit';
      applySide(next);
      sideBtn.dataset.manual = 'true';
      updateTotals();
    });

    accountSel.addEventListener('change', () => {
      if(sideBtn.dataset.manual !== 'true') applySide(computeSide());
      updateTotals();
    });

    amountInput.addEventListener('input', () => { amountInput.dataset.auto = 'false'; updateTotals(); });
    row.querySelectorAll('button')[1].addEventListener('click', () => { row.remove(); updateTotals(); });

    entriesDiv.appendChild(row);
    updateTotals();
  }

  function addItemRow(){
    const opts = itemOptions();
    const row = el(`<div class="item-row">
        <select class="i-item">${opts}</select>
        <select class="i-dir">
          <option value="in">Stock in</option>
          <option value="out">Stock out</option>
        </select>
        <input type="number" step="0.01" class="i-qty" placeholder="Qty">
        <input type="number" step="0.01" class="i-rate" placeholder="Rate">
        <div class="gst-wrap">
          <button type="button" class="gst-toggle" title="Enable GST for this line">GST</button>
          <input type="number" step="0.01" min="0" max="100" class="i-gst gst-pct" placeholder="%" style="display:none;">
        </div>
        <button class="btn ghost" type="button">✕</button>
      </div>
    `);
    const gstBtn = row.querySelector('.gst-toggle');
    const gstIn  = row.querySelector('.i-gst');
    gstBtn.addEventListener('click', () => {
      gstBtn.classList.toggle('active');
      gstIn.style.display = gstBtn.classList.contains('active') ? '' : 'none';
      if(!gstBtn.classList.contains('active')) gstIn.value = '';
      calculateFromInventory();
    });
    row.querySelector('button.btn.ghost').addEventListener('click', ()=>{ row.remove(); calculateFromInventory(); });
    row.querySelector('.i-qty').addEventListener('input', calculateFromInventory);
    row.querySelector('.i-rate').addEventListener('input', calculateFromInventory);
    row.querySelector('.i-gst').addEventListener('input', calculateFromInventory);
    row.querySelector('.i-dir').addEventListener('change', calculateFromInventory);
    itemsDiv.appendChild(row);
  }

  function itemTotal(){
    let total = 0;
    itemsDiv.querySelectorAll('.item-row').forEach(r => {
      const qty     = Number(r.querySelector('.i-qty').value  || 0);
      const rate    = Number(r.querySelector('.i-rate').value || 0);
      const gstBtn  = r.querySelector('.gst-toggle');
      const gstRate = (gstBtn && gstBtn.classList.contains('active'))
                      ? Number(r.querySelector('.i-gst').value || 0) : 0;
      total += qty * rate * (1 + gstRate / 100);
    });
    return total;
  }

  function calculateFromInventory(){
    const type = vTypeSelect.value;
    if (type !== 'sales' && type !== 'purchase') return;
    const total = itemTotal();
    const rows = entriesDiv.querySelectorAll('.entry-row');
    if(rows.length < 2) return;
    const firstAmt  = rows[0].querySelector('.e-amount');
    const secondAmt = rows[1].querySelector('.e-amount');
    if(firstAmt.dataset.auto  === 'true') firstAmt.value  = total > 0 ? total.toFixed(2) : '';
    if(secondAmt.dataset.auto === 'true') secondAmt.value = total > 0 ? total.toFixed(2) : '';
    updateTotals();
  }

  function applyDefaultAccountsForType(type){
    entriesDiv.innerHTML = '';
    if(type === 'sales') {
      const cashAcc  = STATE.accounts.find(a => a.name === 'Cash');
      const salesAcc = STATE.accounts.find(a => a.name === 'Sales');
      addEntryRow('', null, cashAcc  ? cashAcc.id  : null);
      addEntryRow('', null, salesAcc ? salesAcc.id : null);
    } else if(type === 'purchase') {
      const pAcc    = STATE.accounts.find(a => a.name === 'Purchases');
      const cashAcc = STATE.accounts.find(a => a.name === 'Cash');
      addEntryRow('', null, pAcc    ? pAcc.id    : null);
      addEntryRow('', null, cashAcc ? cashAcc.id : null);
    } else {
      addEntryRow(); addEntryRow();
    }
    calculateFromInventory();
  }

  function updateTotals(){
    if (vTypeSelect.value === 'production') return;
    let d=0, c=0;
    entriesDiv.querySelectorAll('.entry-row').forEach(r=>{
      const amt  = Number(r.querySelector('.e-amount').value || 0);
      const side = r.querySelector('.side-toggle').dataset.side;
      if(side === 'debit')  d += amt;
      else                  c += amt;
    });
    const t = form.querySelector('#totals');
    const balanced = Math.abs(d-c) < 0.005;
    t.className = 'totals ' + (balanced ? 'balanced' : 'unbalanced');
    t.textContent = `Debit ${fmt(d)}  ·  Credit ${fmt(c)}  ·  ${balanced ? 'Balanced ✓' : 'Difference ' + fmt(d-c)}`;
  }

  function addItemRowWithVals(itemId = null, dir = 'in', qty = '', rate = 0){
    addItemRow();
    const rows = itemsDiv.querySelectorAll('.item-row');
    const lastRow = rows[rows.length - 1];
    if(!lastRow) return;
    if(itemId) lastRow.querySelector('.i-item').value = String(itemId);
    lastRow.querySelector('.i-dir').value = dir;
    if(qty) lastRow.querySelector('.i-qty').value = String(qty);
    if(rate) lastRow.querySelector('.i-rate').value = String(rate);
  }

  function updateSectionVisibility(type){
    const showLedger = type !== 'production';
    const showInventory = type === 'sales' || type === 'purchase' || type === 'production';
    ledgerSection.style.display = showLedger ? 'block' : 'none';
    inventorySection.style.display = showInventory ? 'block' : 'none';
    
    const bomBox = form.querySelector('#bom-autofill-box');
    if(bomBox){
      bomBox.style.display = type === 'production' ? 'block' : 'none';
      if(type === 'production'){
        const sel = bomBox.querySelector('#bom-prod-item-sel');
        sel.innerHTML = STATE.items.map(i=>`<option value="${i.id}">${i.name}</option>`).join('');
      }
    }
    if(!showInventory) itemsDiv.innerHTML = '';
  }

  const applyBomBtn = form.querySelector('#btn-apply-bom-prod');
  if(applyBomBtn){
    applyBomBtn.addEventListener('click', ()=>{
      const finishedId = Number(form.querySelector('#bom-prod-item-sel').value);
      const lots = Number(form.querySelector('#bom-prod-lots').value || 1);
      const matchingFormulas = (STATE.formulas || []).filter(f => f.finished_item_id === finishedId);

      if(!matchingFormulas || matchingFormulas.length === 0){
        alert("No BOM formula defined for this product. Set formula under Items -> Set Formula first.");
        return;
      }

      itemsDiv.innerHTML = '';
      // Row 0: Finished Product IN
      addItemRowWithVals(finishedId, 'in', lots, 0);

      // Rows 1..N: Raw Materials OUT
      matchingFormulas.forEach(f => {
        const rawQty = Number(f.qty_required || 0) * lots;
        addItemRowWithVals(f.raw_item_id, 'out', rawQty.toFixed(2), 0);
      });
    });
  }

  vTypeSelect.addEventListener('change', () => {
    updateSectionVisibility(vTypeSelect.value);
    if(vTypeSelect.value !== 'production') applyDefaultAccountsForType(vTypeSelect.value);
    const showsInventory = ['sales','purchase','production'].includes(vTypeSelect.value);
    if(showsInventory && itemsDiv.children.length === 0 && STATE.items.length) addItemRow();
  });

  form.querySelector('#add-entry').addEventListener('click', () => addEntryRow());
  form.querySelector('#add-item').addEventListener('click', addItemRow);
  
  updateSectionVisibility(vTypeSelect.value);
  applyDefaultAccountsForType(vTypeSelect.value);
  if(STATE.items.length && (vTypeSelect.value === 'sales' || vTypeSelect.value === 'purchase' || vTypeSelect.value === 'production')) addItemRow();

  form.querySelector('#save-voucher').addEventListener('click', async ()=>{
    const msg = form.querySelector('#voucher-msg');
    msg.innerHTML = '';
    
    const isProd = vTypeSelect.value === 'production';
    const entries = isProd ? [] : [...entriesDiv.querySelectorAll('.entry-row')].map(r => {
      const amt  = Number(r.querySelector('.e-amount').value || 0);
      const side = r.querySelector('.side-toggle').dataset.side;
      return {
        account_id: Number(r.querySelector('.e-account').value),
        debit:  side === 'debit'  ? amt : 0,
        credit: side === 'credit' ? amt : 0,
      };
    }).filter(e => e.debit > 0 || e.credit > 0);

    const items = [...itemsDiv.querySelectorAll('.item-row')].map(r=>{
      const gstBtn  = r.querySelector('.gst-toggle');
      const gstRate = (gstBtn && gstBtn.classList.contains('active'))
                      ? Number(r.querySelector('.i-gst').value || 0) : 0;
      return {
        item_id:   Number(r.querySelector('.i-item').value),
        direction: r.querySelector('.i-dir').value,
        qty:       Number(r.querySelector('.i-qty').value  || 0),
        rate:      Number(r.querySelector('.i-rate').value || 0),
        gst_rate:  gstRate,
      };
    }).filter(i=>i.qty>0);

    if (!isProd) {
      const badEntry = entries.find(e => e.debit > 0 && e.credit > 0);
      if (badEntry) {
        msg.appendChild(el(`<div class="msg error">Each ledger entry must be either a debit or a credit — not both.</div>`));
        return;
      }
    }

    const payload = {
      date: form.querySelector('#v-date').value,
      type: form.querySelector('#v-type').value,
      reference: form.querySelector('#v-reference').value,
      narration: form.querySelector('#v-narration').value,
      entries, items
    };
    try{
      await api('/vouchers', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      msg.appendChild(el(`<div class="msg ok">Voucher saved successfully.</div>`));
      const currentType = vTypeSelect.value;
      itemsDiv.innerHTML='';
      vTypeSelect.value = currentType;
      updateSectionVisibility(currentType);
      applyDefaultAccountsForType(currentType);
      if(STATE.items.length && ['sales','purchase','production'].includes(currentType)) addItemRow();
      form.querySelector('#v-narration').value='';
      form.querySelector('#v-reference').value='';
      loadAll();
    }catch(err){
      msg.appendChild(el(`<div class="msg error">${err.message}</div>`));
    }
  });

  return wrap;
}

// ---------- Shared modal helpers ----------
function showModal(htmlContent) {
  const overlay = el(`<div id="modal-overlay" style="
    position:fixed;inset:0;background:rgba(27,42,65,0.45);z-index:1000;
    display:flex;align-items:center;justify-content:center;padding:24px;">
    <div style="background:var(--card);border:1px solid var(--paper-line);border-radius:4px;
      padding:28px 32px;max-width:520px;width:100%;max-height:90vh;overflow-y:auto;
      box-shadow:0 8px 40px rgba(27,42,65,0.18);">
      ${htmlContent}
    </div>
  </div>`);
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if(e.target === overlay) closeModal(); });
  return overlay;
}

function closeModal() {
  const m = document.getElementById('modal-overlay');
  if(m) m.remove();
}

// ---------- Vouchers list ----------
function itemLinesHtml(items, verboseDirection){
  if(!items || items.length===0) return '';
  return items.map(i=>{
    if(verboseDirection){
      const line = `${i.item_name} (${i.direction === 'in' ? 'Produced' : 'Consumed'}): ${fmt(i.qty)} ${i.item_unit||''}`.trim();
      return `<div>${line}</div>`;
    }
    const gst  = i.gst_rate > 0 ? ` + ${i.gst_rate}%` : '';
    const line = `${i.item_name} — ${fmt(i.qty)}${i.rate?' @ '+fmt(i.rate):''}${gst}`;
    return `<div>${line}</div>`;
  }).join('');
}

function renderVouchers(){
  const wrap = el(`<div></div>`);

  const filterBar = el(`<div class="card" style="margin-bottom:16px;">
    <h2 style="margin-bottom:12px;">Filter vouchers</h2>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
      <div style="flex:1;min-width:120px;"><label>From</label><input type="date" id="vf-from"></div>
      <div style="flex:1;min-width:120px;"><label>To</label><input type="date" id="vf-to"></div>
      <div style="flex:1;min-width:130px;"><label>Type</label>
        <select id="vf-type">
          <option value="">All types</option>
          <option>sales</option><option>purchase</option><option>payment</option>
          <option>receipt</option><option>journal</option><option>production</option>
        </select>
      </div>
      <div style="flex:2;min-width:160px;"><label>Narration / Reference</label>
        <input type="text" id="vf-q" placeholder="Search…">
      </div>
      <button class="btn secondary" id="vf-clear" type="button" style="height:38px;margin-bottom:12px;">Clear</button>
    </div>
  </div>`);
  wrap.appendChild(filterBar);

  const listCard = el(`<div class="card"><h2>All vouchers</h2></div>`);
  wrap.appendChild(listCard);

  function applyFilters(){
    const from = filterBar.querySelector('#vf-from').value;
    const to   = filterBar.querySelector('#vf-to').value;
    const type = filterBar.querySelector('#vf-type').value;
    const q    = filterBar.querySelector('#vf-q').value.toLowerCase();
    return STATE.vouchers.filter(v => {
      if(from && v.date < from) return false;
      if(to   && v.date > to)   return false;
      if(type && v.type !== type) return false;
      if(q && !((v.narration||'').toLowerCase().includes(q) || (v.reference||'').toLowerCase().includes(q))) return false;
      return true;
    });
  }

  function rebuildTable(){
    const existing = listCard.querySelector('table, .empty');
    if(existing) existing.remove();
    const filtered = applyFilters();
    if(filtered.length === 0){
      listCard.appendChild(el(`<div class="empty">No vouchers match the filter.</div>`));
      return;
    }
    const tbl = el(`<table>
      <thead><tr><th>Date</th><th>Type</th><th>Narration</th><th class="num">Amount</th><th class="action-col"></th></tr></thead>
      <tbody></tbody></table>`);
    const tbody = tbl.querySelector('tbody');
    filtered.forEach(v=>{
      const amt = v.entries.reduce((s,e)=>s+Number(e.debit||0),0);
      const entryDetail = v.entries.map(e=>`${e.account_name}: ${e.debit>0?'Dr '+fmt(e.debit):'Cr '+fmt(e.credit)}`).join(' · ');
      const itemsHtml = itemLinesHtml(v.items, v.type === 'production');
      const refHtml = v.reference ? `<strong>${v.reference}</strong> — ` : '';
      const row = el(`<tr>
        <td>${v.date}</td>
        <td><span class="pill ${v.type}">${v.type}</span></td>
        <td>${refHtml}${v.narration||(v.type==='production'?'—':'')}
          ${v.type !== 'production' ? `<div class="muted" style="margin-top:2px;">${entryDetail}</div>` : ''}
          <div class="muted" style="margin-top:2px;">${itemsHtml}</div>
        </td>
        <td class="num">${v.type === 'production' ? '—' : fmt(amt)}</td>
        <td class="action-col" style="display:flex;gap:4px;">
          <button class="btn secondary" style="padding:4px 10px;font-size:12px;" data-action="edit" data-id="${v.id}">Edit</button>
          <button class="btn ghost" data-action="delete" data-id="${v.id}">Delete</button>
        </td>
      </tr>`);
      tbody.appendChild(row);
    });
    tbody.addEventListener('click', async e=>{
      const btn = e.target.closest('button[data-action]');
      if(!btn) return;
      const id = Number(btn.dataset.id);
      const v  = STATE.vouchers.find(x => x.id === id);
      if(btn.dataset.action === 'delete'){
        if(!confirm(`Delete this ${v.type} voucher? This cannot be undone.`)) return;
        try{ await api('/vouchers/'+id, {method:'DELETE'}); await loadAll(); rebuildTable(); }
        catch(err){ alert(err.message); }
      } else if(btn.dataset.action === 'edit'){
        openEditVoucherModal(v, rebuildTable);
      }
    });
    listCard.appendChild(tbl);
  }

  ['#vf-from','#vf-to','#vf-type','#vf-q'].forEach(sel => {
    filterBar.querySelector(sel).addEventListener('input', rebuildTable);
  });
  filterBar.querySelector('#vf-clear').addEventListener('click', ()=>{
    filterBar.querySelector('#vf-from').value = '';
    filterBar.querySelector('#vf-to').value = '';
    filterBar.querySelector('#vf-type').value = '';
    filterBar.querySelector('#vf-q').value = '';
    rebuildTable();
  });

  rebuildTable();
  return wrap;
}

// ---------- Edit-voucher modal ----------
function openEditVoucherModal(v, onSaved){
  const accountOptions = STATE.accounts.map(a=>`<option value="${a.id}">${a.name} (${a.type})</option>`).join('');
  const itemOptions    = STATE.items.map(i=>`<option value="${i.id}">${i.name}</option>`).join('');

  const overlay = showModal(`
    <h2 style="font-family:'Source Serif 4',serif;font-size:18px;margin:0 0 16px;">Edit Voucher #${v.id}</h2>
    <div id="ev-msg"></div>
    <div class="grid2">
      <div><label>Date</label><input type="date" id="ev-date" value="${v.date}"></div>
      <div><label>Type</label>
        <select id="ev-type">${['sales','purchase','payment','receipt','journal','production'].map(t=>`<option${t===v.type?' selected':''}>${t}</option>`).join('')}</select>
      </div>
    </div>
    <div class="grid2">
      <div><label>Reference</label><input type="text" id="ev-ref" value="${v.reference||''}"></div>
      <div><label>Narration</label><input type="text" id="ev-nar" value="${v.narration||''}"></div>
    </div>
    <h3 style="font-size:13px;margin:12px 0 8px;">Ledger entries</h3>
    <div id="ev-entries"></div>
    <button class="btn secondary" id="ev-add-entry" type="button" style="margin-bottom:14px;">+ Add entry</button>
    <div class="totals" id="ev-totals"></div>
    <h3 style="font-size:13px;margin:14px 0 8px;">Inventory lines</h3>
    <div id="ev-items"></div>
    <button class="btn secondary" id="ev-add-item" type="button" style="margin-bottom:16px;">+ Add item line</button>
    <div style="display:flex;gap:10px;">
      <button class="btn" id="ev-save" type="button">Save changes</button>
      <button class="btn secondary" id="ev-cancel" type="button">Cancel</button>
    </div>
  `);

  const modal = overlay.querySelector('div');
  const entriesDiv = modal.querySelector('#ev-entries');
  const itemsDiv   = modal.querySelector('#ev-items');
  const typeSelect = modal.querySelector('#ev-type');

  function naturalSide(t){ return (t==='asset'||t==='expense')?'debit':'credit'; }
  function autoSideForEdit(vType, accType){
    if(vType==='payment' && accType==='asset') return 'credit';
    if(vType==='receipt' && (accType==='liability'||accType==='income'||accType==='equity')) return 'debit';
    return naturalSide(accType);
  }

  function addEditEntryRow(amount='', side=null, accountId=null){
    const row = el(`<div class="entry-row">
      <select class="ev-account">${accountOptions}</select>
      <input type="number" step="0.01" class="ev-amount" placeholder="Amount" value="${amount}">
      <button type="button" class="side-toggle"></button>
      <button class="btn ghost" type="button">✕</button>
    </div>`);
    const sel = row.querySelector('.ev-account');
    const amt = row.querySelector('.ev-amount');
    const sb  = row.querySelector('.side-toggle');
    if(accountId) sel.value = String(accountId);
    function computeSide(){ const a=STATE.accounts.find(x=>x.id===Number(sel.value)); return a?autoSideForEdit(typeSelect.value,a.type):'debit'; }
    function applySide(s){ sb.textContent=s==='debit'?'Dr':'Cr'; sb.className='side-toggle '+(s==='debit'?'dr':'cr'); sb.dataset.side=s; }
    applySide(side!==null?side:computeSide());
    sb.addEventListener('click',()=>{ applySide(sb.dataset.side==='debit'?'credit':'debit'); sb.dataset.manual='true'; updateEditTotals(); });
    sel.addEventListener('change',()=>{ if(sb.dataset.manual!=='true') applySide(computeSide()); updateEditTotals(); });
    amt.addEventListener('input', updateEditTotals);
    row.querySelectorAll('button')[1].addEventListener('click',()=>{ row.remove(); updateEditTotals(); });
    entriesDiv.appendChild(row);
    updateEditTotals();
  }

  function addEditItemRow(itemId=null, dir='in', qty='', rate='', gstRate=0){
    const row = el(`<div class="item-row">
      <select class="ev-item">${itemOptions}</select>
      <select class="ev-dir"><option value="in">Stock in</option><option value="out">Stock out</option></select>
      <input type="number" step="0.01" class="ev-qty" placeholder="Qty" value="${qty}">
      <input type="number" step="0.01" class="ev-rate" placeholder="Rate" value="${rate}">
      <div class="gst-wrap">
        <button type="button" class="gst-toggle${gstRate>0?' active':''}">GST</button>
        <input type="number" step="0.01" min="0" max="100" class="ev-gst gst-pct" placeholder="%" value="${gstRate||''}" style="display:${gstRate>0?'':'none'};">
      </div>
      <button class="btn ghost" type="button">✕</button>
    </div>`);
    if(itemId) row.querySelector('.ev-item').value = String(itemId);
    row.querySelector('.ev-dir').value = dir;
    const gb = row.querySelector('.gst-toggle');
    const gi = row.querySelector('.ev-gst');
    gb.addEventListener('click', ()=>{
      gb.classList.toggle('active');
      gi.style.display = gb.classList.contains('active') ? '' : 'none';
      if(!gb.classList.contains('active')) gi.value = '';
    });
    row.querySelector('button.btn.ghost').addEventListener('click',()=>row.remove());
    itemsDiv.appendChild(row);
  }

  function updateEditTotals(){
    if(typeSelect.value==='production') return;
    let d=0,c=0;
    entriesDiv.querySelectorAll('.entry-row').forEach(r=>{
      const a=Number(r.querySelector('.ev-amount').value||0);
      const s=r.querySelector('.side-toggle').dataset.side;
      if(s==='debit') d+=a; else c+=a;
    });
    const t=modal.querySelector('#ev-totals');
    const balanced=Math.abs(d-c)<0.005;
    t.className='totals '+(balanced?'balanced':'unbalanced');
    t.textContent=`Debit ${fmt(d)}  ·  Credit ${fmt(c)}  ·  ${balanced?'Balanced ✓':'Difference '+fmt(d-c)}`;
  }

  v.entries.forEach(e=>{
    const side = e.debit>0 ? 'debit' : 'credit';
    const amt  = e.debit>0 ? e.debit : e.credit;
    addEditEntryRow(amt, side, e.account_id);
  });
  v.items.forEach(it=>addEditItemRow(it.item_id, it.direction, it.qty, it.rate, it.gst_rate||0));

  modal.querySelector('#ev-add-entry').addEventListener('click',()=>addEditEntryRow());
  modal.querySelector('#ev-add-item').addEventListener('click',()=>addEditItemRow());
  modal.querySelector('#ev-cancel').addEventListener('click', closeModal);

  modal.querySelector('#ev-save').addEventListener('click', async ()=>{
    const msgEl = modal.querySelector('#ev-msg'); msgEl.innerHTML='';
    const isProd = typeSelect.value==='production';
    const entries = isProd ? [] : [...entriesDiv.querySelectorAll('.entry-row')].map(r=>{
      const a=Number(r.querySelector('.ev-amount').value||0);
      const s=r.querySelector('.side-toggle').dataset.side;
      return { account_id:Number(r.querySelector('.ev-account').value), debit:s==='debit'?a:0, credit:s==='credit'?a:0 };
    }).filter(e=>e.debit>0||e.credit>0);
    const items = [...itemsDiv.querySelectorAll('.item-row')].map(r=>{
      const gb = r.querySelector('.gst-toggle');
      const gstRate = (gb && gb.classList.contains('active'))
                      ? Number(r.querySelector('.ev-gst').value || 0) : 0;
      return {
        item_id:   Number(r.querySelector('.ev-item').value),
        direction: r.querySelector('.ev-dir').value,
        qty:       Number(r.querySelector('.ev-qty').value  || 0),
        rate:      Number(r.querySelector('.ev-rate').value || 0),
        gst_rate:  gstRate,
      };
    }).filter(i=>i.qty>0);
    const payload = {
      date:modal.querySelector('#ev-date').value,
      type:typeSelect.value,
      reference:modal.querySelector('#ev-ref').value,
      narration:modal.querySelector('#ev-nar').value,
      entries, items
    };
    try{
      await api('/vouchers/'+v.id, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      await loadAll();
      closeModal();
      if(onSaved) onSaved();
    }catch(err){ msgEl.appendChild(el(`<div class="msg error">${err.message}</div>`)); }
  });
}

// ---------- Accounts ----------
function renderAccounts(){
  const wrap = el(`<div></div>`);
  const form = el(`<div class="card"><h2>Add account</h2>
    <div id="acc-msg"></div>
    <div class="grid2">
      <div><label>Name</label><input id="a-name" placeholder="e.g. Ramesh Traders"></div>
      <div><label>Type</label>
        <select id="a-type">
          <option value="asset">Asset</option>
          <option value="liability">Liability</option>
          <option value="income">Income</option>
          <option value="expense">Expense</option>
          <option value="equity">Equity</option>
        </select>
      </div>
      <div><label>Opening balance</label><input id="a-bal" type="number" step="0.01" value="0"></div>
      <div><label>Opening side</label>
        <select id="a-side"><option value="debit">Debit</option><option value="credit">Credit</option></select>
      </div>
    </div>
    <button class="btn" id="a-save" type="button">Add account</button>
  </div>`);
  form.querySelector('#a-save').addEventListener('click', async ()=>{
    const msg = form.querySelector('#acc-msg'); msg.innerHTML='';
    try{
      await api('/accounts', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
        name: form.querySelector('#a-name').value,
        type: form.querySelector('#a-type').value,
        opening_balance: form.querySelector('#a-bal').value,
        opening_side: form.querySelector('#a-side').value,
      })});
      await loadAll(); render();
    }catch(err){ msg.appendChild(el(`<div class="msg error">${err.message}</div>`)); }
  });
  wrap.appendChild(form);

  const list = el(`<div class="card"><h2>Chart of accounts</h2></div>`);
  const tbl = el(`<table><thead><tr><th>Name</th><th>Type</th><th class="num">Opening balance</th><th class="action-col"></th></tr></thead><tbody></tbody></table>`);
  const tbody = tbl.querySelector('tbody');
  STATE.accounts.forEach(a=>{
    const row = el(`<tr>
      <td>${a.name}</td><td>${a.type}</td>
      <td class="num">${fmt(a.opening_balance)} ${a.opening_side==='debit'?'Dr':'Cr'}</td>
      <td class="action-col" style="display:flex;gap:4px;">
        <button class="btn secondary" style="padding:4px 10px;font-size:12px;" data-action="edit" data-id="${a.id}">Edit</button>
        <button class="btn ghost" data-action="delete" data-id="${a.id}">Delete</button>
      </td>
    </tr>`);
    tbody.appendChild(row);
  });
  tbody.addEventListener('click', async e=>{
    const btn = e.target.closest('button[data-action]');
    if(!btn) return;
    const id = Number(btn.dataset.id);
    if(btn.dataset.action === 'delete'){
      const a = STATE.accounts.find(x=>x.id===id);
      if(!confirm(`Delete account "${a.name}"? This cannot be undone.`)) return;
      try{ await api('/accounts/'+id, {method:'DELETE'}); await loadAll(); render(); }
      catch(err){ alert(err.message); }
    } else if(btn.dataset.action === 'edit'){
      const a = STATE.accounts.find(x=>x.id===id);
      const overlay = showModal(`
        <h2 style="font-family:'Source Serif 4',serif;font-size:18px;margin:0 0 16px;">Edit Account</h2>
        <div id="ea-msg"></div>
        <div class="grid2">
          <div><label>Name</label><input id="ea-name" value="${a.name}"></div>
          <div><label>Type</label>
            <select id="ea-type">${['asset','liability','income','expense','equity'].map(t=>`<option${t===a.type?' selected':''}>${t}</option>`).join('')}</select>
          </div>
          <div><label>Opening balance</label><input id="ea-bal" type="number" step="0.01" value="${a.opening_balance}"></div>
          <div><label>Opening side</label>
            <select id="ea-side">
              <option value="debit"${a.opening_side==='debit'?' selected':''}>Debit</option>
              <option value="credit"${a.opening_side==='credit'?' selected':''}>Credit</option>
            </select>
          </div>
        </div>
        <div style="display:flex;gap:10px;margin-top:4px;">
          <button class="btn" id="ea-save" type="button">Save</button>
          <button class="btn secondary" id="ea-cancel" type="button">Cancel</button>
        </div>
      `);
      const modal = overlay.querySelector('div');
      modal.querySelector('#ea-cancel').addEventListener('click', closeModal);
      modal.querySelector('#ea-save').addEventListener('click', async ()=>{
        const msgEl = modal.querySelector('#ea-msg'); msgEl.innerHTML='';
        try{
          await api('/accounts/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
            name: modal.querySelector('#ea-name').value,
            type: modal.querySelector('#ea-type').value,
            opening_balance: modal.querySelector('#ea-bal').value,
            opening_side: modal.querySelector('#ea-side').value,
          })});
          await loadAll(); closeModal(); render();
        }catch(err){ msgEl.appendChild(el(`<div class="msg error">${err.message}</div>`)); }
      });
    }
  });
  list.appendChild(tbl);
  wrap.appendChild(list);
  return wrap;
}

// ---------- Items ----------
function renderItems(){
  const wrap = el(`<div></div>`);
  const form = el(`<div class="card"><h2>Add item</h2>
    <div id="item-msg"></div>
    <div class="grid2">
      <div><label>Name</label><input id="i-name" placeholder="e.g. PVC Resin K67"></div>
      <div><label>Unit</label><input id="i-unit" value="kg"></div>
      <div><label>Opening quantity</label><input id="i-qty" type="number" step="0.01" value="0"></div>
    </div>
    <button class="btn" id="i-save" type="button">Add item</button>
  </div>`);
  form.querySelector('#i-save').addEventListener('click', async ()=>{
    const msg = form.querySelector('#item-msg'); msg.innerHTML='';
    try{
      await api('/items', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
        name: form.querySelector('#i-name').value,
        unit: form.querySelector('#i-unit').value,
        opening_qty: form.querySelector('#i-qty').value,
        opening_rate: 0,
      })});
      await loadAll(); render();
    }catch(err){ msg.appendChild(el(`<div class="msg error">${err.message}</div>`)); }
  });
  wrap.appendChild(form);

  const list = el(`<div class="card"><h2>Items</h2></div>`);
  const tbl = el(`<table><thead><tr><th>Name</th><th>Unit</th><th class="num">Opening qty</th><th class="num">Opening rate</th><th class="action-col"></th></tr></thead><tbody></tbody></table>`);
  const tbody = tbl.querySelector('tbody');
  STATE.items.forEach(i=>{
    const row = el(`<tr>
      <td>${i.name}</td><td>${i.unit}</td>
      <td class="num">${fmt(i.opening_qty)}</td><td class="num">${fmt(i.opening_rate)}</td>
      <td class="action-col" style="display:flex;gap:4px;">
        <button class="btn secondary" style="padding:4px 10px;font-size:12px;" data-action="edit" data-id="${i.id}">Edit</button>
        <button class="btn ghost" data-action="delete" data-id="${i.id}">Delete</button>
      </td>
    </tr>`);
    tbody.appendChild(row);
  });
  tbody.addEventListener('click', async e=>{
    const btn = e.target.closest('button[data-action]');
    if(!btn) return;
    const id = Number(btn.dataset.id);
    if(btn.dataset.action === 'delete'){
      const it = STATE.items.find(x=>x.id===id);
      if(!confirm(`Delete item "${it.name}"? This cannot be undone.`)) return;
      try{ await api('/items/'+id, {method:'DELETE'}); await loadAll(); render(); }
      catch(err){ alert(err.message); }
    } else if(btn.dataset.action === 'edit'){
      const it = STATE.items.find(x=>x.id===id);
      const overlay = showModal(`
        <h2 style="font-family:'Source Serif 4',serif;font-size:18px;margin:0 0 16px;">Edit Item</h2>
        <div id="ei-msg"></div>
        <div class="grid2">
          <div><label>Name</label><input id="ei-name" value="${it.name}"></div>
          <div><label>Unit</label><input id="ei-unit" value="${it.unit}"></div>
          <div><label>Opening qty</label><input id="ei-qty" type="number" step="0.01" value="${it.opening_qty}"></div>
          <div><label>Opening rate</label><input id="ei-rate" type="number" step="0.01" value="${it.opening_rate}"></div>
        </div>
        <div style="display:flex;gap:10px;margin-top:4px;">
          <button class="btn" id="ei-save" type="button">Save</button>
          <button class="btn secondary" id="ei-cancel" type="button">Cancel</button>
        </div>
      `);
      const modal = overlay.querySelector('div');
      modal.querySelector('#ei-cancel').addEventListener('click', closeModal);
      modal.querySelector('#ei-save').addEventListener('click', async ()=>{
        const msgEl = modal.querySelector('#ei-msg'); msgEl.innerHTML='';
        try{
          await api('/items/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
            name: modal.querySelector('#ei-name').value,
            unit: modal.querySelector('#ei-unit').value,
            opening_qty: modal.querySelector('#ei-qty').value,
            opening_rate: modal.querySelector('#ei-rate').value,
          })});
          await loadAll(); closeModal(); render();
        }catch(err){ msgEl.appendChild(el(`<div class="msg error">${err.message}</div>`)); }
      });
    }
  });
  list.appendChild(tbl);
  wrap.appendChild(list);

  // ---------- BOM Formula Manager Card ----------
  if(STATE.items.length > 0){
    const bomCard = el(`<div class="card">
      <h2>🧪 Set / Edit Bill of Materials (BOM) Formula</h2>
      <div class="muted" style="margin-bottom:12px;">Define raw material ratios needed to produce 1 Lot / Unit of a finished product. Updating a formula does not alter past vouchers.</div>
      <div id="bom-msg"></div>
      <label>Select Finished Product</label>
      <select id="bom-finished-sel" style="margin-bottom:16px;">
        ${STATE.items.map(i=>`<option value="${i.id}">${i.name}</option>`).join('')}
      </select>
      <div id="bom-rows-container"></div>
      <button class="btn" id="bom-save-btn" type="button" style="margin-top:12px;">💾 Save Formula (BOM)</button>
    </div>`);

    const finishedSel = bomCard.querySelector('#bom-finished-sel');
    const rowsContainer = bomCard.querySelector('#bom-rows-container');
    const saveBtn = bomCard.querySelector('#bom-save-btn');
    const msgDiv = bomCard.querySelector('#bom-msg');

    function renderBomRows(){
      rowsContainer.innerHTML = '';
      const finishedId = Number(finishedSel.value);
      const existing = (STATE.formulas || []).filter(f => f.finished_item_id === finishedId);
      
      const itemOpts = ['<option value="0">-- None --</option>', ...STATE.items.filter(i=>i.id!==finishedId).map(i=>`<option value="${i.id}">${i.name} (${i.unit})</option>`)].join('');

      for(let idx=0; idx<7; idx++){
        const ex = existing[idx];
        const defId = ex ? ex.raw_item_id : 0;
        const defQty = ex ? ex.qty_required : '';

        const rowEl = el(`<div class="grid2" style="margin-bottom:8px; align-items:center;">
          <div>
            <label style="font-size:11px;">Raw Material #${idx+1}</label>
            <select class="bom-raw-sel">${itemOpts}</select>
          </div>
          <div>
            <label style="font-size:11px;">Qty per 1 Lot</label>
            <input type="number" step="0.01" min="0" class="bom-raw-qty" placeholder="Qty" value="${defQty}">
          </div>
        </div>`);

        if(defId) rowEl.querySelector('.bom-raw-sel').value = String(defId);
        rowsContainer.appendChild(rowEl);
      }
    }

    finishedSel.addEventListener('change', renderBomRows);
    renderBomRows();

    saveBtn.addEventListener('click', async ()=>{
      msgDiv.innerHTML = '';
      const finishedId = Number(finishedSel.value);
      const rawRows = rowsContainer.querySelectorAll('.grid2');
      const raw_materials = [];

      rawRows.forEach(r => {
        const raw_item_id = Number(r.querySelector('.bom-raw-sel').value);
        const qty_required = Number(r.querySelector('.bom-raw-qty').value || 0);
        if(raw_item_id > 0 && qty_required > 0){
          raw_materials.push({ raw_item_id, qty_required });
        }
      });

      try {
        await api(`/items/${finishedId}/formula`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ raw_materials })
        });
        msgDiv.innerHTML = `<div class="msg ok">Formula saved with ${raw_materials.length} raw material(s).</div>`;
        await loadAll();
      } catch(err) {
        msgDiv.innerHTML = `<div class="msg error">${err.message}</div>`;
      }
    });

    wrap.appendChild(bomCard);
  }

  return wrap;
}

// ---------- Reports ----------
function renderReports(){
  const wrap = el(`<div></div>`);
  const nav = el(`<div class="card" id="reports-nav">
    <h2>Reports</h2>
    <div class="grid2" style="margin-bottom:14px;">
      <div><label>From <span class="muted" style="font-weight:400;">(optional)</span></label><input type="date" id="report-from"></div>
      <div><label>To <span class="muted" style="font-weight:400;">(optional)</span></label><input type="date" id="report-to"></div>
    </div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      <button class="btn secondary" data-r="ledger">Ledger Head</button>
      <button class="btn secondary" data-r="pl">Profit &amp; Loss</button>
      <button class="btn secondary" data-r="bs">Balance Sheet</button>
      <button class="btn secondary" data-r="stock">Stock Summary</button>
      <button class="btn secondary" data-r="item-ledger">Item Ledger</button>
      <button class="btn ghost" id="report-clear-range" type="button" style="margin-left:auto;">Clear range</button>
    </div>
  </div>`);
  const out = el(`<div id="report-out"></div>`);
  wrap.appendChild(nav); wrap.appendChild(out);

  const fromInput = nav.querySelector('#report-from');
  const toInput   = nav.querySelector('#report-to');
  let currentReport = 'pl';
  let selectedLedgerAccount = null;
  let selectedLedgerItem = null;

  function dateParams(){
    const p = new URLSearchParams();
    if(fromInput.value) p.set('from', fromInput.value);
    if(toInput.value)   p.set('to',   toInput.value);
    const qs = p.toString();
    return qs ? '?' + qs : '';
  }

  function rangeLabel(){
    if(fromInput.value && toInput.value) return `Period: ${fromInput.value} to ${toInput.value}`;
    if(fromInput.value) return `From ${fromInput.value} onward`;
    if(toInput.value)   return `Up to ${toInput.value}`;
    return 'All time';
  }

  function reloadCurrent(){
    if(currentReport === 'ledger') showLedgerPicker();
    else if(currentReport === 'pl') showPL();
    else if(currentReport === 'bs') showBS();
    else if(currentReport === 'stock') showStock();
    else if(currentReport === 'item-ledger') showItemLedgerPicker();
  }

  fromInput.addEventListener('change', reloadCurrent);
  toInput.addEventListener('change', reloadCurrent);
  nav.querySelector('#report-clear-range').addEventListener('click', ()=>{
    fromInput.value = ''; toInput.value = ''; reloadCurrent();
  });

  nav.addEventListener('click', async e=>{
    if(e.target.dataset.r==='ledger')     { currentReport='ledger';      await showLedgerPicker(); }
    if(e.target.dataset.r==='pl')         { currentReport='pl';          await showPL(); }
    if(e.target.dataset.r==='bs')         { currentReport='bs';          await showBS(); }
    if(e.target.dataset.r==='stock')      { currentReport='stock';       await showStock(); }
    if(e.target.dataset.r==='item-ledger'){ currentReport='item-ledger'; await showItemLedgerPicker(); }
  });

  function balLabel(bal){
    const abs = Math.abs(bal);
    const side = bal >= 0 ? 'Dr' : 'Cr';
    return `${fmt(abs)} <span class="muted" style="font-size:11px;">${side}</span>`;
  }

  async function showLedgerPicker(){
    out.innerHTML='';
    const accOptions = STATE.accounts.map(a=>`<option value="${a.id}">${a.name}</option>`).join('');
    const card = el(`<div class="card">
      <h2>Ledger Statement <button class="btn secondary" style="float:right; padding:4px 10px; font-size:12px;" onclick="window.print()">Print / Save PDF</button></h2>
      <div class="muted" style="margin-bottom:10px;">${rangeLabel()}</div>
      <label>Choose account</label>
      <select id="ledger-acc">${accOptions}</select>
      <div id="ledger-acc-print" class="print-only"></div>
      <div id="ledger-body"></div>
    </div>`);
    out.appendChild(card);
    const sel = card.querySelector('#ledger-acc');
    if(selectedLedgerAccount && STATE.accounts.some(a=>String(a.id)===String(selectedLedgerAccount))){
      sel.value = selectedLedgerAccount;
    }

    function voucherFor(voucherId){ return STATE.vouchers.find(v => v.id === voucherId); }

    async function load(){
      selectedLedgerAccount = sel.value;
      const data = await api('/reports/ledger/'+sel.value+dateParams());
      const qtySuffix = data.total_qty ? ' (Total Qty: ' + data.total_qty + ')' : '';
      card.querySelector('#ledger-acc-print').textContent = 'Account: ' + data.account.name + qtySuffix + ' — ' + rangeLabel();
      const body = card.querySelector('#ledger-body');
      body.innerHTML='';
      const tbl = el(`<table><thead><tr><th>Date</th><th>Type</th><th>Narration</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">Balance</th></tr></thead><tbody></tbody></table>`);
      const tbody = tbl.querySelector('tbody');
      const obRow = el(`<tr><td colspan="5" class="muted">Opening balance</td><td class="num"></td></tr>`);
      obRow.querySelector('td:last-child').innerHTML = balLabel(data.opening_balance);
      tbody.appendChild(obRow);
      data.entries.forEach(r=>{
        const v = voucherFor(r.voucher_id);
        const itemsHtml = v ? itemLinesHtml(v.items, false) : '';
        const refHtml = r.reference ? `<strong>${r.reference}</strong>` : '';
        const narrationText = r.narration || (itemsHtml ? '' : '—');
        const narrationLine = [refHtml, narrationText].filter(Boolean).join(' — ');
        const row = el(`<tr>
          <td>${r.date}</td><td><span class="pill ${r.type}">${r.type}</span></td>
          <td>${narrationLine}<div class="muted" style="margin-top:2px;">${itemsHtml}</div></td>
          <td class="num">${r.debit?fmt(r.debit):''}</td><td class="num">${r.credit?fmt(r.credit):''}</td>
          <td class="num"></td></tr>`);
        row.querySelector('td:last-child').innerHTML = balLabel(r.balance);
        tbody.appendChild(row);
      });
      const cbRow = el(`<tr><td colspan="5" style="font-weight:600;">Closing balance ${qtySuffix}</td><td class="num" style="font-weight:600;"></td></tr>`);
      cbRow.querySelector('td:last-child').innerHTML = balLabel(data.closing_balance);
      tbody.appendChild(cbRow);
      body.appendChild(tbl);
    }
    sel.addEventListener('change', load);
    if(STATE.accounts.length) load();
  }

  async function showPL(){
    out.innerHTML='';
    const data = await api('/reports/pl'+dateParams());
    const card = el(`<div class="card">
      <h2>Profit &amp; Loss Statement <button class="btn secondary" style="float:right; padding:4px 10px; font-size:12px;" onclick="window.print()">Print / Save PDF</button></h2>
      <div class="muted" style="margin-bottom:10px;">${rangeLabel()}${fromInput.value ? ' — opening balances excluded from a dated period view' : ''}</div>
    </div>`);
    const grid = el(`<div class="grid2"></div>`);
    const incCard = el(`<div><h2 style="font-size:14px; border:none;">Income</h2></div>`);
    const incTbl = el(`<table><tbody></tbody></table>`);
    data.income.forEach(i=>incTbl.querySelector('tbody').appendChild(el(`<tr><td>${i.name}</td><td class="num">${fmt(i.amount)}</td></tr>`)));
    incTbl.querySelector('tbody').appendChild(el(`<tr><td style="font-weight:600;">Total income</td><td class="num" style="font-weight:600;">${fmt(data.total_income)}</td></tr>`));
    incCard.appendChild(incTbl);
    const expCard = el(`<div><h2 style="font-size:14px; border:none;">Expenses</h2></div>`);
    const expTbl = el(`<table><tbody></tbody></table>`);
    data.expense.forEach(i=>expTbl.querySelector('tbody').appendChild(el(`<tr><td>${i.name}</td><td class="num">${fmt(i.amount)}</td></tr>`)));
    expTbl.querySelector('tbody').appendChild(el(`<tr><td style="font-weight:600;">Total expense</td><td class="num" style="font-weight:600;">${fmt(data.total_expense)}</td></tr>`));
    expCard.appendChild(expTbl);
    grid.appendChild(incCard); grid.appendChild(expCard);
    card.appendChild(grid);
    const net = el(`<div class="totals ${data.net_profit>=0?'balanced':'unbalanced'}" style="margin-top:14px; font-size:16px;">Net ${data.net_profit>=0?'profit':'loss'}: ${fmt(Math.abs(data.net_profit))}</div>`);
    card.appendChild(net);
    out.appendChild(card);
  }

  async function showBS(){
    out.innerHTML='';
    const qs = toInput.value ? `?to=${toInput.value}` : '';
    const data = await api('/reports/bs' + qs);
    const card = el(`<div class="card">
      <h2>Balance Sheet <button class="btn secondary" style="float:right; padding:4px 10px; font-size:12px;" onclick="window.print()">Print / Save PDF</button></h2>
      <div class="muted" style="margin-bottom:14px;">${toInput.value ? 'As of ' + toInput.value : 'All time'}</div>
    </div>`);

    function makeSection(title, rows, total, totalLabel){
      const sec = el(`<div><h3 style="font-size:13px;font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.6px;color:var(--ink-soft);margin:0 0 8px;">${title}</h3></div>`);
      const tbl = el(`<table><tbody></tbody></table>`);
      rows.forEach(r=>{
        const row = el(`<tr><td>${r.name}</td><td class="num"></td></tr>`);
        row.querySelector('td:last-child').innerHTML = balLabel(r.balance);
        tbl.querySelector('tbody').appendChild(row);
      });
      const totRow = el(`<tr style="border-top:1px solid var(--ink);"><td style="font-weight:600;">${totalLabel}</td><td class="num" style="font-weight:600;"></td></tr>`);
      totRow.querySelector('td:last-child').innerHTML = balLabel(total);
      tbl.querySelector('tbody').appendChild(totRow);
      sec.appendChild(tbl);
      return sec;
    }

    const grid = el(`<div class="grid2"></div>`);
    const left  = el(`<div></div>`);
    const right = el(`<div></div>`);

    left.appendChild(makeSection('Assets', data.assets, data.total_assets, 'Total assets'));
    right.appendChild(makeSection('Liabilities', data.liabilities, data.total_liabilities, 'Total liabilities'));
    const eqRows = [...data.equity, { name: 'Net profit / (loss)', balance: data.net_profit }];
    right.appendChild(el(`<div style="margin-top:16px;"></div>`));
    right.querySelector('div:last-child').appendChild(
      makeSection('Equity', eqRows, data.total_equity, 'Total equity')
    );

    grid.appendChild(left); grid.appendChild(right);
    card.appendChild(grid);

    const diff = Math.abs(data.total_assets - data.total_liabilities - data.total_equity);
    const balanced = diff < 0.02;
    const check = el(`<div class="totals ${balanced?'balanced':'unbalanced'}" style="margin-top:16px;font-size:14px;">
      ${balanced ? 'Balance sheet balances ✓' : `Out of balance by ${fmt(diff)} — check opening balances`}
    </div>`);
    card.appendChild(check);
    out.appendChild(card);
  }

  async function showStock(){
    out.innerHTML='';
    const data = await api('/reports/stock'+dateParams());
    const card = el(`<div class="card">
      <h2>Stock Inventory Summary <button class="btn secondary" style="float:right; padding:4px 10px; font-size:12px;" onclick="window.print()">Print / Save PDF</button></h2>
      <div class="muted" style="margin-bottom:10px;">${rangeLabel()}</div>
    </div>`);
    if(data.length===0){ card.appendChild(el(`<div class="empty">No items yet.</div>`)); out.appendChild(card); return; }
    const tbl = el(`<table><thead><tr><th>Item</th><th class="num">Opening</th><th class="num">In</th><th class="num">Out</th><th class="num">Closing</th><th class="num">Avg rate</th><th class="num">Value</th></tr></thead><tbody></tbody></table>`);
    const tbody = tbl.querySelector('tbody');
    let totalValue = 0;
    data.forEach(i=>{
      totalValue += i.closing_value;
      tbody.appendChild(el(`<tr>
        <td>${i.name} <span class="muted">(${i.unit})</span></td>
        <td class="num">${fmt(i.opening_qty)}</td>
        <td class="num">${fmt(i.in_qty)}</td>
        <td class="num">${fmt(i.out_qty)}</td>
        <td class="num">${fmt(i.closing_qty)}</td>
        <td class="num">${fmt(i.avg_rate)}</td>
        <td class="num">${fmt(i.closing_value)}</td>
      </tr>`));
    });
    tbody.appendChild(el(`<tr><td colspan="6" style="font-weight:600;">Total stock value</td><td class="num" style="font-weight:600;">${fmt(totalValue)}</td></tr>`));
    card.appendChild(tbl);
    out.appendChild(card);
  }

  async function showItemLedgerPicker(){
    out.innerHTML='';
    const itemOptions = STATE.items.map(i=>`<option value="${i.id}">${i.name} (${i.unit})</option>`).join('');
    const card = el(`<div class="card">
      <h2>Item Ledger Statement <button class="btn secondary" style="float:right; padding:4px 10px; font-size:12px;" onclick="window.print()">Print / Save PDF</button></h2>
      <div class="muted" style="margin-bottom:10px;">${rangeLabel()}</div>
      <label>Choose item</label>
      <select id="ledger-item">${itemOptions}</select>
      <div id="item-ledger-print" class="print-only"></div>
      <div id="item-ledger-body"></div>
    </div>`);
    out.appendChild(card);
    const sel = card.querySelector('#ledger-item');
    if(selectedLedgerItem && STATE.items.some(i=>String(i.id)===String(selectedLedgerItem))){
      sel.value = selectedLedgerItem;
    }
    async function load(){
      selectedLedgerItem = sel.value;
      const data = await api('/reports/item-ledger/'+sel.value+dateParams());
      card.querySelector('#item-ledger-print').textContent = 'Item: ' + data.item.name + ' (' + data.item.unit + ') — ' + rangeLabel();
      const body = card.querySelector('#item-ledger-body');
      body.innerHTML='';
      const tbl = el(`<table><thead><tr><th>Date</th><th>Type</th><th>Ref / Narration</th><th class="num">In Qty</th><th class="num">Out Qty</th><th class="num">Stock Balance</th></tr></thead><tbody></tbody></table>`);
      const tbody = tbl.querySelector('tbody');
      tbody.appendChild(el(`<tr><td></td><td></td><td class="muted">Opening Balance</td><td></td><td></td><td class="num font-bold">${fmt(data.opening_qty)} ${data.item.unit}</td></tr>`));
      data.rows.forEach(r=>{
        const refStr = r.reference ? `Ref: ${r.reference}` : '';
        const narrStr = [refStr, r.narration].filter(Boolean).join(' — ');
        const inStr = r.qty_in > 0 ? fmt(r.qty_in) : '';
        const outStr = r.qty_out > 0 ? fmt(r.qty_out) : '';
        tbody.appendChild(el(`<tr>
          <td>${r.date}</td><td><span class="badge">${r.type}</span></td><td>${narrStr || '—'}</td>
          <td class="num" style="color:var(--green);">${inStr}</td><td class="num" style="color:var(--red);">${outStr}</td>
          <td class="num font-bold">${fmt(r.stock_balance)} ${data.item.unit}</td>
        </tr>`));
      });
      tbody.appendChild(el(`<tr style="font-weight:bold; border-top:2px solid var(--border);"><td></td><td></td><td>Closing Stock Balance</td><td></td><td></td><td class="num">${fmt(data.closing_qty)} ${data.item.unit}</td></tr>`));
      body.appendChild(tbl);
    }
    sel.addEventListener('change', load);
    if(STATE.items.length) load();
  }

  showPL();
  return wrap;
}

(async function init(){
  try {
    const config = await api('/config');
    document.getElementById('app-title').textContent = config.company_name;
    document.title = config.company_name;
    
    if (config.password_protected && !sessionStorage.getItem('authenticated')) {
      const lockOverlay = el(`
        <div id="app-lock" style="
          position: fixed; inset: 0; background: var(--paper); z-index: 10000;
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          background-image: repeating-linear-gradient(180deg, transparent, transparent 27px, var(--paper-line) 28px);
        ">
          <div class="card" style="max-width: 400px; width: 100%; text-align: center; padding: 30px;">
            <h2 style="font-family: 'Source Serif 4', serif; margin-bottom: 20px;">🔒 ${config.company_name}</h2>
            <div id="lock-msg" style="margin-bottom: 12px;"></div>
            <label style="text-align: left;">Enter Password</label>
            <input type="password" id="lock-pwd" style="margin-bottom: 16px;">
            <button class="btn" id="lock-submit" style="width: 100%;">Unlock System</button>
          </div>
        </div>
      `);
      document.body.appendChild(lockOverlay);
      
      const pwdIn = lockOverlay.querySelector('#lock-pwd');
      const msgDiv = lockOverlay.querySelector('#lock-msg');
      const submitBtn = lockOverlay.querySelector('#lock-submit');
      
      async function attemptUnlock() {
        try {
          await api('/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({password: pwdIn.value})
          });
          sessionStorage.setItem('authenticated', 'true');
          lockOverlay.remove();
          await loadAll();
          render();
        } catch (err) {
          msgDiv.innerHTML = `<div class="msg error">Incorrect password.</div>`;
        }
      }
      
      submitBtn.addEventListener('click', attemptUnlock);
      pwdIn.addEventListener('keydown', e => { if(e.key === 'Enter') attemptUnlock(); });
      return;
    }
  } catch (err) {
    console.error("Config load failed", err);
  }
  
  await loadAll();
  render();
})();

import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { cachedGet, request, session } from './api';
import { enqueue, getCache, listOutbox, removeEvent, setCache } from './db';
import './style.css';

const uid = () => crypto.randomUUID();
const money = value => `KES ${Number(value || 0).toLocaleString('en-KE', { minimumFractionDigits: 2 })}`;

function App() {
  const [signedIn, setSignedIn] = useState(Boolean(session.access));
  const [view, setView] = useState('Dashboard');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [online, setOnline] = useState(navigator.onLine);
  const [workspace, setWorkspace] = useState(null);
  const [medicines, setMedicines] = useState([]);
  const [batches, setBatches] = useState([]);
  const [alerts, setAlerts] = useState(null);
  const [outbox, setOutbox] = useState([]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [search, setSearch] = useState('');
  const [cart, setCart] = useState([]);
  const [salePayment, setSalePayment] = useState('cash');
  const [salePaid, setSalePaid] = useState('');
  const [deviceName, setDeviceName] = useState(localStorage.getItem('solona.device_name') || `${navigator.platform || 'Browser'} terminal`);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    addEventListener('online', update); addEventListener('offline', update);
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    return () => { removeEventListener('online', update); removeEventListener('offline', update); };
  }, []);

  useEffect(() => { refreshOutbox(); if (signedIn) loadWorkspace(); }, [signedIn]);

  async function refreshOutbox() { setOutbox(await listOutbox()); }
  async function loadWorkspace() {
    try {
      let data; let offline = false;
      try { data = await request('/auth/workspaces'); await setCache('workspaces', data); }
      catch (error) { data = await getCache('workspaces'); offline = true; if (!data) throw error; }
      const current = data.workspaces?.[0];
      setWorkspace(current ? { ...current, user_id: data.user_id } : null);
      if (current?.shops?.[0]) await loadShop({ ...current, user_id: data.user_id }, current.shops[0], offline);
    } catch (e) { setNotice(e.message); }
  }
  async function loadShop(ws, shop, offline = false) {
    const ids = { tenant: ws.tenant_id, shop: shop.shop_id, user: ws.user_id };
    setWorkspace({ ...ws, selectedShop: shop, user_id: ids.user });
    const results = await Promise.allSettled([
      cachedGet(`medicines:${ids.tenant}`, `/tenants/${ids.tenant}/medicines`),
      cachedGet(`batches:${ids.tenant}:${ids.shop}`, `/tenants/${ids.tenant}/shops/${ids.shop}/batches`),
      cachedGet(`alerts:${ids.tenant}:${ids.shop}`, `/tenants/${ids.tenant}/shops/${ids.shop}/inventory/alerts`),
    ]);
    if (results[0].status === 'fulfilled') setMedicines(results[0].value.data);
    if (results[1].status === 'fulfilled') setBatches(results[1].value.data);
    if (results[2].status === 'fulfilled') setAlerts(results[2].value.data);
    if (!offline && navigator.onLine) syncQueue();
  }
  async function signIn(event) {
    event.preventDefault(); setBusy(true); setNotice('');
    try {
      const result = await request('/auth/login', { method: 'POST', auth: false, body: { email, password } });
      session.access = result.access_token; session.refresh = result.refresh_token; setSignedIn(true); setPassword('');
    } catch (e) { setNotice(e.message); }
    finally { setBusy(false); }
  }
  async function syncQueue() {
    if (!navigator.onLine || !workspace?.selectedShop) return;
    const deviceId = localStorage.getItem('solona.device_id');
    const secret = localStorage.getItem('solona.device_secret');
    if (!deviceId || !secret) { setNotice('Offline sales are queued on this device. Ask an administrator to enroll this browser before syncing.'); return; }
    const pending = await listOutbox();
    for (const event of pending) {
      try {
        const response = await request(`/tenants/${event.tenant_id}/shops/${event.shop_id}/sync/events`, {
          method: 'POST', auth: false,
          headers: { 'X-Device-ID': deviceId, 'X-Device-Secret': secret },
          body: { events: [{ ...event, device_id: deviceId }] },
        });
        const ack = response.events?.[0];
        if (ack?.status === 'applied') await removeEvent(event.event_id);
        else { event.sync_status = ack?.status || 'needs_review'; event.sync_code = ack?.code; await enqueue(event); }
      } catch (e) { setNotice(`Sync paused: ${e.message}`); break; }
    }
    await refreshOutbox();
  }
  async function enrollDevice(event) {
    event.preventDefault();
    if (!workspace?.selectedShop) return;
    try {
      const result = await request(`/tenants/${workspace.tenant_id}/devices`, { method: 'POST', body: {
        shop_id: workspace.selectedShop.shop_id, name: deviceName, platform: `Web · ${navigator.platform || 'Browser'}`,
      } });
      localStorage.setItem('solona.device_id', result.id);
      localStorage.setItem('solona.device_secret', result.device_credential);
      localStorage.setItem('solona.device_name', deviceName);
      setNotice('This browser is enrolled. Keep this browser profile available; its device credential is stored locally.');
      syncQueue();
    } catch (e) { setNotice(e.message); }
  }
  function addToCart(batch) {
    setCart(current => {
      const line = current.find(item => item.batch.id === batch.id);
      if (line) return current.map(item => item.batch.id === batch.id ? { ...item, quantity: Math.min(Number(batch.quantity), item.quantity + 1) } : item);
      return [...current, { batch, quantity: 1 }];
    });
  }
  const cartTotal = useMemo(() => cart.reduce((total, item) => total + Number(item.batch.selling_price) * item.quantity, 0), [cart]);
  async function completeSale(event) {
    event.preventDefault(); if (!workspace?.selectedShop || !cart.length) return;
    if (!navigator.onLine && !localStorage.getItem('solona.device_id')) {
      setNotice('Enroll this browser as a shop device while online before recording offline sales.'); setView('Settings'); return;
    }
    const transactionId = uid(); const eventId = uid(); const invoice = `WEB-${Date.now()}`;
    const amountPaid = salePaid === '' ? cartTotal : Number(salePaid);
    if (amountPaid < 0 || amountPaid > cartTotal || (amountPaid < cartTotal && salePayment !== 'credit')) {
      setNotice('Enter a paid amount from zero to the total. Use Credit for a partial payment.'); return;
    }
    const payload = {
      cashier_cloud_id: workspace.user_id, invoice_no: invoice, customer_cloud_id: null,
      payment_method: salePayment, amount_paid: amountPaid.toFixed(2),
      total_amount: cartTotal.toFixed(2), discount: '0.00', tax: '0.00',
      credit_amount: (cartTotal - amountPaid).toFixed(2), payment_id: amountPaid > 0 ? uid() : null,
      lines: cart.map(item => ({ cloud_batch_id: item.batch.id, cloud_medicine_id: item.batch.medicine_id,
        cloud_sale_item_id: uid(), quantity: item.quantity, unit_price: Number(item.batch.selling_price).toFixed(2),
        unit_cost: Number(item.batch.purchase_cost).toFixed(2), subtotal: (Number(item.batch.selling_price) * item.quantity).toFixed(2) })),
    };
    const row = { event_id: eventId, transaction_id: transactionId, tenant_id: workspace.tenant_id,
      shop_id: workspace.selectedShop.shop_id, device_id: localStorage.getItem('solona.device_id') || uid(),
      event_type: 'sale.created', aggregate_id: transactionId, schema_version: 1, occurred_at: new Date().toISOString(),
      payload, sync_status: 'pending' };
    if (!localStorage.getItem('solona.device_id')) localStorage.setItem('solona.device_id', row.device_id);
    const nextBatches = batches.map(batch => {
      const sold = cart.find(item => item.batch.id === batch.id)?.quantity || 0;
      return sold ? { ...batch, quantity: Math.max(0, Number(batch.quantity) - sold) } : batch;
    });
    await enqueue(row); setBatches(nextBatches); await setCache(`batches:${workspace.tenant_id}:${workspace.selectedShop.shop_id}`, nextBatches);
    setCart([]); setSalePaid(''); await refreshOutbox(); setNotice(`Sale ${invoice} saved to this device and queued for sync.`);
    if (navigator.onLine) syncQueue();
  }
  function signOut() { session.access = ''; session.refresh = ''; setWorkspace(null); setSignedIn(false); }

  if (!signedIn) return <main className="login-shell"><form className="login-card" onSubmit={signIn}>
    <div className="brand-mark">S</div><span className="eyebrow">SOLONA PHARMACY</span><h1>Welcome back</h1>
    <p>Sign in to your pharmacy workspace. Cached stock remains available offline on this device.</p>
    <label>Email<input type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} /></label>
    <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
    {notice && <div className="notice error">{notice}</div>}<button className="primary full" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    <span className="muted small">Offline access requires a prior sign in and cached workspace.</span>
  </form></main>;

  const nav = ['Dashboard', 'Point of sale', 'Inventory', 'Customers', 'Suppliers', 'Reports', 'Settings'];
  const filtered = medicines.filter(m => `${m.name} ${m.sku} ${m.barcode || ''}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="app-shell"><aside className="sidebar">
    <div className="brand"><div className="brand-mark small-mark">S</div><div><b>SOLONA</b><span>PHARMACY</span></div></div>
    <div className="shop-picker"><span className="eyebrow">WORKSPACE</span><strong>{workspace?.tenant_name || 'Pharmacy'}</strong><span>{workspace?.selectedShop?.shop_name || 'No shop access'}</span></div>
    <span className="eyebrow nav-label">MENU</span><nav>{nav.map((label, i) => <button key={label} className={view === label ? 'nav-item active' : 'nav-item'} onClick={() => setView(label)}><span>{['◫','▣','▦','♙','▤','▥','⚙'][i]}</span>{label}</button>)}</nav>
    <div className="sidebar-bottom"><div className="user-chip"><div className="avatar">{(workspace?.role || 'U')[0].toUpperCase()}</div><div><b>{workspace?.role || 'User'}</b><span>{workspace?.tenant_name || ''}</span></div></div><button className="nav-item" onClick={signOut}>↪ &nbsp; Sign out</button></div>
  </aside><main className="main-panel"><header className="topbar"><div className="mobile-brand"><div className="brand-mark small-mark">S</div><b>SOLONA</b></div><div className="breadcrumb">Workspace <span>/</span> {view}</div><div className="top-actions"><div className={online ? 'connection online' : 'connection offline'}><i />{online ? 'Online' : 'Offline'}</div><button className="sync-button" onClick={syncQueue}>↻ <span>Sync</span>{outbox.length > 0 && <b>{outbox.length}</b>}</button><button className="avatar top-avatar">{(workspace?.role || 'U')[0].toUpperCase()}</button></div></header>
    <div className="content"><div className="page-heading"><div><span className="eyebrow">{new Date().toLocaleDateString('en-KE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</span><h1>{view === 'Point of sale' ? 'Point of sale' : `Good day, ${workspace?.role || 'there'}`}</h1><p>{view === 'Point of sale' ? 'Build a basket and record a sale.' : 'Here is what is happening at your pharmacy today.'}</p></div><button className="primary" onClick={() => setView('Point of sale')}>＋ New sale</button></div>
      {notice && <div className="notice" onClick={() => setNotice('')}>{notice}<button aria-label="Dismiss">×</button></div>}
      {!workspace?.selectedShop ? <section className="card"><h2>No shop is available</h2><p>Your account has no assigned shop. Ask your workspace owner to grant shop access.</p></section> : <>
      {view === 'Dashboard' && <><div className="metric-grid"><Metric icon="▣" label="Inventory items" value={medicines.length} tone="mint"/><Metric icon="▦" label="Stock units" value={batches.reduce((sum, b) => sum + Number(b.quantity), 0)} tone="peach"/><Metric icon="↗" label="Needs attention" value={(alerts?.expired_batches?.length || 0) + (alerts?.expiring_batches?.length || 0) + (alerts?.low_stock_medicines?.length || 0)} tone="lavender"/><Metric icon="↻" label="Pending sync" value={outbox.length} tone="blue"/></div>
        <div className="dashboard-grid"><section className="card"><div className="card-heading"><div><span className="eyebrow">STOCK OVERVIEW</span><h2>Inventory snapshot</h2></div><button className="text-button" onClick={() => setView('Inventory')}>View all →</button></div><InventoryTable batches={batches.slice(0, 5)}/></section>
        <section className="card attention-card"><div className="card-heading"><div><span className="eyebrow">ALERTS</span><h2>Needs attention</h2></div><span className="alert-count">{(alerts?.expired_batches?.length || 0) + (alerts?.expiring_batches?.length || 0) + (alerts?.low_stock_medicines?.length || 0)}</span></div><AlertList alerts={alerts}/></section></div>
        <section className="card"><div className="card-heading"><div><span className="eyebrow">LOCAL ACTIVITY</span><h2>Pending synchronization</h2></div><button className="text-button" onClick={syncQueue}>Sync now →</button></div><OutboxList rows={outbox}/></section></>}
      {view === 'Inventory' && <section className="card"><div className="card-heading"><div><span className="eyebrow">CATALOGUE & STOCK</span><h2>Inventory</h2></div><span className="muted">{medicines.length} medicines</span></div><div className="search-wrap"><span>⌕</span><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search medicine, SKU or barcode…"/></div><InventoryTable batches={batches.filter(b => `${b.medicine_name} ${b.sku} ${b.batch_no}`.toLowerCase().includes(search.toLowerCase()))} full/></section>}
      {view === 'Point of sale' && <div className="pos-grid"><section className="card"><div className="card-heading"><div><span className="eyebrow">PRODUCTS</span><h2>Select items</h2></div></div><div className="search-wrap"><span>⌕</span><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search medicine or scan barcode…"/></div><div className="product-grid">{filtered.map(medicine => { const available = batches.filter(b => b.medicine_id === medicine.id && Number(b.quantity) > 0); return available.map(batch => <button className="product-card" key={batch.id} onClick={() => addToCart(batch)}><span className="product-icon">{(medicine.name || 'M')[0]}</span><b>{medicine.name}</b><span>{medicine.sku} · {batch.quantity} in stock</span><strong>{money(batch.selling_price)}</strong></button>); })}</div></section>
      <form className="card cart-card" onSubmit={completeSale}><div className="card-heading"><div><span className="eyebrow">CURRENT SALE</span><h2>Basket <span className="count-pill">{cart.reduce((n, l) => n + l.quantity, 0)}</span></h2></div><button type="button" className="text-button" onClick={() => setCart([])}>Clear</button></div><div className="cart-lines">{cart.length ? cart.map(line => <div className="cart-line" key={line.batch.id}><div><b>{line.batch.medicine_name}</b><span>{money(line.batch.selling_price)} each</span></div><div className="quantity"><button type="button" onClick={() => setCart(cart.map(x => x === line ? { ...x, quantity: Math.max(1, x.quantity - 1) } : x))}>−</button><b>{line.quantity}</b><button type="button" onClick={() => addToCart(line.batch)}>＋</button></div></div>) : <div className="empty-cart"><span>▣</span><b>Your basket is empty</b><small>Add items from the product list.</small></div>}</div><div className="cart-summary"><div><span>Subtotal</span><b>{money(cartTotal)}</b></div><div className="total"><span>Total due</span><strong>{money(cartTotal)}</strong></div><label>Payment method<select value={salePayment} onChange={e => setSalePayment(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="credit">Credit</option><option value="other">Other</option></select></label><label>Amount paid<input type="number" min="0" step="0.01" placeholder={cartTotal.toFixed(2)} value={salePaid} onChange={e => setSalePaid(e.target.value)}/></label><button className="primary full" disabled={!cart.length}>Complete sale · {money(cartTotal)}</button><small className="muted center">Saved locally first. Server confirmation may happen after sync.</small></div></form></div>}
      {view === 'Settings' && <section className="card settings-card"><div className="card-heading"><div><span className="eyebrow">THIS BROWSER</span><h2>Device enrollment</h2></div><span className={`status ${localStorage.getItem('solona.device_id') ? 'good' : 'warning'}`}>{localStorage.getItem('solona.device_id') ? 'Enrolled' : 'Not enrolled'}</span></div><p>Enrollment lets this browser send offline sales to your shop when it reconnects. Your workspace owner must grant device management permission.</p><form className="enroll-form" onSubmit={enrollDevice}><label>Device name<input required value={deviceName} onChange={e => setDeviceName(e.target.value)}/></label><button className="primary">Enroll this browser</button></form><p className="muted small">The device credential is stored in this browser profile. Clearing site data removes it. Revoke lost devices from the owner’s device management tools.</p><div className="feature-divider"/><span className="eyebrow">WEB WORKFLOW AVAILABILITY</span><div className="availability"><span><b>Dashboard, inventory, alerts and POS</b><small>Available with cached data; new sales queue locally.</small></span><span><b>Customers, suppliers, reports and returns</b><small>Awaiting matching API endpoints.</small></span></div></section>}
      {['Customers', 'Suppliers', 'Reports'].includes(view) && <section className="card empty-feature"><div className="feature-icon">◌</div><span className="eyebrow">WEB WORKFLOW STATUS</span><h2>{view} need additional API endpoints</h2><p>The existing service API does not yet expose the full {view.toLowerCase()} workflow needed for this web app. Those screens are not carried over from the desktop version.</p></section>}
      </>}
      <footer>Offline first · Data on this device is stored in this browser. <a href="/api/v1/docs" target="_blank" rel="noreferrer">API documentation</a></footer>
    </div></main></div>;
}

function Metric({ icon, label, value, tone }) { return <div className="metric card"><div className={`metric-icon ${tone}`}>{icon}</div><span>{label}</span><strong>{value}</strong></div>; }
function InventoryTable({ batches, full = false }) { return <div className="table-scroll"><table><thead><tr><th>MEDICINE</th><th>BATCH</th><th>EXPIRY</th><th>QUANTITY</th><th>PRICE</th>{full && <th>STATUS</th>}</tr></thead><tbody>{batches.map(batch => <tr key={batch.id}><td><b>{batch.medicine_name}</b><small>{batch.sku}</small></td><td>{batch.batch_no}</td><td>{batch.expiry_date}</td><td><b>{batch.quantity}</b></td><td>{money(batch.selling_price)}</td>{full && <td><span className={`status ${Number(batch.quantity) <= 10 ? 'warning' : 'good'}`}>{Number(batch.quantity) <= 10 ? 'Low stock' : 'In stock'}</span></td>}</tr>)}{!batches.length && <tr><td colSpan={full ? 6 : 5} className="empty-row">No inventory records are available in this cached workspace.</td></tr>}</tbody></table></div>; }
function AlertList({ alerts }) { const rows = [...(alerts?.expired_batches || []).map(x => ({ ...x, title: 'Expired batch', tone: 'red' })), ...(alerts?.expiring_batches || []).slice(0, 4).map(x => ({ ...x, title: `Expires in ${x.days_until_expiry} days`, tone: 'amber' })), ...(alerts?.low_stock_medicines || []).slice(0, 4).map(x => ({ ...x, medicine_name: x.name, title: `${x.quantity} units remaining`, tone: 'blue' }))].slice(0, 5); return rows.length ? <div className="alert-list">{rows.map((row, i) => <div className="alert-row" key={`${row.title}-${row.sku}-${i}`}><i className={row.tone}>!</i><div><b>{row.medicine_name}</b><span>{row.title}</span></div><small>{row.sku}</small></div>)}</div> : <div className="all-clear"><span>✓</span><b>All clear</b><small>No stock alerts at the moment.</small></div>; }
function OutboxList({ rows }) { return rows.length ? <div className="outbox-list">{rows.slice(0, 5).map(row => <div className="outbox-row" key={row.event_id}><span className="outbox-icon">↗</span><div><b>{row.event_type === 'sale.created' ? `Sale ${row.payload?.payload?.invoice_no || ''}` : row.event_type}</b><span>{new Date(row.occurred_at).toLocaleString()}</span></div><span className={`status ${row.sync_status === 'conflict' ? 'warning' : 'pending'}`}>{row.sync_status || 'Pending'}</span></div>)}</div> : <div className="all-clear compact"><span>✓</span><b>Everything is synchronized</b></div>; }

createRoot(document.getElementById('root')).render(<App/>);

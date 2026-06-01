/* GridironCards — frontend app */

// ── Helpers ────────────────────────────────────────────────────

function fmt$(cents) {
  // API prices are integer cents (e.g. 1599 = $15.99).
  if (cents == null) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100);
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? '—' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function rankClass(i) {
  return i === 0 ? 'rank rank-1' : i === 1 ? 'rank rank-2' : i === 2 ? 'rank rank-3' : 'rank';
}

function cardBadges(card) {
  let b = '';
  if (card.is_rookie)    b += '<span class="badge badge-rc">RC</span>';
  if (card.is_autograph) b += '<span class="badge badge-auto">Auto</span>';
  if (card.is_patch)     b += '<span class="badge badge-patch">Patch</span>';
  return b ? `<span class="badges">${b}</span>` : '';
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function spinner()  { return '<div class="spinner"></div>'; }
function empty(msg) { return `<div class="empty-state"><div class="empty-icon">🏈</div><p>${msg}</p></div>`; }
function err(msg)   { return `<div class="error-banner">⚠️ ${msg}</div>`; }

function setHTML(id, html) { document.getElementById(id).innerHTML = html; }

// ── Hero stats ─────────────────────────────────────────────────

async function loadStats() {
  try {
    const [sales, players, cards] = await Promise.all([
      api('/sales/?limit=1').then(() => api('/analytics/top-sales?limit=1')),
      api('/players/?limit=200'),
      api('/cards/?limit=200'),
    ]);
    // Top-sales only gives us 1; fetch actual counts via a full list
    const salesFull  = await api('/sales/?limit=1');  // we just need count, use 0-item trick
    document.getElementById('stat-players').textContent = `${players.length} players`;
    document.getElementById('stat-cards').textContent   = `${cards.length} cards`;
  } catch (_) { /* non-critical */ }
}

// Simpler stats: just count what comes back (limit 200)
async function loadHeroStats() {
  try {
    const [players, cards, sales] = await Promise.allSettled([
      api('/players/?limit=500'),
      api('/cards/?limit=500'),
      api('/sales/?limit=500'),
    ]);
    const p = players.status === 'fulfilled' ? players.value.length : '—';
    const c = cards.status   === 'fulfilled' ? cards.value.length   : '—';
    const s = sales.status   === 'fulfilled' ? sales.value.length   : '—';
    document.getElementById('stat-sales').textContent   = `${s} sales tracked`;
    document.getElementById('stat-players').textContent = `${p} players`;
    document.getElementById('stat-cards').textContent   = `${c} cards`;
  } catch (_) { /* non-critical */ }
}

// ── Top Sales ──────────────────────────────────────────────────

async function loadTopSales() {
  setHTML('top-sales-body', spinner());
  const limit = document.getElementById('top-sales-limit').value;
  try {
    const sales = await api(`/analytics/top-sales?limit=${limit}`);
    if (!sales.length) { setHTML('top-sales-body', empty('No sales recorded yet. Start by adding some via the API.')); return; }

    const rows = sales.map((s, i) => `
      <tr>
        <td><span class="${rankClass(i)}">#${i + 1}</span></td>
        <td><div class="player-cell"><span class="player-name">${esc(s.player_name)}</span></div></td>
        <td>
          <div class="card-cell">
            <span class="card-brand">${esc(s.year + ' ' + s.brand)}</span>
            <span class="card-sub">${esc(s.variant ?? '—')}</span>
          </div>
        </td>
        <td><span class="price">${fmt$(s.sale_price)}</span></td>
        <td>${fmtDate(s.sale_date)}</td>
        <td class="col-platform"><span class="platform">${esc(s.platform ?? '—')}</span></td>
      </tr>`).join('');

    setHTML('top-sales-body', `
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr>
            <th>#</th><th>Player</th><th>Card</th>
            <th>Sale Price</th><th>Date Sold</th><th class="col-platform">Platform</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  } catch (e) {
    setHTML('top-sales-body', err('Could not load top sales — is the API server running?'));
  }
}

// ── Market Insights ────────────────────────────────────────────

async function loadMarket() {
  setHTML('market-body', spinner());
  const rookie = document.getElementById('filter-rookie').checked;
  const auto   = document.getElementById('filter-auto').checked;
  let qs = '?limit=20';
  if (rookie) qs += '&is_rookie=true';
  if (auto)   qs += '&is_autograph=true';

  try {
    const items = await api(`/analytics/price-summary${qs}`);
    if (!items.length) { setHTML('market-body', empty('No market data yet.')); return; }

    const rows = items.map(s => `
      <tr>
        <td><div class="player-cell"><span class="player-name">${esc(s.player_name)}</span></div></td>
        <td>
          <div class="card-cell">
            <span class="card-brand">${esc(s.year + ' ' + s.brand)}</span>
            <span class="card-sub">${esc(s.variant ?? '—')}</span>
          </div>
        </td>
        <td>
          <span class="price">${fmt$(s.avg_price)}</span>
          <span class="price-avg"> avg</span>
        </td>
        <td><span class="price-high">${fmt$(s.max_price)}</span></td>
        <td><span class="price-low">${fmt$(s.min_price)}</span></td>
        <td><span class="sale-count">${s.sale_count}</span></td>
        <td>${fmtDate(s.last_sale_date)}</td>
      </tr>`).join('');

    setHTML('market-body', `
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr>
            <th>Player</th><th>Card</th><th>Avg Price</th>
            <th>High</th><th>Low</th><th>Sales</th><th>Last Sale</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  } catch (e) {
    setHTML('market-body', err('Could not load market data.'));
  }
}

// ── Search ─────────────────────────────────────────────────────

async function handleSearch() {
  const query = document.getElementById('search-input').value.trim();
  if (!query) return;

  const section  = document.getElementById('search-results');
  const countEl  = document.getElementById('results-count');
  section.classList.remove('hidden');
  setHTML('search-results-body', spinner());
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const listings = await api(`/scrape/search?query=${encodeURIComponent(query)}&max_pages=2`);
    countEl.textContent = `${listings.length} sold listing${listings.length !== 1 ? 's' : ''}`;

    const rows = listings.map(s => `
      <tr>
        <td style="width:54px;padding:6px 8px">
          ${s.image_url ? `<img src="${esc(s.image_url)}" alt="" style="width:48px;height:48px;object-fit:cover;border-radius:6px;border:1px solid var(--border)">` : ''}
        </td>
        <td><div class="card-cell"><span class="card-brand">${esc(s.title)}</span></div></td>
        <td><span class="price">${fmt$(s.sale_price)}</span></td>
        <td class="col-cond">${esc(s.condition ?? '—')}</td>
        <td>${fmtDate(s.sale_date)}</td>
        <td class="col-platform"><a href="${esc(s.listing_url)}" target="_blank" rel="noopener" style="color:var(--gold);text-decoration:underline">View ↗</a></td>
      </tr>`).join('');

    setHTML('search-results-body', `
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr>
            <th></th><th>Listing Title</th><th>Sale Price</th>
            <th class="col-cond">Condition</th><th>Date Sold</th>
            <th class="col-platform">eBay Link</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  } catch (e) {
    if (e.message.startsWith('503'))
      setHTML('search-results-body', err('eBay is temporarily rate-limiting this search. Please try again in 15 minutes.'));
    else if (e.message.startsWith('404'))
      setHTML('search-results-body', empty(`No eBay sold listings found for "${esc(query)}". Try a different search.`));
    else
      setHTML('search-results-body', err('Search failed — the API may be waking up, please try again in 30 seconds.'));
  }
}

function clearSearch() {
  document.getElementById('search-results').classList.add('hidden');
  document.getElementById('search-input').value = '';
}

async function getApiKey() {
  const email = document.getElementById('api-email').value.trim();
  const errEl = document.getElementById('api-key-error');
  errEl.style.display = 'none';
  if (!email) { errEl.textContent = 'Please enter your email.'; errEl.style.display = 'block'; return; }
  const btn = document.querySelector('button[onclick="getApiKey()"]');
  btn.textContent = 'Getting key…';
  btn.disabled = true;
  try {
    const r = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await r.json();
    if (!r.ok || !data.api_key) throw new Error(data.error || 'No key returned');
    document.getElementById('api-key-value').textContent = data.api_key;
    document.getElementById('api-key-result').style.display = 'block';
  } catch (e) {
    errEl.textContent = 'Could not generate key — ' + e.message;
    errEl.style.display = 'block';
  } finally {
    btn.textContent = 'Get API Key';
    btn.disabled = false;
  }
}

function copyKey() {
  const key = document.getElementById('api-key-value').textContent;
  navigator.clipboard.writeText(key).then(() => {
    const btn = event.target;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
  });
}

// XSS guard
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Event listeners ────────────────────────────────────────────

document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') handleSearch();
});

// ── Init ───────────────────────────────────────────────────────

loadTopSales();
loadMarket();
loadHeroStats();

// GridironCards API — Cloudflare Worker backed by D1
// Static assets in frontend/ are served by Cloudflare Assets; this Worker handles all API routes.

const CACHE_TTL_MS    = 24 * 60 * 60 * 1000;  // 24 h
const BLOCKED_TTL_MS  = 15 * 60 * 1000;         // 15 min
const SCRAPE_GAP_MS   =  6 * 1000;              //  6 s global min between eBay fetches
const RATE_LIMIT_KEY  = '__global_rate_limit__';

// ── Helpers ────────────────────────────────────────────────────────────────

function json(data, status = 200) {
  return Response.json(data, {
    status,
    headers: { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json' },
  });
}

function err(detail, status = 400) {
  return json({ detail }, status);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Entry point ────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-API-Key',
        },
      });
    }

    try {
      return await route(request, env);
    } catch (e) {
      console.error(e);
      return err(e.message || 'Internal server error', 500);
    }
  },
};

// ── Router ─────────────────────────────────────────────────────────────────

async function route(request, env) {
  const url    = new URL(request.url);
  const method = request.method;
  const seg    = url.pathname.replace(/\/$/, '').split('/').filter(Boolean);
  const [r0, r1] = seg;

  // Known API segments — everything else falls through to static assets
  const API = new Set(['health', 'auth', 'players', 'cards', 'sales', 'analytics', 'scrape']);
  if (!r0 || !API.has(r0)) return env.ASSETS.fetch(request);

  if (r0 === 'health') return json({ status: 'ok' });

  if (r0 === 'auth') {
    if (r1 === 'register' && method === 'POST') return authRegister(request, env);
    if (r1 === 'me'       && method === 'GET')  return authMe(url, env);
  }

  if (r0 === 'players') {
    if (!r1) {
      if (method === 'GET')  return listPlayers(url, env);
      if (method === 'POST') return createPlayer(request, env);
    } else {
      const id = parseInt(r1);
      if (method === 'GET')    return getPlayer(id, env);
      if (method === 'PATCH')  return updatePlayer(id, request, env);
      if (method === 'PUT')    return updatePlayer(id, request, env);
      if (method === 'DELETE') return deletePlayer(id, env);
    }
  }

  if (r0 === 'cards') {
    if (!r1) {
      if (method === 'GET')  return listCards(url, env);
      if (method === 'POST') return createCard(request, env);
    } else {
      const id = parseInt(r1);
      if (method === 'GET')    return getCard(id, env);
      if (method === 'PATCH')  return updateCard(id, request, env);
      if (method === 'PUT')    return updateCard(id, request, env);
      if (method === 'DELETE') return deleteCard(id, env);
    }
  }

  if (r0 === 'sales') {
    if (!r1) {
      if (method === 'GET')  return listSales(url, env);
      if (method === 'POST') return createSale(request, env);
    } else {
      const id = parseInt(r1);
      if (method === 'GET')    return getSale(id, env);
      if (method === 'PATCH')  return updateSale(id, request, env);
      if (method === 'PUT')    return updateSale(id, request, env);
      if (method === 'DELETE') return deleteSale(id, env);
    }
  }

  if (r0 === 'analytics') {
    if (r1 === 'price-summary' && method === 'GET') return priceSummary(url, env);
    if (r1 === 'top-sales'     && method === 'GET') return topSales(url, env);
  }

  if (r0 === 'scrape') {
    if (r1 === 'search') {
      if (method === 'GET')  return scrapeSearchGet(url, env);
      if (method === 'POST') return scrapeSearchPost(request, env);
    }
    if (r1 === 'import'      && method === 'POST') return scrapeImport(request, env);
    if (r1 === 'debug'       && method === 'GET')  return scrapeDebug(url, env);
    if (r1 === 'clear-cache' && method === 'POST') return clearCache(env);
  }

  return env.ASSETS.fetch(request);
}

// ── Players ────────────────────────────────────────────────────────────────

async function listPlayers(url, env) {
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '50'), 1000);
  const skip  = parseInt(url.searchParams.get('skip') || '0');
  const name  = url.searchParams.get('name');
  const team  = url.searchParams.get('team');
  const pos   = url.searchParams.get('position');

  let q = 'SELECT * FROM players WHERE 1=1';
  const p = [];
  if (name) { q += ' AND name LIKE ?';     p.push(`%${name}%`); }
  if (team) { q += ' AND team LIKE ?';     p.push(`%${team}%`); }
  if (pos)  { q += ' AND position LIKE ?'; p.push(`%${pos}%`); }
  q += ' LIMIT ? OFFSET ?';
  p.push(limit, skip);

  const { results } = await env.DB.prepare(q).bind(...p).all();
  return json(results);
}

async function getPlayer(id, env) {
  const row = await env.DB.prepare('SELECT * FROM players WHERE id = ?').bind(id).first();
  if (!row) return err('Player not found', 404);
  return json(row);
}

async function createPlayer(request, env) {
  const b = await request.json();
  if (!b.name?.trim()) return err('name is required');
  const row = await env.DB.prepare(
    'INSERT INTO players (name, team, position) VALUES (?, ?, ?) RETURNING *'
  ).bind(b.name.trim(), b.team || null, b.position || null).first();
  return json(row, 201);
}

async function updatePlayer(id, request, env) {
  const existing = await env.DB.prepare('SELECT * FROM players WHERE id = ?').bind(id).first();
  if (!existing) return err('Player not found', 404);
  const b = await request.json();
  const row = await env.DB.prepare(
    'UPDATE players SET name=?, team=?, position=? WHERE id=? RETURNING *'
  ).bind(b.name ?? existing.name, b.team ?? existing.team, b.position ?? existing.position, id).first();
  return json(row);
}

async function deletePlayer(id, env) {
  const existing = await env.DB.prepare('SELECT id FROM players WHERE id = ?').bind(id).first();
  if (!existing) return err('Player not found', 404);
  await env.DB.prepare('DELETE FROM players WHERE id = ?').bind(id).run();
  return new Response(null, { status: 204 });
}

// ── Cards ──────────────────────────────────────────────────────────────────

function cardWithPlayer(r) {
  const { p_id, p_name, p_team, p_position, ...card } = r;
  return {
    ...card,
    is_rookie:    !!card.is_rookie,
    is_autograph: !!card.is_autograph,
    is_patch:     !!card.is_patch,
    player: { id: p_id, name: p_name, team: p_team, position: p_position },
  };
}

const CARD_SELECT = `
  SELECT c.*, p.id as p_id, p.name as p_name, p.team as p_team, p.position as p_position
  FROM cards c JOIN players p ON c.player_id = p.id`;

async function listCards(url, env) {
  const limit    = Math.min(parseInt(url.searchParams.get('limit') || '50'), 1000);
  const skip     = parseInt(url.searchParams.get('skip') || '0');
  const playerId = url.searchParams.get('player_id');

  let q = CARD_SELECT;
  const p = [];
  if (playerId) { q += ' WHERE c.player_id = ?'; p.push(parseInt(playerId)); }
  q += ' LIMIT ? OFFSET ?';
  p.push(limit, skip);

  const { results } = await env.DB.prepare(q).bind(...p).all();
  return json(results.map(cardWithPlayer));
}

async function getCard(id, env) {
  const row = await env.DB.prepare(`${CARD_SELECT} WHERE c.id = ?`).bind(id).first();
  if (!row) return err('Card not found', 404);
  return json(cardWithPlayer(row));
}

async function createCard(request, env) {
  const b = await request.json();
  if (!b.player_id || !b.year || !b.brand) return err('player_id, year, and brand are required');
  const player = await env.DB.prepare('SELECT * FROM players WHERE id = ?').bind(b.player_id).first();
  if (!player) return err('Player not found', 404);
  const row = await env.DB.prepare(`
    INSERT INTO cards (player_id,year,brand,set_name,card_number,variant,is_rookie,is_autograph,is_patch,print_run)
    VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING *
  `).bind(
    b.player_id, b.year, b.brand, b.set_name || null, b.card_number || null,
    b.variant || null, b.is_rookie ? 1 : 0, b.is_autograph ? 1 : 0,
    b.is_patch ? 1 : 0, b.print_run || null
  ).first();
  return json({ ...row, is_rookie: !!row.is_rookie, is_autograph: !!row.is_autograph, is_patch: !!row.is_patch, player }, 201);
}

async function updateCard(id, request, env) {
  const existing = await env.DB.prepare('SELECT * FROM cards WHERE id = ?').bind(id).first();
  if (!existing) return err('Card not found', 404);
  const b = await request.json();
  const row = await env.DB.prepare(`
    UPDATE cards SET player_id=?,year=?,brand=?,set_name=?,card_number=?,variant=?,
    is_rookie=?,is_autograph=?,is_patch=?,print_run=? WHERE id=? RETURNING *
  `).bind(
    b.player_id    ?? existing.player_id,
    b.year         ?? existing.year,
    b.brand        ?? existing.brand,
    b.set_name     ?? existing.set_name,
    b.card_number  ?? existing.card_number,
    b.variant      ?? existing.variant,
    b.is_rookie    != null ? (b.is_rookie    ? 1 : 0) : existing.is_rookie,
    b.is_autograph != null ? (b.is_autograph ? 1 : 0) : existing.is_autograph,
    b.is_patch     != null ? (b.is_patch     ? 1 : 0) : existing.is_patch,
    b.print_run    ?? existing.print_run,
    id
  ).first();
  const player = await env.DB.prepare('SELECT * FROM players WHERE id = ?').bind(row.player_id).first();
  return json(cardWithPlayer({ ...row, p_id: player.id, p_name: player.name, p_team: player.team, p_position: player.position }));
}

async function deleteCard(id, env) {
  const existing = await env.DB.prepare('SELECT id FROM cards WHERE id = ?').bind(id).first();
  if (!existing) return err('Card not found', 404);
  await env.DB.prepare('DELETE FROM cards WHERE id = ?').bind(id).run();
  return new Response(null, { status: 204 });
}

// ── Sales ──────────────────────────────────────────────────────────────────

function saleWithCard(r) {
  const { player_id, year, brand, set_name, card_number, variant,
          is_rookie, is_autograph, is_patch, print_run,
          p_id, p_name, p_team, p_position, ...sale } = r;
  return {
    ...sale,
    card: {
      id: sale.card_id, player_id, year, brand, set_name, card_number, variant,
      is_rookie: !!is_rookie, is_autograph: !!is_autograph, is_patch: !!is_patch, print_run,
      player: { id: p_id, name: p_name, team: p_team, position: p_position },
    },
  };
}

const SALE_SELECT = `
  SELECT s.*,
    c.player_id, c.year, c.brand, c.set_name, c.card_number, c.variant,
    c.is_rookie, c.is_autograph, c.is_patch, c.print_run,
    p.id as p_id, p.name as p_name, p.team as p_team, p.position as p_position
  FROM sales s
  JOIN cards c ON s.card_id = c.id
  JOIN players p ON c.player_id = p.id`;

async function listSales(url, env) {
  const limit  = Math.min(parseInt(url.searchParams.get('limit') || '50'), 1000);
  const skip   = parseInt(url.searchParams.get('skip') || '0');
  const cardId = url.searchParams.get('card_id');

  let q = SALE_SELECT;
  const p = [];
  if (cardId) { q += ' WHERE s.card_id = ?'; p.push(parseInt(cardId)); }
  q += ' ORDER BY s.sale_date DESC LIMIT ? OFFSET ?';
  p.push(limit, skip);

  const { results } = await env.DB.prepare(q).bind(...p).all();
  return json(results.map(saleWithCard));
}

async function getSale(id, env) {
  const row = await env.DB.prepare(`${SALE_SELECT} WHERE s.id = ?`).bind(id).first();
  if (!row) return err('Sale not found', 404);
  return json(saleWithCard(row));
}

async function createSale(request, env) {
  const b = await request.json();
  if (!b.card_id || !b.sale_price || !b.sale_date) return err('card_id, sale_price, and sale_date are required');
  const card = await env.DB.prepare('SELECT id FROM cards WHERE id = ?').bind(b.card_id).first();
  if (!card) return err('Card not found', 404);
  const row = await env.DB.prepare(`
    INSERT INTO sales (card_id,sale_price,sale_date,platform,condition,grade,notes,listing_url)
    VALUES (?,?,?,?,?,?,?,?) RETURNING *
  `).bind(b.card_id, b.sale_price, b.sale_date, b.platform || null, b.condition || null,
    b.grade || null, b.notes || null, b.listing_url || null).first();
  const full = await env.DB.prepare(`${SALE_SELECT} WHERE s.id = ?`).bind(row.id).first();
  return json(saleWithCard(full), 201);
}

async function updateSale(id, request, env) {
  const existing = await env.DB.prepare('SELECT * FROM sales WHERE id = ?').bind(id).first();
  if (!existing) return err('Sale not found', 404);
  const b = await request.json();
  await env.DB.prepare(`
    UPDATE sales SET card_id=?,sale_price=?,sale_date=?,platform=?,condition=?,grade=?,notes=?,listing_url=?
    WHERE id=?
  `).bind(
    b.card_id      ?? existing.card_id,
    b.sale_price   ?? existing.sale_price,
    b.sale_date    ?? existing.sale_date,
    b.platform     ?? existing.platform,
    b.condition    ?? existing.condition,
    b.grade        ?? existing.grade,
    b.notes        ?? existing.notes,
    b.listing_url  ?? existing.listing_url,
    id
  ).run();
  const full = await env.DB.prepare(`${SALE_SELECT} WHERE s.id = ?`).bind(id).first();
  return json(saleWithCard(full));
}

async function deleteSale(id, env) {
  const existing = await env.DB.prepare('SELECT id FROM sales WHERE id = ?').bind(id).first();
  if (!existing) return err('Sale not found', 404);
  await env.DB.prepare('DELETE FROM sales WHERE id = ?').bind(id).run();
  return new Response(null, { status: 204 });
}

// ── Analytics ──────────────────────────────────────────────────────────────

async function priceSummary(url, env) {
  const limit    = Math.min(parseInt(url.searchParams.get('limit') || '20'), 100);
  const playerId = url.searchParams.get('player_id');
  const isRookie = url.searchParams.get('is_rookie');
  const isAuto   = url.searchParams.get('is_autograph');

  const where = ['1=1'];
  const p = [];
  if (playerId)        { where.push('c.player_id = ?');   p.push(parseInt(playerId)); }
  if (isRookie === 'true')  where.push('c.is_rookie = 1');
  if (isAuto   === 'true')  where.push('c.is_autograph = 1');
  p.push(limit);

  const { results } = await env.DB.prepare(`
    SELECT
      c.id as card_id, p.name as player_name, c.year, c.brand, c.variant,
      COUNT(s.id)          as sale_count,
      ROUND(AVG(s.sale_price), 2) as avg_price,
      MIN(s.sale_price)    as min_price,
      MAX(s.sale_price)    as max_price,
      MAX(s.sale_date)     as last_sale_date,
      (SELECT sale_price FROM sales WHERE card_id = c.id ORDER BY sale_date DESC LIMIT 1) as last_sale_price
    FROM cards c
    JOIN players p ON c.player_id = p.id
    JOIN sales s ON s.card_id = c.id
    WHERE ${where.join(' AND ')}
    GROUP BY c.id, p.name, c.year, c.brand, c.variant
    ORDER BY avg_price DESC
    LIMIT ?
  `).bind(...p).all();

  return json(results);
}

async function topSales(url, env) {
  const limit    = Math.min(parseInt(url.searchParams.get('limit') || '10'), 100);
  const playerId = url.searchParams.get('player_id');

  const where = playerId ? 'WHERE c.player_id = ?' : '';
  const p = playerId ? [parseInt(playerId), limit] : [limit];

  const { results } = await env.DB.prepare(`
    SELECT s.id as sale_id, p.name as player_name, c.year, c.brand, c.variant,
           s.sale_price, s.sale_date, s.platform
    FROM sales s
    JOIN cards c ON s.card_id = c.id
    JOIN players p ON c.player_id = p.id
    ${where}
    ORDER BY s.sale_price DESC
    LIMIT ?
  `).bind(...p).all();

  return json(results);
}

// ── Auth ───────────────────────────────────────────────────────────────────

async function authRegister(request, env) {
  const b = await request.json();
  if (!b.email) return err('email is required');
  const email = b.email.toLowerCase().trim();

  const existing = await env.DB.prepare('SELECT * FROM api_keys WHERE email = ?').bind(email).first();
  if (existing) {
    return json({ email: existing.email, api_key: existing.key, message: 'You already have an API key — here it is again.' });
  }

  const key = 'gc_' + randomToken();
  await env.DB.prepare('INSERT INTO api_keys (email, key) VALUES (?, ?)').bind(email, key).run();
  return json({ email, api_key: key, message: 'API key created. Include it as the X-API-Key header in your requests.' }, 201);
}

async function authMe(url, env) {
  const apiKey = url.searchParams.get('api_key');
  if (!apiKey) return err('api_key query param required', 401);
  const row = await env.DB.prepare('SELECT * FROM api_keys WHERE key = ? AND is_active = 1').bind(apiKey).first();
  if (!row) return err('Invalid or inactive API key', 401);
  return json({ email: row.email, is_active: !!row.is_active, request_count: row.request_count, created_at: row.created_at });
}

function randomToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

// ── Scrape ─────────────────────────────────────────────────────────────────

function cacheKey(query) { return query.toLowerCase().trim(); }

async function getCached(env, query) {
  const row = await env.DB.prepare('SELECT * FROM search_cache WHERE query = ?').bind(cacheKey(query)).first();
  if (!row) return undefined;
  const age  = Date.now() - new Date(row.cached_at).getTime();
  const data = JSON.parse(row.results_json);
  if (!data.length) return age < BLOCKED_TTL_MS ? [] : undefined;
  return age < CACHE_TTL_MS ? data : undefined;
}

// Returns any non-empty cached result regardless of age (stale-while-revalidate fallback)
async function getStaleCache(env, query) {
  const row = await env.DB.prepare('SELECT results_json FROM search_cache WHERE query = ?').bind(cacheKey(query)).first();
  if (!row) return undefined;
  const data = JSON.parse(row.results_json);
  return data.length ? data : undefined;
}

// Returns true and reserves the slot if enough time has passed since the last scrape
async function reserveScrapeSlot(env) {
  const row = await env.DB.prepare(
    'SELECT results_json FROM search_cache WHERE query = ?'
  ).bind(RATE_LIMIT_KEY).first();
  const lastAt = row ? new Date(row.results_json).getTime() : 0;
  if (Date.now() - lastAt < SCRAPE_GAP_MS) return false;
  await env.DB.prepare(
    'INSERT OR REPLACE INTO search_cache (query, results_json, cached_at) VALUES (?, ?, ?)'
  ).bind(RATE_LIMIT_KEY, new Date().toISOString(), new Date().toISOString()).run();
  return true;
}

async function setCache(env, query, results) {
  await env.DB.prepare(
    'INSERT OR REPLACE INTO search_cache (query, results_json, cached_at) VALUES (?, ?, ?)'
  ).bind(cacheKey(query), JSON.stringify(results), new Date().toISOString()).run();
}

async function clearCache(env) {
  const { meta } = await env.DB.prepare("DELETE FROM search_cache WHERE results_json = '[]'").run();
  return json({ deleted: meta.changes });
}

async function doSearch(query, maxPages, env) {
  const cached = await getCached(env, query);
  if (cached !== undefined) {
    if (!cached.length) return err('eBay is temporarily rate-limiting this search. Please try again in 15 minutes.', 503);
    return json(cached);
  }

  // Global rate limit — all CF Worker instances share this D1 row
  const slot = await reserveScrapeSlot(env);
  if (!slot) {
    // Slot taken: return stale cache if we have any, otherwise ask them to wait
    const stale = await getStaleCache(env, query);
    if (stale) return json(stale);
    return err('Too many searches at once — please wait a few seconds and try again.', 429);
  }

  const results = await scrapeEbay(query, maxPages, env);
  if (!results.length) {
    await setCache(env, query, []);
    return err('eBay is temporarily rate-limiting this search. Please try again in 15 minutes.', 503);
  }
  await setCache(env, query, results);
  return json(results);
}

async function scrapeSearchGet(url, env) {
  const query    = url.searchParams.get('query');
  const maxPages = Math.min(parseInt(url.searchParams.get('max_pages') || '1'), 3);
  if (!query) return err('query parameter is required');
  return doSearch(query, maxPages, env);
}

async function scrapeSearchPost(request, env) {
  const b = await request.json();
  if (!b.query) return err('query is required');
  return doSearch(b.query, Math.min(b.max_pages || 1, 5), env);
}

async function scrapeImport(request, env) {
  const b = await request.json();
  if (!b.card_id || !b.query) return err('card_id and query are required');
  const card = await env.DB.prepare('SELECT id FROM cards WHERE id = ?').bind(b.card_id).first();
  if (!card) return err('Card not found', 404);

  const listings = await scrapeEbay(b.query, b.max_pages || 1, env);
  if (!listings.length) return err('No sold listings found. Try a broader search query.', 404);

  let imported = 0, skipped = 0, errors = 0;
  const ids = [];

  for (const l of listings) {
    if (!l.sale_date) { skipped++; continue; }
    try {
      const r = await env.DB.prepare(`
        INSERT OR IGNORE INTO sales (card_id,sale_price,sale_date,platform,condition,grade,grade_company,item_number,notes,listing_url)
        VALUES (?,?,?,'eBay',?,?,?,?,?,?) RETURNING id
      `).bind(
        b.card_id, l.sale_price, l.sale_date, l.condition || null,
        l.grade ?? null, l.grade_company || null, l.item_number || null,
        l.title, l.listing_url || null
      ).first();
      if (r) { ids.push(r.id); imported++; } else skipped++;
    } catch { errors++; }
  }

  const sales = ids.length
    ? (await env.DB.prepare(`${SALE_SELECT} WHERE s.id IN (${ids.join(',')})`).all()).results.map(saleWithCard)
    : [];

  return json({ imported, skipped, errors, sales });
}

async function scrapeDebug(url, env) {
  const scraperUrl = (env.SCRAPER_URL || '').trim().replace(/\/$/, '');
  const result = {
    scraper_url_configured: Boolean(scraperUrl),
    scraper_url: scraperUrl || '(not set — deploy scraper-service and set SCRAPER_URL)',
    note: 'Add ?test=true to fire a live test through the scraper service.',
  };

  if (url.searchParams.get('test') === 'true') {
    if (!scraperUrl) {
      result.error = 'SCRAPER_URL not configured';
    } else {
      try {
        const r    = await fetch(`${scraperUrl}/scrape`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ query: 'mahomes prizm', max_pages: 1 }),
        });
        const data = await r.json();
        result.scraper_http_status = r.status;
        result.parsed_count        = Array.isArray(data) ? data.length : 0;
        result.sample              = Array.isArray(data) ? data.slice(0, 2) : data;
      } catch (e) { result.error = e.message; }
    }
  }

  return json(result);
}

// ── eBay scraper (delegates to external scraper-service) ────────────────────

async function scrapeEbay(query, maxPages, env) {
  const scraperUrl = (env.SCRAPER_URL || '').trim().replace(/\/$/, '');
  if (!scraperUrl) return [];

  try {
    const r = await fetch(`${scraperUrl}/scrape`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query, max_pages: maxPages }),
    });
    if (!r.ok) return [];
    const data = await r.json();
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

// ── HTML parser (HTMLRewriter) ──────────────────────────────────────────────

async function parseEbayHtml(html) {
  // Try standard eBay list-view classes first, then card/grid-view classes
  let results = await _parseWithPrefix(html, 's-item');
  if (!results.length) results = await _parseWithPrefix(html, 's-card');
  return results;
}

async function _parseWithPrefix(html, pfx) {
  const items = [];
  let cur = null;
  let fld = null;

  const rewriter = new HTMLRewriter()
    .on(`li[class*="${pfx}"]`, {
      element(el) {
        const cls = el.getAttribute('class') || '';
        if (cls.includes('placeholder') || cls.includes('--load')) { cur = null; return; }
        cur = { _title: '', _price: '', url: null, _date: '', _cond: '', imgUrl: null };
        items.push(cur);
        fld = null;
      },
    })
    .on(`li[class*="${pfx}"] [class*="${pfx}__title"]`, {
      element(el) { if (cur) { fld = '_title'; el.onEndTag(() => { fld = null; }); } },
      text(t)     { if (cur && fld === '_title') cur._title += t.text; },
    })
    .on(`li[class*="${pfx}"] [class*="${pfx}__price"]`, {
      element(el) { if (cur) { fld = '_price'; el.onEndTag(() => { fld = null; }); } },
      text(t)     { if (cur && fld === '_price') cur._price += t.text; },
    })
    .on(`li[class*="${pfx}"] a[class*="${pfx}__link"]`, {
      element(el) { if (cur) cur.url = el.getAttribute('href')?.split('?')[0] || null; },
    })
    .on(`li[class*="${pfx}"] .POSITIVE`, {
      element(el) { if (cur && !cur._date) { fld = '_date'; el.onEndTag(() => { fld = null; }); } },
      text(t)     { if (cur && fld === '_date') cur._date += t.text; },
    })
    .on(`li[class*="${pfx}"] [class*="${pfx}__subtitle"]`, {
      element(el) { if (cur && !cur._date) { fld = '_date'; el.onEndTag(() => { fld = null; }); } },
      text(t)     { if (cur && fld === '_date') cur._date += t.text; },
    })
    .on(`li[class*="${pfx}"] [class*="SECONDARY_INFO"]`, {
      element(el) { if (cur && !cur._cond) { fld = '_cond'; el.onEndTag(() => { fld = null; }); } },
      text(t)     { if (cur && fld === '_cond') cur._cond += t.text; },
    })
    .on(`li[class*="${pfx}"] [class*="${pfx}__secondary-info"]`, {
      element(el) { if (cur && !cur._cond) { fld = '_cond'; el.onEndTag(() => { fld = null; }); } },
      text(t)     { if (cur && fld === '_cond') cur._cond += t.text; },
    })
    .on(`li[class*="${pfx}"] img`, {
      element(el) {
        if (cur && !cur.imgUrl)
          cur.imgUrl = el.getAttribute('data-defer-load') || el.getAttribute('src') || null;
      },
    });

  await rewriter.transform(new Response(html)).text();

  return items
    .filter(item => item.url && item._title.trim() && !item._title.includes('Shop on eBay'))
    .map(item => {
      const price = parsePrice(item._price.split(' to ')[0]);
      if (price === null) return null;
      return {
        title:       item._title.trim(),
        sale_price:  price,
        sale_date:   parseDate(item._date),
        condition:   item._cond.trim() || null,
        listing_url: item.url,
        image_url:   item.imgUrl,
      };
    })
    .filter(Boolean);
}

function parsePrice(text) {
  const m = text.replace(/,/g, '').match(/\d+\.?\d*/);
  return m ? parseFloat(m[0]) : null;
}

function parseDate(text) {
  if (!text) return null;
  const cleaned = text.replace(/sold\s*/i, '').trim();
  const d = new Date(cleaned);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

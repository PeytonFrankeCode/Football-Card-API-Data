// GridironCards API — Cloudflare Worker backed by D1
// Static assets in frontend/ are served by Cloudflare Assets; this Worker handles all API routes.

// Sold listings are immutable, so we cache aggressively and serve stale results
// while refreshing in the background — most user searches never touch eBay.
const CACHE_FRESH_MS  = 24 * 60 * 60 * 1000;       // serve directly, no refetch
const CACHE_STALE_MS  =  7 * 24 * 60 * 60 * 1000;  // serve stale + refresh in background
const BLOCKED_TTL_MS  = 15 * 60 * 1000;            // negative-cache a blocked query
const SCRAPE_GAP_MS   =  6 * 1000;                 // global min between eBay fetches
const RATE_LIMIT_KEY  = '__global_rate_limit__';

// Circuit breaker: when eBay starts blocking us, pause ALL live fetches for an
// escalating cooldown so we stop poking it (which is what hardens a temp block).
const BREAKER_KEY     = '__circuit_breaker__';
const BREAKER_BASE_MS = 15 * 60 * 1000;            // first cooldown
const BREAKER_MAX_MS  = 60 * 60 * 1000;            // cap
const PREWARM_LIMIT   = 8;                          // top-N popular queries per cron run

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
  async fetch(request, env, ctx) {
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
      return await route(request, env, ctx);
    } catch (e) {
      console.error(e);
      return err(e.message || 'Internal server error', 500);
    }
  },

  // Cloudflare Cron Trigger — pre-warm popular searches so user traffic hits
  // warm cache instead of hammering eBay live.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(prewarm(env));
  },
};

// ── Router ─────────────────────────────────────────────────────────────────

async function route(request, env, ctx) {
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
      if (method === 'GET')  return scrapeSearchGet(url, env, ctx);
      if (method === 'POST') return scrapeSearchPost(request, env, ctx);
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

// Reads the cache row and classifies it: { data, ageMs, negative }.
// negative === true means the query was blocked/empty last time.
async function readCache(env, query) {
  const row = await env.DB.prepare('SELECT results_json, cached_at FROM search_cache WHERE query = ?')
    .bind(cacheKey(query)).first();
  if (!row) return null;
  let data;
  try { data = JSON.parse(row.results_json); } catch { return null; }
  if (!Array.isArray(data)) return null;
  return { data, ageMs: Date.now() - new Date(row.cached_at).getTime(), negative: data.length === 0 };
}

// Returns any non-empty cached result regardless of age (stale-while-revalidate fallback)
async function getStaleCache(env, query) {
  const row = await env.DB.prepare('SELECT results_json FROM search_cache WHERE query = ?').bind(cacheKey(query)).first();
  if (!row) return undefined;
  const data = JSON.parse(row.results_json);
  return Array.isArray(data) && data.length ? data : undefined;
}

// ── Circuit breaker ──────────────────────────────────────────────────────────

async function getBreaker(env) {
  const row = await env.DB.prepare('SELECT results_json FROM search_cache WHERE query = ?').bind(BREAKER_KEY).first();
  if (!row) return { until: 0, level: 0 };
  try { return JSON.parse(row.results_json); } catch { return { until: 0, level: 0 }; }
}

async function breakerOpen(env) {
  return Date.now() < ((await getBreaker(env)).until || 0);
}

async function tripBreaker(env) {
  const b = await getBreaker(env);
  const level = Math.min((b.level || 0) + 1, 4);
  const cooldown = Math.min(BREAKER_BASE_MS * 2 ** (level - 1), BREAKER_MAX_MS);
  await env.DB.prepare('INSERT OR REPLACE INTO search_cache (query, results_json, cached_at) VALUES (?, ?, ?)')
    .bind(BREAKER_KEY, JSON.stringify({ until: Date.now() + cooldown, level }), new Date().toISOString()).run();
}

async function clearBreaker(env) {
  await env.DB.prepare('INSERT OR REPLACE INTO search_cache (query, results_json, cached_at) VALUES (?, ?, ?)')
    .bind(BREAKER_KEY, JSON.stringify({ until: 0, level: 0 }), new Date().toISOString()).run();
}

// Tracks query popularity so the cron job knows what to pre-warm.
async function bumpQueryStat(env, query) {
  try {
    await env.DB.prepare(
      `INSERT INTO query_stats (query, hits, last_at) VALUES (?, 1, ?)
       ON CONFLICT(query) DO UPDATE SET hits = hits + 1, last_at = excluded.last_at`
    ).bind(cacheKey(query), new Date().toISOString()).run();
  } catch { /* query_stats may not exist yet on a stale deploy — non-fatal */ }
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

// Fetches a query live (subject to the breaker + global slot) and updates the
// cache + breaker state. Shared by user-miss and background/cron refreshes.
async function refreshQuery(query, maxPages, env) {
  if (await breakerOpen(env)) return { ok: false, reason: 'breaker' };
  if (!(await reserveScrapeSlot(env))) return { ok: false, reason: 'slot' };

  const results = await scrapeEbay(query, maxPages, env);
  if (results.length) {
    await setCache(env, query, results);
    await clearBreaker(env);
    return { ok: true, results };
  }
  await setCache(env, query, []);   // negative-cache the block
  await tripBreaker(env);
  return { ok: false, reason: 'blocked' };
}

async function doSearch(query, maxPages, env, ctx) {
  await bumpQueryStat(env, query);
  const cached = await readCache(env, query);

  if (cached) {
    if (cached.negative) {
      if (cached.ageMs < BLOCKED_TTL_MS) {
        return err('eBay is temporarily rate-limiting this search. Please try again in 15 minutes.', 503);
      }
      // negative cache expired — fall through and try again
    } else if (cached.ageMs < CACHE_FRESH_MS) {
      return json(cached.data);                                  // fresh hit
    } else if (cached.ageMs < CACHE_STALE_MS) {
      // Stale-while-revalidate: serve now, refresh in the background.
      if (ctx) ctx.waitUntil(refreshQuery(query, maxPages, env));
      return json(cached.data);
    }
  }

  // Miss (or expired) — must fetch live, unless the breaker is open.
  if (await breakerOpen(env)) {
    if (cached && !cached.negative) return json(cached.data);    // serve any stale data
    return err('eBay searches are paused briefly while we cool down. Please try again shortly.', 503);
  }

  if (!(await reserveScrapeSlot(env))) {
    const stale = await getStaleCache(env, query);
    if (stale) return json(stale);
    return err('Too many searches at once — please wait a few seconds and try again.', 429);
  }

  const results = await scrapeEbay(query, maxPages, env);
  if (!results.length) {
    await setCache(env, query, []);
    await tripBreaker(env);
    const stale = await getStaleCache(env, query);
    if (stale) return json(stale);
    return err('eBay is temporarily rate-limiting this search. Please try again in 15 minutes.', 503);
  }
  await setCache(env, query, results);
  await clearBreaker(env);
  return json(results);
}

// Cron entry point: refresh the most popular queries that have gone stale,
// at a calm jittered pace, stopping early if eBay starts blocking.
async function prewarm(env) {
  if (await breakerOpen(env)) return;
  const { results } = await env.DB.prepare(
    'SELECT query FROM query_stats ORDER BY hits DESC LIMIT ?'
  ).bind(PREWARM_LIMIT).all();

  for (const row of results || []) {
    const cached = await readCache(env, row.query);
    if (cached && !cached.negative && cached.ageMs < CACHE_FRESH_MS) continue;  // still fresh
    await refreshQuery(row.query, 1, env);
    if (await breakerOpen(env)) break;                                          // got blocked — stop
    await sleep(4000 + Math.floor(Math.random() * 3000));                       // calm, jittered
  }
}

async function scrapeSearchGet(url, env, ctx) {
  const query    = url.searchParams.get('query');
  const maxPages = Math.min(parseInt(url.searchParams.get('max_pages') || '1'), 3);
  if (!query) return err('query parameter is required');
  return doSearch(query, maxPages, env, ctx);
}

async function scrapeSearchPost(request, env, ctx) {
  const b = await request.json();
  if (!b.query) return err('query is required');
  return doSearch(b.query, Math.min(b.max_pages || 1, 5), env, ctx);
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

function activeRoute(env) {
  if ((env.SCRAPER_API_KEY || '').trim()) return 'scraperapi';
  if ((env.CF_WORKER_URL  || '').trim()) return 'cf_worker';
  return 'direct';
}

async function scrapeDebug(url, env) {
  const ultra = env.SCRAPER_API_ULTRA === 'true';
  const premium = env.SCRAPER_API_PREMIUM === 'true';
  const result = {
    mode: 'in-worker',
    scraper_api_configured: Boolean((env.SCRAPER_API_KEY || '').trim()),
    scraper_api_proxy: ultra ? 'ultra_premium' : premium ? 'premium'
      : 'datacenter (default — often blocked by eBay)',
    cf_worker_url_configured: Boolean((env.CF_WORKER_URL || '').trim()),
    active_route: activeRoute(env),
    note: 'Add ?test=true to fire a live eBay fetch through the active route.',
  };

  if (url.searchParams.get('test') === 'true') {
    const html = await fetchEbayHtml('mahomes prizm', 1, env);
    result.fetched = Boolean(html);
    result.bot_detected = html ? isBotHtml(html) : null;
    if (html) {
      const parsed = await parseEbayHtml(html);
      result.parsed_count = parsed.length;
      result.sample = parsed.slice(0, 2);
    }
  }

  return json(result);
}

// ── eBay scraper (fetches + parses in-Worker) ───────────────────────────────

const EBAY_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
  'Referer': 'https://www.google.com/',
  'Upgrade-Insecure-Requests': '1',
  'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
  'Sec-Ch-Ua-Mobile': '?0',
  'Sec-Ch-Ua-Platform': '"Windows"',
  'Sec-Fetch-Dest': 'document',
  'Sec-Fetch-Mode': 'navigate',
  'Sec-Fetch-Site': 'cross-site',
};

function buildEbaySearchUrl(query, page) {
  const p = new URLSearchParams({
    _nkw: query, LH_Complete: '1', LH_Sold: '1', _pgn: String(page), _ipg: '60',
  });
  return `https://www.ebay.com/sch/i.html?${p}`;
}

// Transport routes in priority order. ScraperAPI gives a residential IP (the
// real fix for datacenter-IP blocking); CF_WORKER_URL is the existing eBay
// proxy worker; direct is a last resort (usually blocked).
function ebayRoutes(ebayUrl, env) {
  const routes = [];
  const key = (env.SCRAPER_API_KEY || '').trim();
  if (key) {
    const p = new URLSearchParams({ api_key: key, url: ebayUrl, country_code: env.SCRAPER_API_COUNTRY || 'us' });
    if (env.SCRAPER_API_RENDER === 'true') p.set('render', 'true');
    if (env.SCRAPER_API_ULTRA === 'true') p.set('ultra_premium', 'true');
    else if (env.SCRAPER_API_PREMIUM === 'true') p.set('premium', 'true');
    routes.push({ label: 'scraperapi', url: `https://api.scraperapi.com/?${p}`, headers: {} });
  }
  const cf = (env.CF_WORKER_URL || '').trim().replace(/\/$/, '');
  if (cf) {
    const headers = env.CF_WORKER_SECRET ? { 'X-Proxy-Secret': env.CF_WORKER_SECRET } : {};
    routes.push({ label: 'cf_worker', url: `${cf}?url=${encodeURIComponent(ebayUrl)}`, headers });
  }
  routes.push({ label: 'direct', url: ebayUrl, headers: EBAY_HEADERS });
  return routes;
}

function isBotHtml(html) {
  const l = html.toLowerCase();
  return l.includes('pardon our interruption') || l.includes('captcha')
    || l.includes('robot check') || l.includes('access denied');
}

// Try each route with retry + backoff; a bot page is retryable (fresh proxy IP).
async function fetchEbayHtml(query, page, env) {
  const ebayUrl = buildEbaySearchUrl(query, page);
  for (const route of ebayRoutes(ebayUrl, env)) {
    let backoff = 1000;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const r = await fetch(route.url, { headers: route.headers });
        if (r.ok) {
          const html = await r.text();
          if (!isBotHtml(html)) return html;
        } else if (![429, 500, 502, 503, 504].includes(r.status)) {
          break;  // non-retryable → next route
        }
      } catch { /* network error → retry */ }
      if (attempt < 3) { await sleep(backoff + Math.random() * 500); backoff *= 2; }
    }
  }
  return null;
}

async function scrapeEbay(query, maxPages, env) {
  const all = [];
  const seen = new Set();
  for (let page = 1; page <= maxPages; page++) {
    const html = await fetchEbayHtml(query, page, env);
    if (!html) break;
    const listings = await parseEbayHtml(html);
    if (!listings.length) break;
    for (const l of listings) {
      const key = l.item_number || l.listing_url;
      if (seen.has(key)) continue;
      seen.add(key);
      all.push(l);
    }
    if (page < maxPages) await sleep(800 + Math.random() * 800);
  }
  return all;
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
      const title = item._title.trim();
      if (isJunk(title)) return null;                 // drop lots/reprints/customs
      const price = parsePrice(item._price.split(' to ')[0]);
      if (price === null) return null;
      const [grade_company, grade] = parseGrade(title);
      return {
        title,
        sale_price:    price,
        sale_date:     parseDate(item._date),
        condition:     item._cond.trim() || null,
        listing_url:   item.url,
        image_url:     item.imgUrl,
        item_number:   itemNumber(item.url),
        grade,
        grade_company,
      };
    })
    .filter(Boolean);
}

const GRADE_RE = /\b(PSA|BGS|BVG|SGC|CGC|CSG|HGA|TAG)\s*\.?\s*(10|\d(?:\.5)?)\b/i;
const JUNK_RE  = /\b(lot|lots|reprint|re-print|rp|repack|digital|custom|sticker|decal|proxy|aceo|novelty|case\s*break|box\s*break|read\s*description)\b/i;
const ITEM_RE  = /\/itm\/(?:[^/]+\/)?(\d{6,})/;

function parseGrade(title) {
  const m = title.match(GRADE_RE);
  if (!m) return [null, null];
  const grade = parseFloat(m[2]);
  return [m[1].toUpperCase(), Number.isNaN(grade) ? null : grade];
}

function isJunk(title)   { return JUNK_RE.test(title); }
function itemNumber(url) { const m = (url || '').match(ITEM_RE); return m ? m[1] : null; }

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

# eBay scraper service

A tiny Flask service that fetches eBay sold listings (via `curl-cffi` browser
impersonation) and returns parsed JSON. The Cloudflare Worker delegates to it
when `SCRAPER_URL` is set, so the **host's outbound IP** is what talks to eBay.

> Why this exists: eBay blocks Cloudflare/Vercel datacenter IPs. Hosting the
> fetch on a different datacenter (e.g. Render, which has historically worked)
> often gets through for free. If one host's IP gets blocked, redeploy the same
> container to another free host.

## Endpoints
- `GET /health` → `{ "status": "ok", "proxy_configured": bool }`
- `GET /api/scrape?debug=true` → live fetch diagnostics (status, blocked, count)
- `POST /scrape` body `{ "query": "...", "max_pages": 1 }` → `[ { title, sale_price, sale_date, condition, listing_url, image_url, item_number, grade, grade_company }, ... ]`

## Deploy (any Docker host)
It's a standard Dockerfile that listens on `$PORT`. Works on Render, Railway,
Fly.io, Koyeb, Google Cloud Run, etc. Build context is this `scraper-service/`
directory.

## Environment variables (all optional)
| Var | Purpose |
| --- | --- |
| `SCRAPER_SECRET` | If set, callers must send `X-Scraper-Secret`. Set the same value as the Worker's `SCRAPER_SECRET`. |
| `SCRAPER_PROXY_URL` | Route eBay through a proxy (`http://user:pass@host:port`). |
| `SCRAPER_API_KEY` | ScraperAPI proxy-mode key (+ `SCRAPER_API_PREMIUM=true`, `SCRAPER_API_COUNTRY=us`). |

## Wire it to the Worker
After deploying, set on the Cloudflare Worker:
- `SCRAPER_URL` = your service's base URL (e.g. `https://my-scraper.onrender.com`)
- `SCRAPER_SECRET` = same secret as above (if you set one)

Then verify: `GET https://api.thecardhuddle.com/scrape/debug?test=true` should
show `"mode": "delegated"`, `"service_reachable": true`, and `parsed_count > 0`.

/**
 * GridironCards — eBay proxy worker
 *
 * Proxies eBay search pages through Cloudflare's edge network so
 * requests don't come from flagged datacenter IPs.
 *
 * Free tier: 100,000 requests / day
 *
 * Deploy:
 *   1. Go to dash.cloudflare.com → Workers & Pages → Create Worker
 *   2. Paste this file, click Save & Deploy
 *   3. Copy the *.workers.dev URL
 *   4. Set CF_WORKER_URL=<that URL> in Render environment variables
 *
 * Optional secret (recommended): set a CF_SECRET env var in the Worker,
 * then set the same value as CF_WORKER_SECRET in Render.
 */

const ALLOWED_HOST = "www.ebay.com";

export default {
  async fetch(request, env) {
    // Optional bearer-token auth so only your API can use the worker
    const secret = env.CF_SECRET;
    if (secret) {
      const auth = request.headers.get("X-Proxy-Secret");
      if (auth !== secret) {
        return new Response("Unauthorized", { status: 401 });
      }
    }

    const incoming = new URL(request.url);
    const target = incoming.searchParams.get("url");

    if (!target) {
      return json({ error: "Missing ?url= parameter" }, 400);
    }

    let targetUrl;
    try {
      targetUrl = new URL(target);
    } catch {
      return json({ error: "Invalid URL" }, 400);
    }

    if (targetUrl.hostname !== ALLOWED_HOST) {
      return json({ error: `Only ${ALLOWED_HOST} is allowed` }, 403);
    }

    const upstream = await fetch(targetUrl.toString(), {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
          "AppleWebKit/537.36 (KHTML, like Gecko) " +
          "Chrome/124.0.0.0 Safari/537.36",
        Accept:
          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
      },
    });

    const html = await upstream.text();

    return new Response(html, {
      status: upstream.status,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ALLOWED_HOST = "www.ebay.com";

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target   = incoming.searchParams.get("url");

    if (!target) {
      return Response.json({ error: "Missing ?url= parameter" }, { status: 400 });
    }

    let targetUrl;
    try {
      targetUrl = new URL(target);
    } catch {
      return Response.json({ error: "Invalid URL" }, { status: 400 });
    }

    if (targetUrl.hostname !== ALLOWED_HOST) {
      return Response.json({ error: `Only ${ALLOWED_HOST} is allowed` }, { status: 403 });
    }

    const upstream = await fetch(targetUrl.toString(), {
      headers: {
        "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Referer":                   "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua":                 '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile":          "?0",
        "Sec-Ch-Ua-Platform":        '"Windows"',
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "cross-site",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
      },
    });

    const html = await upstream.text();

    return new Response(html, {
      status: upstream.status,
      headers: {
        "Content-Type":                "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};

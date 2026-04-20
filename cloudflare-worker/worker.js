const ALLOWED_HOST = "www.ebay.com";

addEventListener("fetch", function(event) {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(request) {
  var incoming = new URL(request.url);
  var target = incoming.searchParams.get("url");

  if (!target) {
    return new Response(JSON.stringify({ error: "Missing ?url= parameter" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }

  var targetUrl;
  try {
    targetUrl = new URL(target);
  } catch (e) {
    return new Response(JSON.stringify({ error: "Invalid URL" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }

  if (targetUrl.hostname !== ALLOWED_HOST) {
    return new Response(JSON.stringify({ error: "Only " + ALLOWED_HOST + " is allowed" }), {
      status: 403,
      headers: { "Content-Type": "application/json" }
    });
  }

  var upstream = await fetch(targetUrl.toString(), {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
      "Accept-Encoding": "gzip, deflate, br",
      "Upgrade-Insecure-Requests": "1",
      "Sec-Fetch-Dest": "document",
      "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Site": "none",
      "Sec-Fetch-User": "?1",
      "Cache-Control": "max-age=0"
    }
  });

  var html = await upstream.text();

  return new Response(html, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Access-Control-Allow-Origin": "*"
    }
  });
}

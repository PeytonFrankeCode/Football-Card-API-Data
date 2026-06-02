# Generic container for the eBay scraper service. Works on any host that builds
# a Dockerfile (Render, Railway, Fly.io, Koyeb, Google Cloud Run, etc.).
# The host's outbound IP is what fetches eBay — pick a host whose IP eBay does
# not block (a datacenter like Render historically works; Cloudflare/Vercel do not).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api ./api

# Most free hosts inject the port to listen on via $PORT; default to 8000 locally.
# Single worker keeps eBay requests serialized (our anti-rate-limit design).
ENV PORT=8000
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 api.scrape:app

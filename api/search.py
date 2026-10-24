import csv
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import time
from http.server import BaseHTTPRequestHandler

DATA_URL = "https://raw.githubusercontent.com/otizemlanetzag/comunity-search/main/CRAWLED.CSV"
USER_AGENT = "CommunitySearch/1.0"
MAX_DATA_BYTES = 8_000_000
MAX_RESULTS = 25


def send_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def load_pages():
    request = urllib.request.Request(DATA_URL + "?v=" + str(int(time.time())), headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Pragma": "no-cache"})
    with urllib.request.urlopen(request, timeout=6) as response:
        raw = response.read(MAX_DATA_BYTES + 1)
    if len(raw) > MAX_DATA_BYTES:
        raise ValueError("Search data is too large; split the crawl index into smaller files.")
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def snippet_for(text, terms, limit=240):
    text = clean_text(text)
    if not text:
        return ""
    lowered = text.casefold()
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    start = max(0, (min(positions) - 75)) if positions else 0
    snippet = text[start:start + limit]
    if start:
        snippet = "…" + snippet
    if start + limit < len(text):
        snippet += "…"
    return snippet


def search_pages(query, pages):
    terms = [t.casefold() for t in re.findall(r"[^\W_]+", query, flags=re.UNICODE) if len(t) > 1]
    if not terms:
        return []
    results = []
    for row in pages:
        if (row.get("status") or "").casefold() not in ("ok", "done", ""):
            continue
        title = clean_text(row.get("title")) or clean_text(row.get("site_name")) or row.get("site_url", "")
        url = clean_text(row.get("site_url"))
        body = clean_text(row.get("text"))
        haystack = (title + " " + url + " " + body).casefold()
        hits = sum(haystack.count(term) for term in terms)
        matched = sum(1 for term in terms if term in haystack)
        if not matched:
            continue
        title_lower = title.casefold()
        url_lower = url.casefold()
        title_hits = sum(title_lower.count(term) for term in terms)
        url_hits = sum(url_lower.count(term) for term in terms)
        score = matched * 10 + min(hits, 50) + title_hits * 8 + url_hits * 4
        results.append({
            "title": title[:300],
            "url": url,
            "snippet": snippet_for(body, terms),
            "score": score,
            "crawled_at": clean_text(row.get("crawled_at"))
        })
    results.sort(key=lambda item: (item["score"], item["title"].casefold()), reverse=True)
    return results[:MAX_RESULTS]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        query = clean_text((params.get("q") or [""])[0])[:200]
        if not query:
            send_json(self, 200, {"ok": True, "query": "", "count": 0, "results": []})
            return
        try:
            pages = load_pages()
            results = search_pages(query, pages)
            send_json(self, 200, {
                "ok": True, "query": query, "count": len(results), "results": results,
                "indexed_pages": len(pages)
            })
        except urllib.error.URLError:
            send_json(self, 502, {"ok": False, "error": "Could not load the crawler index. Please try again shortly."})
        except Exception:
            send_json(self, 500, {"ok": False, "error": "Search is temporarily unavailable. Check the index and Vercel logs."})

    def do_POST(self):
        send_json(self, 405, {"ok": False, "error": "Use GET with the q parameter."})

    def log_message(self, format, *args):
        return

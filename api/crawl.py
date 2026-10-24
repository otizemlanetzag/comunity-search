import base64
import csv
import io
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

OWNER = "otizemlanetzag"
REPO = "comunity-search"
BRANCH = "main"
UA = "CommunitySearchCrawler/1.0 (+Vercel)"
MAX_BYTES = 1_500_000
MAX_LINKS = 100


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA}


def read_csv_file(path):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}"
    req = urllib.request.Request(url, headers=github_headers())
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            item = json.loads(response.read().decode("utf-8"))
        content = base64.b64decode(item["content"]).decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(content))), item["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return [], None
        raise


def write_csv_file(path, fields, rows, sha, message):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    payload = {"message": message, "content": base64.b64encode(output.getvalue().encode()).decode(),
               "branch": BRANCH}
    if sha:
        payload["sha"] = sha
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
        headers={**github_headers(), "Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=18) as response:
        response.read()


def validate_public_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid website URL")
    host = parsed.hostname
    if host.lower() == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("Private host blocked")
    addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Host could not be resolved")
    for address in addresses:
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("Private or non-public IP blocked")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title, self.text, self.links = [], [], []
        self.in_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "title":
            self.in_title = True
        if tag in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1
        if tag == "a" and attrs.get("href") and len(self.links) < MAX_LINKS:
            link = urllib.parse.urljoin(self.base_url, attrs["href"])
            parsed, base = urllib.parse.urlparse(link), urllib.parse.urlparse(self.base_url)
            if parsed.scheme in ("http", "https") and parsed.netloc == base.netloc:
                self.links.append(link)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in ("script", "style", "noscript", "svg") and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        if self.in_title:
            self.title.append(value)
        if not self.skip_depth and sum(map(len, self.text)) < 30000:
            self.text.append(value)


def crawl(url):
    validate_public_url(url)
    opener = urllib.request.build_opener(SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with opener.open(req, timeout=12) as response:
        final_url = response.geturl()
        validate_public_url(final_url)
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            raise ValueError("URL did not return an HTML page")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    import re
    match = re.search(br"charset\s*=\s*['\"]?([\w-]+)", raw[:8192], re.I)
    charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
    parser = PageParser(final_url)
    parser.feed(raw.decode(charset, errors="replace"))
    return {"crawled_at": utcnow(), "title": " ".join(parser.title)[:500],
            "text": " ".join(parser.text)[:30000], "links": json.dumps(parser.links, ensure_ascii=False),
            "status": "ok"}


def respond(self, status, payload):
    body = json.dumps(payload).encode()
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = os.environ.get("CRON_SECRET")
        if not secret or self.headers.get("Authorization", "") != f"Bearer {secret}":
            respond(self, 401, {"ok": False, "error": "Unauthorized"})
            return
        try:
            queue, queue_sha = read_csv_file("CRAWL_QUEUE.CSV")
            index = next((i for i, row in enumerate(queue) if row.get("status") == "pending"), None)
            if index is None:
                respond(self, 200, {"ok": True, "message": "No pending URLs"})
                return
            item = queue[index]
            try:
                result = crawl(item.get("site_url", ""))
                crawled, crawled_sha = read_csv_file("CRAWLED.CSV")
                crawled.append({"site_name": item.get("site_name", ""), "site_url": item.get("site_url", ""), **result})
                write_csv_file("CRAWLED.CSV",
                    ["site_name", "site_url", "crawled_at", "title", "text", "links", "status"],
                    crawled, crawled_sha, "Save crawled website data")
                queue[index]["status"], queue[index]["error"] = "done", ""
                outcome = "done"
            except Exception as exc:
                queue[index]["status"], queue[index]["error"] = "error", str(exc)[:300]
                outcome = "error"
            write_csv_file("CRAWL_QUEUE.CSV",
                ["site_name", "site_url", "status", "added_at", "error"],
                queue, queue_sha, "Update crawler queue")
            respond(self, 200, {"ok": True, "site": item.get("site_url"), "status": outcome})
        except Exception:
            respond(self, 500, {"ok": False, "error": "Crawler failed; check Vercel function logs"})

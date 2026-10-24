import base64
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

OWNER = "otizemlanetzag"
REPO = "comunity-search"
BRANCH = "main"
API_ROOT = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/"
HEADERS_BASE = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "comunity-search-site-form",
}


def headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN")
    return {**HEADERS_BASE, "Authorization": f"Bearer {token}"}


def read_csv(path, fields):
    req = urllib.request.Request(API_ROOT + path + "?ref=" + BRANCH, headers=headers())
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            obj = json.loads(response.read().decode("utf-8"))
        text = base64.b64decode(obj["content"]).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        return rows, obj["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return [], None


def write_csv(path, fields, rows, sha, message):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    payload = {"message": message, "content": base64.b64encode(output.getvalue().encode("utf-8")).decode("ascii"),
               "branch": BRANCH}
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(API_ROOT + path, data=json.dumps(payload).encode("utf-8"),
        headers={**headers(), "Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=18) as response:
        response.read()


class handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.reply(204, {})

    def do_GET(self):
        self.reply(405, {"error": "Method not allowed."})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10000:
                self.reply(400, {"error": "Invalid request."})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            name = str(data.get("site_name", "")).strip()
            url = str(data.get("site_url", "")).strip()
            if not name or len(name) > 120:
                self.reply(400, {"error": "Enter a website name (up to 120 characters)."})
                return
            if len(url) > 2048 or not re.fullmatch(r"https?://[^\s]+", url, re.IGNORECASE):
                self.reply(400, {"error": "Enter a valid URL starting with http:// or https://."})
                return

            # Store the submission in the crawl queue first, so it can be picked up by Vercel Cron.
            queue_fields = ["site_name", "site_url", "status", "added_at", "error"]
            queue, queue_sha = read_csv("CRAWL_QUEUE.CSV", queue_fields)
            queue.append({"site_name": name, "site_url": url, "status": "pending",
                          "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": ""})
            write_csv("CRAWL_QUEUE.CSV", queue_fields, queue, queue_sha, "Queue submitted website for crawling")

            site_fields = ["site_name", "site_url"]
            sites, sites_sha = read_csv("SITES.CSV", site_fields)
            sites.append({"site_name": name, "site_url": url})
            write_csv("SITES.CSV", site_fields, sites, sites_sha, "Add submitted website to directory")

            self.reply(200, {"ok": True, "message": "Website added to the crawl queue."})
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 422):
                self.reply(409, {"error": "The file changed during submission. Please try again."})
            else:
                self.reply(502, {"error": "GitHub could not save this submission. Check the GITHUB_TOKEN permissions."})
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            self.reply(400, {"error": "The submitted data is invalid."})
        except Exception:
            self.reply(500, {"error": "Could not save the submission. Check Vercel function logs."})

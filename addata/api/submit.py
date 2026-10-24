import base64
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

OWNER = "otizemlanetzag"
REPO = "comunity-search"
PATH = "SITES.CSV"
API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}"


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
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
        self._reply(204, {})

    def do_GET(self):
        self._reply(405, {"error": "Method not allowed."})

    def do_POST(self):
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            self._reply(500, {"error": "חסר משתנה הסביבה GITHUB_TOKEN ב-Vercel."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10000:
                self._reply(400, {"error": "בקשה לא תקינה."})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            name = str(data.get("site_name", "")).strip()
            url = str(data.get("site_url", "")).strip()

            if not name or len(name) > 120:
                self._reply(400, {"error": "יש להזין שם אתר באורך של עד 120 תווים."})
                return
            if len(url) > 2048 or not re.fullmatch(r"https?://[^\\s]+", url, re.IGNORECASE):
                self._reply(400, {"error": "יש להזין כתובת אתר תקינה שמתחילה ב-http:// או ב-https://."})
                return

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "comunity-search-site-form"
            }
            get_req = urllib.request.Request(API_URL, headers=headers)
            try:
                with urllib.request.urlopen(get_req, timeout=15) as response:
                    file_data = json.loads(response.read().decode("utf-8"))
                current = base64.b64decode(file_data["content"]).decode("utf-8-sig")
                sha = file_data["sha"]
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                current = "site_name,site_url\\r\\n"
                sha = None

            rows = list(csv.reader(io.StringIO(current)))
            if not rows:
                rows = [["site_name", "site_url"]]
            if rows[0] != ["site_name", "site_url"]:
                self._reply(500, {"error": "מבנה SITES.CSV אינו צפוי; לא נשמרו שינויים."})
                return
            rows.append([name, url])
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\\r\\n")
            writer.writerows(rows)
            payload = {
                "message": "Add site submission",
                "content": base64.b64encode(output.getvalue().encode("utf-8")).decode("ascii"),
                "branch": "main"
            }
            if sha:
                payload["sha"] = sha
            put_req = urllib.request.Request(
                API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={**headers, "Content-Type": "application/json"},
                method="PUT"
            )
            with urllib.request.urlopen(put_req, timeout=20) as response:
                response.read()
            self._reply(200, {"ok": True})
        except urllib.error.HTTPError as error:
            if error.code in (409, 422):
                self._reply(409, {"error": "הקובץ השתנה בזמן השליחה. נסו לשלוח שוב."})
            else:
                self._reply(502, {"error": "GitHub לא הצליח לשמור את התשובה. בדקו את הרשאות GITHUB_TOKEN."})
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": "הנתונים שנשלחו אינם תקינים."})
        except Exception:
            self._reply(500, {"error": "אירעה שגיאה בשמירת התשובה."})

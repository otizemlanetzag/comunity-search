import base64
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request

OWNER = "otizemlanetzag"
REPO = "comunity-search"
PATH = "SITES.CSV"
API_BASE = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}"


def reply(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def handler(request):
    if request.method == "OPTIONS":
        return reply(request, 204, {})
    if request.method != "POST":
        return reply(request, 405, {"error": "Method not allowed."})

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return reply(request, 500, {"error": "חסר משתנה הסביבה GITHUB_TOKEN ב-Vercel."})

    try:
        length = int(request.headers.get("Content-Length", "0"))
        if length <= 0 or length > 10000:
            return reply(request, 400, {"error": "בקשה לא תקינה."})
        data = json.loads(request.rfile.read(length).decode("utf-8"))
        name = str(data.get("site_name", "")).strip()
        url = str(data.get("site_url", "")).strip()

        if not name or len(name) > 120:
            return reply(request, 400, {"error": "יש להזין שם אתר באורך של עד 120 תווים."})
        if len(url) > 2048 or not re.fullmatch(r"https?://[^\s]+", url, re.IGNORECASE):
            return reply(request, 400, {"error": "יש להזין כתובת אתר תקינה שמתחילה ב-http:// או ב-https://."})

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "comunity-search-site-form"
        }
        get_req = urllib.request.Request(API_BASE, headers=headers)
        try:
            with urllib.request.urlopen(get_req, timeout=15) as response:
                file_data = json.loads(response.read().decode("utf-8"))
            current = base64.b64decode(file_data["content"]).decode("utf-8-sig")
            sha = file_data["sha"]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            current = "site_name,site_url\r\n"
            sha = None

        rows = list(csv.reader(io.StringIO(current)))
        if not rows:
            rows = [["site_name", "site_url"]]
        if rows[0] != ["site_name", "site_url"]:
            return reply(request, 500, {"error": "מבנה SITES.CSV אינו צפוי; לא נשמרו שינויים."})
        rows.append([name, url])
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerows(rows)
        encoded = base64.b64encode(output.getvalue().encode("utf-8")).decode("ascii")
        payload = {
            "message": "Add site submission",
            "content": encoded,
            "branch": "main"
        }
        if sha:
            payload["sha"] = sha
        put_req = urllib.request.Request(
            API_BASE,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(put_req, timeout=20) as response:
            response.read()
        return reply(request, 200, {"ok": True})
    except urllib.error.HTTPError as error:
        if error.code == 409 or error.code == 422:
            return reply(request, 409, {"error": "הקובץ השתנה בזמן השליחה. נסו לשלוח שוב."})
        return reply(request, 502, {"error": "GitHub לא הצליח לשמור את התשובה. בדקו את הרשאות GITHUB_TOKEN."})
    except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return reply(request, 400, {"error": "הנתונים שנשלחו אינם תקינים."})
    except Exception:
        return reply(request, 500, {"error": "אירעה שגיאה בשמירת התשובה."})

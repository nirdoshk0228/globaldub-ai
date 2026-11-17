#!/usr/bin/env python3
"""Seed the "Pehli Naukri" script as the permanent demo episode.

What it does (all through the existing API in app/main.py):
  1. POST samples/naukri_60s.json to /api/jobs with the admin token
     (admin bypasses the public-demo guardrails), requesting every language.
  2. Poll /api/jobs/{id} until the dub is ready (or partial).
  3. POST /api/admin/jobs/{id}/demo?is_demo=true so it shows in /api/gallery,
     which is what the website's "Hear the demo episode" button plays.
  4. With --exclusive, un-demo every other gallery episode so this is the only one.

The job row lives in Postgres and the audio in R2, so once seeded it is permanent;
re-run only to re-dub (e.g. after editing the script).

Stdlib only - no pip installs needed. Run it from the backend/ directory:

  python scripts/seed_naukri_demo.py \
    --api https://<your-service>.onrender.com \
    --admin-token "$ADMIN_TOKEN" \
    --langs en-US,ta-IN,te-IN,bn-IN,mr-IN,ar-SA,pt-BR,es-MX \
    --exclusive
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

READY_STATES = {"ready", "partial"}
DONE_STATES = READY_STATES | {"failed"}


def _request(method: str, url: str, *, headers=None, data=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    """Build a multipart/form-data body with one file part and several text parts."""
    boundary = "----globaldub" + uuid.uuid4().hex
    crlf = b"\r\n"
    out: list[bytes] = []
    for name, value in fields.items():
        out += [
            b"--" + boundary.encode(), crlf,
            f'Content-Disposition: form-data; name="{name}"'.encode(), crlf, crlf,
            str(value).encode(), crlf,
        ]
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    out += [
        b"--" + boundary.encode(), crlf,
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"'.encode(), crlf,
        f"Content-Type: {ctype}".encode(), crlf, crlf,
        file_path.read_bytes(), crlf,
        b"--" + boundary.encode() + b"--", crlf,
    ]
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the Pehli Naukri demo episode.")
    ap.add_argument("--api", required=True, help="Base API URL, e.g. https://xxx.onrender.com")
    ap.add_argument("--admin-token", required=True, help="ADMIN_TOKEN set on the server")
    ap.add_argument("--script", default="../samples/naukri_60s.json",
                    help="Path to the script JSON (default: ../samples/naukri_60s.json, i.e. run from backend/)")
    ap.add_argument("--langs", default="en-US,ta-IN,te-IN,bn-IN,mr-IN,ar-SA,pt-BR,es-MX",
                    help="Comma-separated target languages")
    ap.add_argument("--title", default="Pehli Naukri", help="Episode title shown in the gallery")
    ap.add_argument("--source-lang", default="hi-IN")
    ap.add_argument("--exclusive", action="store_true",
                    help="Un-demo every other gallery episode so this is the only demo")
    ap.add_argument("--timeout", type=int, default=1800, help="Seconds to wait for the dub (default 1800)")
    ap.add_argument("--poll", type=int, default=10, help="Seconds between status checks")
    args = ap.parse_args()

    api = args.api.rstrip("/")
    script = Path(args.script)
    if not script.exists():
        print(f"ERROR: script not found: {script.resolve()}", file=sys.stderr)
        return 2
    if script.suffix.lower() != ".json":
        print(f"ERROR: expected a .json script, got {script.suffix}", file=sys.stderr)
        return 2
    # Fail early on a malformed script rather than after a slow round-trip.
    try:
        doc = json.loads(script.read_text(encoding="utf-8"))
        assert doc.get("lines"), "script has no 'lines'"
    except Exception as e:
        print(f"ERROR: {script} is not a valid script: {e}", file=sys.stderr)
        return 2

    admin_headers = {"x-admin-token": args.admin_token}

    # 1) create the job ----------------------------------------------------
    body, content_type = _multipart(
        {"target_langs": args.langs, "title": args.title, "source_lang": args.source_lang},
        "file", script,
    )
    status, text = _request("POST", f"{api}/api/jobs",
                            headers={**admin_headers, "Content-Type": content_type}, data=body)
    if status != 201:
        print(f"ERROR: POST /api/jobs returned {status}: {text}", file=sys.stderr)
        return 1
    job_id = json.loads(text)["id"]
    print(f"created job {job_id}  langs={args.langs}")

    # 2) wait for the dub --------------------------------------------------
    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        status, text = _request("GET", f"{api}/api/jobs/{job_id}")
        if status != 200:
            print(f"ERROR: GET /api/jobs/{job_id} returned {status}: {text}", file=sys.stderr)
            return 1
        job = json.loads(text)
        if job["status"] != last:
            last = job["status"]
            print(f"  status: {last}")
        if job["status"] in DONE_STATES:
            break
        time.sleep(args.poll)
    else:
        print(f"ERROR: timed out after {args.timeout}s waiting for the dub.", file=sys.stderr)
        return 1

    if job["status"] == "failed":
        print(f"ERROR: dubbing failed: {job.get('error')}", file=sys.stderr)
        return 1
    if job["status"] not in READY_STATES:
        print(f"ERROR: job ended in unexpected state {job['status']}", file=sys.stderr)
        return 1
    ready_tracks = [t["lang"] for t in job.get("tracks", []) if t.get("dub_url")]
    print(f"  dubbed tracks: {', '.join(ready_tracks) or '(none)'}")

    # 3) make it a demo ----------------------------------------------------
    status, text = _request("POST", f"{api}/api/admin/jobs/{job_id}/demo?is_demo=true",
                            headers=admin_headers)
    if status != 200:
        print(f"ERROR: setting is_demo returned {status}: {text}", file=sys.stderr)
        return 1
    print(f"marked job {job_id} as a demo episode")

    # 4) optionally make it the only demo ----------------------------------
    if args.exclusive:
        status, text = _request("GET", f"{api}/api/gallery")
        if status == 200:
            for other in json.loads(text):
                if other["id"] != job_id:
                    _request("POST", f"{api}/api/admin/jobs/{other['id']}/demo?is_demo=false",
                             headers=admin_headers)
                    print(f"  un-demoed old episode {other['id']}")
        else:
            print(f"WARNING: could not read /api/gallery to clear old demos: {status}", file=sys.stderr)

    print(f"\nDone. Open the site and click \"Hear the demo episode\" - it will play job {job_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

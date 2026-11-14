"""Create the gallery episode on a running deployment (run once after deploying).

    python scripts/seed_demo.py --api https://your-api.onrender.com --admin-token $ADMIN_TOKEN \
        --langs en-US,ta-IN,te-IN,bn-IN,mr-IN,ar-SA,pt-BR,es-MX
"""
import argparse
import time
from pathlib import Path

import httpx

SAMPLE = Path(__file__).resolve().parent.parent.parent / "samples" / "virasat_ep01.json"
BED = SAMPLE.with_name("virasat_bed.mp3")

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8000")
ap.add_argument("--admin-token", required=True)
ap.add_argument("--langs", default="en-US,ta-IN,ar-SA")
ap.add_argument("--script", type=Path, default=SAMPLE)
ap.add_argument("--bed", type=Path, default=BED, help="background music track ('' for none)")
a = ap.parse_args()
h = {"x-admin-token": a.admin_token}
files = {"file": (a.script.name, a.script.read_bytes())}
if a.bed and Path(a.bed).is_file():
    files["bed"] = (Path(a.bed).name, Path(a.bed).read_bytes())
r = httpx.post(f"{a.api}/api/jobs", data={"target_langs": a.langs}, files=files, headers=h, timeout=180)
r.raise_for_status()
job = r.json()["id"]
print("job", job, "- waiting...")
while True:
    j = httpx.get(f"{a.api}/api/jobs/{job}", timeout=30).json()
    print("  ", j["status"], {t["lang"]: f'{t["stage"]} {t["progress"]:.0%}' for t in j["tracks"]})
    if j["status"] in ("ready", "partial", "failed"):
        break
    time.sleep(10)
httpx.post(f"{a.api}/api/admin/jobs/{job}/demo", headers=h, timeout=30).raise_for_status()
print("gallery episode ready:", job)

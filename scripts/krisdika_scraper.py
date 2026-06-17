#!/usr/bin/env python3
"""
Krisdika Law Scraper — via Wayback Machine
Source: PyThaiNLP/thai-law law_url_df.csv (2,215 laws from krisdika.go.th)
Rate: 3s delay between requests
Output: /root/webapp/legal-rag-api/scripts/output/krisdika_laws.csv
"""
import csv, re, time, urllib.request, urllib.parse, json
from pathlib import Path

OUTPUT_DIR = Path("/root/webapp/legal-rag-api/scripts/output")
OUTPUT_DIR.mkdir(exist_ok=True)
OUT_CSV = OUTPUT_DIR / "krisdika_laws.csv"
DONE_FILE = OUTPUT_DIR / ".krisdika_done.txt"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_wayback(sysid):
    url = f"https://www.krisdika.go.th/librarian/getfile?sysid={sysid}&ext=htm"
    wb_url = f"https://web.archive.org/web/2024/{url}"
    req = urllib.request.Request(wb_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    try:
        text = raw.decode('tis-620')
    except Exception:
        text = raw.decode('utf-8', 'ignore')
    # Strip scripts/styles
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Skip Wayback header junk — find first Thai char
    idx = next((i for i, c in enumerate(text) if '฀' <= c <= '๿'), 0)
    return text[max(0, idx-20):]

# Load law list from PyThaiNLP repo
print("Fetching law list from PyThaiNLP repo...")
api_url = "https://api.github.com/repos/PyThaiNLP/thai-law/contents/data/v0.3/law_url_df.csv"
req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github.v3+json"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read())

import base64
content = base64.b64decode(data['content']).decode('utf-8')
rows = list(csv.DictReader(content.splitlines()))
print(f"Total laws: {len(rows)}")

# Resume
done = set()
if DONE_FILE.exists():
    done = {x.strip() for x in DONE_FILE.read_text().splitlines() if x.strip()}
    print(f"Resume: {len(done)} already done")

fieldnames = ["sysid", "title", "article_text", "law_group", "law_type"]
mode = "a" if OUT_CSV.exists() and done else "w"
ok_count = 0

with open(OUT_CSV, mode, newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if mode == "w":
        w.writeheader()

    for i, row in enumerate(rows):
        sysid = str(row.get('sysid', '')).strip()
        title = row.get('title', '').strip()
        law_group = row.get('law_group', '').strip()
        law_type = row.get('law_type', '').strip()

        if not sysid or sysid in done:
            continue

        try:
            text = fetch_wayback(sysid)
            thai = len(re.findall(r'[ก-๙]', text))
            total = len([c for c in text if c.strip()])
            ratio = thai / total if total else 0

            if ratio >= 0.3 and len(text) > 200:
                w.writerow({
                    "sysid": f"kris_{sysid}",
                    "title": title,
                    "article_text": text[:6000],
                    "law_group": law_group,
                    "law_type": law_type,
                })
                f.flush()
                ok_count += 1
                print(f"[{i+1}/{len(rows)}] {sysid}: ✅ {ratio:.0%} {len(text)}c — {title[:50]}", flush=True)
            else:
                print(f"[{i+1}/{len(rows)}] {sysid}: skip Thai={ratio:.0%}", flush=True)

        except Exception as e:
            print(f"[{i+1}/{len(rows)}] {sysid}: ERR {e}", flush=True)

        with open(DONE_FILE, "a") as dp:
            dp.write(sysid + "\n")
        done.add(sysid)
        time.sleep(3)

print(f"\nDone: {ok_count} laws → {OUT_CSV}")

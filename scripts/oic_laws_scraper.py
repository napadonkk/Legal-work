#!/usr/bin/env python3
"""
OIC Law Scraper — mid=577 (ประกันชีวิต), mid=578 (ประกันวินาศ), mid=579 (พ.ร.บ.รถ)
Rate: 1 req/min per mid, runs all 3 sequentially
"""
import csv, json, time, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

BASE_URL = "https://www.oic.or.th/api"
OUTPUT_DIR = Path("/root/webapp/legal-rag-api/scripts/output")
DELAY = 60
PER_PAGE = 20

MIDS = {
    577: ("กฎหมายประกันชีวิต", "oic_law_577.csv"),
    578: ("กฎหมายประกันวินาศภัย", "oic_law_578.csv"),
    579: ("กฎหมายคุ้มครองผู้ประสบภัยจากรถ", "oic_law_579.csv"),
}

_token = None
_token_ts = 0

def get_token():
    global _token, _token_ts
    if _token and (time.time() - _token_ts) < 82000:
        return _token
    req = urllib.request.Request(
        f"{BASE_URL}/oauth2/guest/access-token",
        data=json.dumps({"grant_type":"client_credentials","client_id":"oic_web_client","scope":""}).encode(),
        headers={"Content-Type":"application/json","User-Agent":"Mozilla/5.0","Origin":"https://www.oic.or.th"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        _token = json.loads(r.read())["access_token"]
    _token_ts = time.time()
    return _token

def fetch_page(mid, page):
    token = get_token()
    url = f"{BASE_URL}/news?mid={mid}&siteID=1&page={page}&perPage={PER_PAGE}&lang=th&sortBy=_dtmins&sortType=asc"
    req = urllib.request.Request(url, headers={"Authorization":f"Bearer {token}","Accept":"application/json","User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def scrape_mid(mid, law_type, out_file):
    out_path = OUTPUT_DIR / out_file
    done_path = OUTPUT_DIR / f".done_{mid}.txt"
    done_pages = set()
    if done_path.exists():
        done_pages = {int(x) for x in done_path.read_text().splitlines() if x.strip()}
        print(f"[{mid}] resume: {len(done_pages)} pages done")

    first = fetch_page(mid, 1)
    page_count = first["pageInfo"]["pageCount"]
    print(f"[{mid}] {law_type}: {page_count} pages")

    fieldnames = ["sysid","title","article_text","law_group","law_type"]
    mode = "a" if done_path.exists() and out_path.exists() else "w"
    total = 0

    with open(out_path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if mode == "w": w.writeheader()

        if 1 not in done_pages:
            rows = []
            for item in first.get("items",[]):
                subj = (item.get("_subject") or "").strip()
                cate = (item.get("cate_name") or law_type).strip()
                dt = datetime.fromtimestamp(item.get("_postdate",0)).strftime("%Y-%m-%d") if item.get("_postdate") else ""
                rows.append({"sysid":f"oic_{item['_id']}","title":subj,"article_text":f"[{cate}] {dt} {subj}".strip(),"law_group":"ประกันภัย","law_type":law_type})
            w.writerows(rows); f.flush(); total += len(rows)
            with open(done_path,"a") as dp: dp.write("1\n")
            done_pages.add(1)
            print(f"[{mid}] p1/{page_count}: {len(rows)} rows")

        for page in range(2, page_count + 1):
            if page in done_pages: continue
            print(f"[{mid}] waiting {DELAY}s → p{page}/{page_count}...", flush=True)
            time.sleep(DELAY)
            try:
                data = fetch_page(mid, page)
                rows = []
                for item in data.get("items",[]):
                    subj = (item.get("_subject") or "").strip()
                    cate = (item.get("cate_name") or law_type).strip()
                    dt = datetime.fromtimestamp(item.get("_postdate",0)).strftime("%Y-%m-%d") if item.get("_postdate") else ""
                    rows.append({"sysid":f"oic_{item['_id']}","title":subj,"article_text":f"[{cate}] {dt} {subj}".strip(),"law_group":"ประกันภัย","law_type":law_type})
                w.writerows(rows); f.flush(); total += len(rows)
                with open(done_path,"a") as dp: dp.write(f"{page}\n")
                done_pages.add(page)
                print(f"[{mid}] p{page}/{page_count}: {len(rows)} rows (total {total})", flush=True)
            except Exception as e:
                print(f"[{mid}] ERROR p{page}: {e}")
                break

    print(f"[{mid}] DONE: {total} rows → {out_path}")
    return total

for mid, (law_type, out_file) in MIDS.items():
    scrape_mid(mid, law_type, out_file)

print("\nAll mids complete.")

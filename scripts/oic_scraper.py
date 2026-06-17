#!/usr/bin/env python3
"""
OIC Announcement Scraper
Source: www.oic.or.th/api — guest token (auto-renewed every 24h)

Pipeline:
1. Get guest bearer token from OIC API
2. Paginate through /api/news?mid=254&siteID=1 (1022+ announcements)
3. Extract: _id, _subject, _desc, _postdate, cate_name
4. Save to oic_clean.csv — compatible with build_index.py

Rate limit: 1 request/minute (60s delay between pages)
Each page = 20 items → ~52 pages → ~52 minutes total

Output CSV columns: sysid, title, article_text, law_group, law_type
(same schema as laws_articles.csv for build_index.py compatibility)
"""
import csv, json, os, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_URL   = "https://www.oic.or.th/api"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_CSV = "oic_clean.csv"
DONE_FILE  = ".oic_done_pages.txt"

MID        = 254       # announcement module ID
SITE_ID    = 1
PER_PAGE   = 20        # items per API call
DELAY      = 60        # seconds between pages (1 req/min)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.oic.or.th",
    "Referer": "https://www.oic.or.th/th/laws",
}

_token: str | None = None
_token_ts: float = 0
TOKEN_TTL = 82000  # refresh before 24h expires


def get_token() -> str:
    global _token, _token_ts
    if _token and (time.time() - _token_ts) < TOKEN_TTL:
        return _token
    print("[oic] refreshing bearer token...")
    req = urllib.request.Request(
        f"{BASE_URL}/oauth2/guest/access-token",
        data=json.dumps({
            "grant_type": "client_credentials",
            "client_id": "oic_web_client",
            "scope": ""
        }).encode(),
        headers={**HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    _token = data["access_token"]
    _token_ts = time.time()
    return _token


def fetch_page(page: int) -> dict:
    token = get_token()
    url = (
        f"{BASE_URL}/news"
        f"?mid={MID}&siteID={SITE_ID}&page={page}&perPage={PER_PAGE}"
        f"&lang=th&sortBy=_dtmins&sortType=asc"
    )
    req = urllib.request.Request(url, headers={
        **HEADERS,
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def item_to_row(item: dict) -> dict:
    subject = (item.get("_subject") or "").strip()
    desc = (item.get("_desc") or "").strip()
    # Combine subject + desc (often same, dedup)
    if desc and desc != subject:
        text = f"{subject}\n{desc}"
    else:
        text = subject

    postdate = item.get("_postdate") or 0
    dt_str = datetime.fromtimestamp(postdate).strftime("%Y-%m-%d") if postdate else ""
    cate = (item.get("cate_name") or item.get("_cate_name") or "").strip()

    return {
        "sysid":        f"oic_{item['_id']}",
        "title":        subject,
        "article_text": f"[{cate}] {dt_str} {text}".strip(),
        "law_group":    "ประกันภัย",
        "law_type":     cate or "ประกาศ คปภ.",
    }


def scrape(resume: bool = True):
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path  = OUTPUT_DIR / OUTPUT_CSV
    done_path = OUTPUT_DIR / DONE_FILE

    done_pages: set[int] = set()
    if resume and done_path.exists():
        done_pages = {int(x) for x in done_path.read_text().splitlines() if x.strip()}
        print(f"[oic] resume: {len(done_pages)} pages already done")

    # Get total pages on first run
    # OIC API: "pageCount" = total number of pages, "total" = items on current page
    first = fetch_page(1)
    page_info  = first.get("pageInfo", {})
    page_count = page_info.get("pageCount", 1)   # total pages
    total_items = page_count * PER_PAGE           # approx total items
    print(f"[oic] ~{total_items} announcements across {page_count} pages")

    write_mode = "a" if resume and out_path.exists() else "w"
    fieldnames = ["sysid", "title", "article_text", "law_group", "law_type"]

    total_rows = 0
    with open(out_path, write_mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_mode == "w":
            writer.writeheader()

        # Process page 1 items if not done
        if 1 not in done_pages:
            rows = [item_to_row(item) for item in first.get("items", [])]
            writer.writerows(rows)
            f.flush()
            total_rows += len(rows)
            with open(done_path, "a") as dp:
                dp.write("1\n")
            done_pages.add(1)
            print(f"[oic] page 1/{page_count}: {len(rows)} rows")

        # Remaining pages
        for page in range(2, page_count + 1):
            if page in done_pages:
                continue

            print(f"[oic] waiting {DELAY}s before page {page}/{page_count}...", flush=True)
            time.sleep(DELAY)

            try:
                data = fetch_page(page)
                items = data.get("items", [])
                rows = [item_to_row(item) for item in items]
                writer.writerows(rows)
                f.flush()
                total_rows += len(rows)
                with open(done_path, "a") as dp:
                    dp.write(f"{page}\n")
                done_pages.add(page)
                print(f"[oic] page {page}/{page_count}: {len(rows)} rows (total: {total_rows})", flush=True)
            except Exception as e:
                print(f"[oic] ERROR page {page}: {e} — will retry on next run")
                break

    print(f"\n[oic] DONE — {total_rows} rows → {out_path}")
    return str(out_path)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="OIC Announcement Scraper")
    p.add_argument("--no-resume", action="store_true", help="Start fresh")
    args = p.parse_args()
    scrape(resume=not args.no_resume)

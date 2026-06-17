#!/usr/bin/env python3
"""Krisdika Law Scraper — OCS API with correct KTDatatable format"""
import csv, re, time, json, urllib.request, urllib.parse
from pathlib import Path

OUTPUT_DIR = Path("/root/webapp/legal-rag-api/scripts/output")
OUT_CSV = OUTPUT_DIR / "krisdika_laws2.csv"
DONE_FILE = OUTPUT_DIR / ".krisdika_done2.txt"

API = "https://www.ocs.go.th/searchlaw/indexs/list_table_search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.ocs.go.th/searchlaw-law",
    "Origin": "https://www.ocs.go.th",
}
CHARS = ['ก','ข','ค','ง','จ','ช','ด','ต','ถ','ท','ธ','น','บ','ป','ผ','ฝ','พ','ฟ','ภ','ม','ย','ร','ล','ว','ศ','ส','ห','อ']
PERPAGE = 50

done = set()
if DONE_FILE.exists():
    done = {x.strip() for x in DONE_FILE.read_text().splitlines() if x.strip()}
    print(f"Resume: {len(done)} pages done")

fieldnames = ["sysid","title","article_text","law_group","law_type"]
mode = "a" if OUT_CSV.exists() and done else "w"
total_ok = 0

with open(OUT_CSV, mode, newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if mode == "w": w.writeheader()

    for letter in CHARS:
        page = 1
        total_pages = None
        while total_pages is None or page <= total_pages:
            key = f"{letter}_{page}"
            if key in done:
                page += 1
                continue
            try:
                params = {
                    "query[letter]": letter,
                    "query[tab_type]": "law",
                    "query[type_view]": "law",
                    "query[q]": "",
                    "pagination[page]": str(page),
                    "pagination[perpage]": str(PERPAGE),
                    "sort[field]": "",
                    "sort[sort]": "asc",
                }
                body = urllib.parse.urlencode(params).encode()
                req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read())

                meta = data.get("meta", {})
                if total_pages is None:
                    import math
                    total_items = meta.get("total", 0)
                    total_pages = math.ceil(total_items / PERPAGE)
                    print(f"[{letter}] {total_items} laws, {total_pages} pages", flush=True)

                for item in data.get("data", []):
                    content = item.get("contentlaw", "").strip()
                    if not content or len(content) < 50: continue
                    thai = len(re.findall(r"[ก-๙]", content))
                    total_c = len([c for c in content if c.strip()])
                    if total_c and thai/total_c < 0.2: continue

                    code = item.get("lawCode","").replace("/","_")
                    title = item.get("lawNameTh","").strip()
                    pub = item.get("publishDate","")
                    law_type = "อื่นๆ"
                    for lt in ["รัฐธรรมนูญ","ประมวลกฎหมาย","พระราชบัญญัติ","พระราชกำหนด","พระราชกฤษฎีกา","กฎกระทรวง","ประกาศ","คำสั่ง"]:
                        if title.startswith(lt): law_type = lt; break

                    w.writerow({
                        "sysid": f"kris_{code}",
                        "title": title,
                        "article_text": f"[{pub}] {title}\n{content}"[:6000],
                        "law_group": "กฎหมายทั่วไป",
                        "law_type": law_type,
                    })
                    total_ok += 1

                f.flush()
                with open(DONE_FILE,"a") as dp: dp.write(key+"\n")
                done.add(key)
                print(f"[{letter}] p{page}/{total_pages}: ok={total_ok}", flush=True)
                page += 1

            except Exception as e:
                print(f"[{letter}] p{page}: ERR {e}", flush=True)
                page += 1
            time.sleep(1.5)

print(f"\nTotal: {total_ok} laws → {OUT_CSV}")

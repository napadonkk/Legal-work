#!/usr/bin/env python3
"""
Krisdika Law Scraper — OCS API (ocs.go.th/searchlaw/indexs/list_table_search)
Uses the official OCS JSON API — returns contentlaw field with full law text
Rate: 2s delay between pages, 10 results/page
Output: /root/webapp/legal-rag-api/scripts/output/krisdika_laws.csv
"""
import csv, re, time, json, urllib.request, urllib.parse
from pathlib import Path

OUTPUT_DIR = Path("/root/webapp/legal-rag-api/scripts/output")
OUTPUT_DIR.mkdir(exist_ok=True)
OUT_CSV = OUTPUT_DIR / "krisdika_laws.csv"
DONE_FILE = OUTPUT_DIR / ".krisdika_api_done.txt"
API_URL = "https://www.ocs.go.th/searchlaw/indexs/list_table_search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.ocs.go.th/searchlaw-law",
    "Origin": "https://www.ocs.go.th",
}
PERPAGE = 10
TOTAL_EST = 1884  # from first response meta.total

# Thai law groups we want (from PyThaiNLP law_groups.csv)
LAW_GROUPS = [
    "การเงิน การคลัง และวิธีการงบประมาณ",
    "การเมืองการปกครอง",
    "ขนส่งและคมนาคม",
    "คนต่างด้าว",
    "ครอบครัว และมรดก",
    "ความมั่นคง และการรักษาความสงบเรียบร้อย",
    "คุ้มครองผู้บริโภค",
    "ทรัพยากรธรรมชาติ พลังงาน และสิ่งแวดล้อม",
    "ทรัพย์สินทางปัญญา",
    "ที่ดิน",
    "ทุจริต และประพฤติมิชอบ",
    "ธนาคาร สถาบันการเงิน และตลาดหลักทรัพย์",
    "ธุรกิจ และพาณิชยกรรม",
    "ภาษีอากร และค่าธรรมเนียม",
    "ศาล และกระบวนการยุติธรรม",
    "สาธารณสุข",
    "สวัสดิการสังคม",
    "แรงงาน",
]

done_pages = set()
if DONE_FILE.exists():
    done_pages = {x.strip() for x in DONE_FILE.read_text().splitlines() if x.strip()}
    print(f"Resume: {len(done_pages)} pages done")

fieldnames = ["sysid","title","article_text","law_group","law_type"]
mode = "a" if OUT_CSV.exists() and done_pages else "w"
total_ok = 0

with open(OUT_CSV, mode, newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if mode == "w": w.writeheader()

    # Paginate through all results using keyword="" (get all)
    page = 1
    total_pages = None
    while total_pages is None or page <= total_pages:
        page_key = str(page)
        if page_key in done_pages:
            page += 1
            continue

        try:
            payload = json.dumps({"keyword": "", "page": page, "perpage": PERPAGE}).encode()
            req = urllib.request.Request(API_URL, data=payload, headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())

            meta = data.get("meta", {})
            if total_pages is None:
                total_pages = meta.get("pages", 200)
                print(f"Total: {meta.get('total')} laws, {total_pages} pages")

            items = data.get("data", [])
            for item in items:
                content = item.get("contentlaw", "").strip()
                if not content or len(content) < 50:
                    continue
                thai = len(re.findall(r"[ก-๙]", content))
                total_chars = len([c for c in content if c.strip()])
                ratio = thai / total_chars if total_chars else 0
                if ratio < 0.2:
                    continue

                law_code = item.get("lawCode", "")
                title = item.get("lawNameTh", "").strip()
                pub_date = item.get("publishDate", "")
                # Determine law_type from title prefix
                law_type = "อื่นๆ"
                for lt in ["รัฐธรรมนูญ","ประมวลกฎหมาย","พระราชบัญญัติ","พระราชกำหนด","พระราชกฤษฎีกา","กฎกระทรวง","ประกาศ","คำสั่ง"]:
                    if title.startswith(lt):
                        law_type = lt; break

                # law_group from lawCode prefix (heuristic)
                law_group = "กฎหมายทั่วไป"

                text = f"[{pub_date}] {title}\n{content}"
                w.writerow({
                    "sysid": f"kris_{law_code.replace('/','_')}",
                    "title": title,
                    "article_text": text[:6000],
                    "law_group": law_group,
                    "law_type": law_type,
                })
                total_ok += 1

            f.flush()
            with open(DONE_FILE, "a") as dp: dp.write(page_key + "\n")
            done_pages.add(page_key)
            print(f"Page {page}/{total_pages}: {len(items)} items, cumulative ok: {total_ok}", flush=True)
            page += 1

        except Exception as e:
            print(f"Page {page}: ERR {e}", flush=True)
            page += 1

        time.sleep(2)

print(f"\nDone: {total_ok} laws → {OUT_CSV}")

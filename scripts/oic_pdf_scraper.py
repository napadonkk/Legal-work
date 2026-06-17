#!/usr/bin/env python3
"""
OIC PDF Scraper — download 318 PDFs from oiceservice via Wayback-discovered URLs
Extract Thai text with pdfplumber inside Docker
Rate: polite delays, resume-capable
"""
import csv, json, time, urllib.request, urllib.parse, subprocess
from pathlib import Path

OUTPUT_DIR = Path("/root/webapp/legal-rag-api/scripts/output")
PDF_DIR = Path("/root/webapp/legal-rag-api/scripts/output/pdfs")
PDF_DIR.mkdir(exist_ok=True)

# All 318 unique PDF URLs from Wayback Machine
WAYBACK_URLS = []

# Fetch from Wayback CDX
print("Fetching PDF list from Wayback Machine...")
q = urllib.parse.urlencode({
    "url": "oiceservice.oic.or.th/document/Law/file/*",
    "output": "json", "limit": "1000", "fl": "original",
    "collapse": "original"
})
req = urllib.request.Request(f"http://web.archive.org/cdx/search/cdx?{q}", headers={"User-Agent":"Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read())
    
urls = list({item[0] for item in data if item[0] != "original" and item[0].endswith(".pdf")})
print(f"Found {len(urls)} unique PDF URLs")

# Save URL list
done_path = OUTPUT_DIR / ".pdf_done.txt"
done = set()
if done_path.exists():
    done = {x.strip() for x in done_path.read_text().splitlines() if x.strip()}
    print(f"Resume: {len(done)} already done")

out_path = OUTPUT_DIR / "oic_pdfs.csv"
mode = "a" if out_path.exists() and done else "w"
fieldnames = ["sysid","title","article_text","law_group","law_type"]

total = 0
with open(out_path, mode, newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if mode == "w": w.writeheader()

    for i, url in enumerate(sorted(urls)):
        if url in done:
            continue
        
        # Extract file_id from URL
        import re
        m = re.search(r'/file/(\w+)/(\w+)\.pdf', url)
        if not m: continue
        file_id = m.group(1)
        filename = m.group(2)
        pdf_path = PDF_DIR / f"{filename}.pdf"

        print(f"[{i+1}/{len(urls)}] {file_id}...", flush=True)

        # Download PDF
        try:
            req2 = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=30) as r:
                pdf_data = r.read()
            with open(pdf_path, "wb") as pf:
                pf.write(pdf_data)
        except Exception as e:
            print(f"  Download fail: {e}")
            time.sleep(5)
            continue

        # Extract text via Docker container (has pdfplumber)
        try:
            result = subprocess.run(
                ["docker","exec","-i","legal-rag-api","python3","-c",
                 f"""
import pdfplumber, re, sys
try:
    with pdfplumber.open('/tmp/oic_{file_id}.pdf') as pdf:
        text = '\\n'.join(p.extract_text() or '' for p in pdf.pages)
    thai = len(re.findall(r'[ก-๙]', text))
    total = len([c for c in text if c.strip()])
    ratio = thai/total if total else 0
    print(f'{{ratio:.2f}}|{{text[:3000]}}')
except Exception as e:
    print(f'0.00|ERROR: {{e}}')
"""],
                input=pdf_data,
                capture_output=True, text=True, timeout=30
            )
            # Copy PDF to container first
            subprocess.run(["docker","cp",str(pdf_path),f"legal-rag-api:/tmp/oic_{file_id}.pdf"], check=True, timeout=10)
            result2 = subprocess.run(
                ["docker","exec","legal-rag-api","python3","-c",
                 f"import pdfplumber,re;pdf=pdfplumber.open('/tmp/oic_{file_id}.pdf');text='\\n'.join(p.extract_text() or '' for p in pdf.pages);thai=len(re.findall(r'[ก-๙]',text));total=len([c for c in text if c.strip()]);print(f'{{thai/total if total else 0:.2f}}|{{text[:3000]}}')"],
                capture_output=True, text=True, timeout=30
            )
            out = result2.stdout.strip()
            if "|" in out:
                ratio_str, text = out.split("|", 1)
                ratio = float(ratio_str)
                if ratio >= 0.15 and len(text) > 50:
                    title = f"กฎหมายประกันภัย OIC {file_id}"
                    w.writerow({"sysid":f"oicpdf_{file_id}","title":title,"article_text":text.strip(),"law_group":"ประกันภัย","law_type":"กฎหมาย คปภ."})
                    f.flush(); total += 1
                    print(f"  ✅ Thai={ratio:.0%} {len(text)} chars")
                else:
                    print(f"  ⚠️ Thai={ratio:.0%} (skipped)")
            # Cleanup
            subprocess.run(["docker","exec","legal-rag-api","rm",f"/tmp/oic_{file_id}.pdf"], timeout=5)
        except Exception as e:
            print(f"  Extract fail: {e}")

        with open(done_path,"a") as dp: dp.write(url+"\n")
        done.add(url)
        time.sleep(3)  # polite delay

print(f"\nDone: {total} PDFs extracted → {out_path}")

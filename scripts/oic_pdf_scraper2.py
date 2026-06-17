#!/usr/bin/env python3
import csv, json, time, urllib.request, urllib.parse, subprocess, re
from pathlib import Path

OUTPUT_DIR = Path("/root/webapp/legal-rag-api/scripts/output")
PDF_DIR = OUTPUT_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)

print("Fetching PDF list from Wayback Machine...")
q = urllib.parse.urlencode({"url":"oiceservice.oic.or.th/document/Law/file/*","output":"json","limit":"1000","fl":"original","collapse":"original"})
req = urllib.request.Request(f"http://web.archive.org/cdx/search/cdx?{q}", headers={"User-Agent":"Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read())
urls = sorted({item[0] for item in data if item[0] != "original" and item[0].endswith(".pdf")})
print(f"Total unique PDFs: {len(urls)}")

done_path = OUTPUT_DIR / ".pdf_done.txt"
done = set()
if done_path.exists():
    done = {x.strip() for x in done_path.read_text().splitlines() if x.strip()}
    print(f"Resume: {len(done)} already done")

out_path = OUTPUT_DIR / "oic_pdfs.csv"
mode = "a" if out_path.exists() and done else "w"
fieldnames = ["sysid","title","article_text","law_group","law_type"]
total_ok = 0

with open(out_path, mode, newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if mode == "w": w.writeheader()

    for i, url in enumerate(urls):
        if url in done:
            continue

        m = re.search(r'/file/(\w+)/(\w+)\.pdf', url)
        if not m: continue
        file_id = m.group(1)
        fname = m.group(2)
        pdf_path = PDF_DIR / f"{fname}.pdf"

        # Download
        if not pdf_path.exists():
            try:
                req2 = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
                with urllib.request.urlopen(req2, timeout=30) as r:
                    pdf_data = r.read()
                with open(pdf_path, "wb") as pf:
                    pf.write(pdf_data)
            except Exception as e:
                print(f"[{i+1}/{len(urls)}] {file_id}: DL fail {e}", flush=True)
                time.sleep(2)
                continue

        # Copy to container and extract
        try:
            subprocess.run(["docker","cp",str(pdf_path),f"legal-rag-api:/tmp/cur.pdf"], check=True, capture_output=True, timeout=10)
            result = subprocess.run(
                ["docker","exec","legal-rag-api","python3","-c",
                 "import pdfplumber,re,sys\n"
                 "try:\n"
                 "  pdf=pdfplumber.open('/tmp/cur.pdf')\n"
                 "  text='\\n'.join(p.extract_text() or '' for p in pdf.pages)\n"
                 "  pdf.close()\n"
                 "  thai=len(re.findall(r'[ก-๙]',text))\n"
                 "  tot=len([c for c in text if c.strip()])\n"
                 "  print(f'{thai/tot if tot else 0:.2f}|{text[:3000]}')\n"
                 "except Exception as e: print(f'0.00|ERR:{e}')"],
                capture_output=True, text=True, timeout=30
            )
            out = result.stdout.strip()
            if "|" in out:
                ratio_str, text = out.split("|", 1)
                ratio = float(ratio_str)
                if ratio >= 0.15 and len(text.strip()) > 100:
                    w.writerow({"sysid":f"oicpdf_{file_id}","title":f"กฎหมายประกันภัย คปภ. {file_id}","article_text":text.strip(),"law_group":"ประกันภัย","law_type":"กฎหมาย คปภ."})
                    f.flush(); total_ok += 1
                    print(f"[{i+1}/{len(urls)}] {file_id}: ✅ Thai={ratio:.0%} {len(text)}c", flush=True)
                else:
                    print(f"[{i+1}/{len(urls)}] {file_id}: skip Thai={ratio:.0%}", flush=True)
            else:
                print(f"[{i+1}/{len(urls)}] {file_id}: no output", flush=True)
        except Exception as e:
            print(f"[{i+1}/{len(urls)}] {file_id}: extract err {e}", flush=True)

        with open(done_path,"a") as dp: dp.write(url+"\n")
        done.add(url)
        time.sleep(2)

print(f"\nDone: {total_ok} PDFs with good Thai text → {out_path}")

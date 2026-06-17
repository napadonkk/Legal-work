#!/usr/bin/env python3
"""
Legal RAG API — FastAPI backend for legal-work.tonygroup.org search tab
Databases: Thai Law (42K cleaned), OIC Insurance (249), Supreme Court (1,207),
           Civil & Commercial (1,874 article-level), Criminal (453 article-level)
"""
import os, sys, pickle, time, json, faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import anthropic

DATA_DIR  = os.environ.get("DATA_DIR", "/data")
AUTH_FILE = os.environ.get("MINIMAX_AUTH", "/root/.hermes/auth.json")

# ── Shared model (loaded once) ───────────────────────────────────────────────
_model  = None
_claude = None

def model():
    global _model
    if _model is None:
        print("[legal-rag] loading SentenceTransformer...", flush=True)
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        print("[legal-rag] model ready", flush=True)
    return _model

def claude():
    global _claude
    if _claude is None:
        with open(AUTH_FILE) as f:
            auth = json.load(f)
        p = auth["providers"]["minimax-oauth"]
        _claude = anthropic.Anthropic(
            api_key  = p["access_token"],
            base_url = p["inference_base_url"],
        )
        print("[legal-rag] MiniMax client ready", flush=True)
    return _claude


# ── Generic FAISS searcher ───────────────────────────────────────────────────
class LawSearcher:
    def __init__(self, index_path, meta_path, snippet_path, id_field, kw_fields=("title",)):
        self.index     = faiss.read_index(index_path)
        with open(meta_path, "rb") as f:
            self.metas = pickle.load(f)
        with open(snippet_path, "rb") as f:
            self.snippets = pickle.load(f)   # dict: id → snippet_str or dict
        self.id_field  = id_field
        self.kw_fields = kw_fields
        print(f"[legal-rag] loaded {self.index.ntotal:,} vectors from {index_path}", flush=True)

    def _kw_text(self, meta: dict) -> str:
        return " ".join(str(meta.get(f, "")) for f in self.kw_fields).lower()

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        q_emb = model().encode([query])
        faiss.normalize_L2(q_emb)
        search_k = min(top_k * 8, self.index.ntotal)
        scores, idxs = self.index.search(q_emb.astype("float32"), search_k)

        q_terms = query.lower().split()
        results = []
        for sim, idx in zip(scores[0], idxs[0]):
            if idx < 0:
                continue
            meta    = self.metas[int(idx)]
            rec_id  = meta[self.id_field]
            raw_snip = self.snippets.get(rec_id, "")
            snippet  = raw_snip if isinstance(raw_snip, str) else (
                raw_snip.get("fact", "") + " | " + raw_snip.get("decision", ""))

            kw_txt   = self._kw_text(meta)
            kw_boost = sum(0.12 for t in q_terms if len(t) >= 2 and t in kw_txt)
            combined = float(sim) + kw_boost

            results.append({**meta, "score": round(combined, 4), "sim": round(float(sim), 4),
                             "snippet": snippet[:380]})

        results.sort(key=lambda r: r["score"], reverse=True)
        for i, r in enumerate(results, 1):
            r["rank"] = i
        return results[:top_k]


# ── Database searchers (lazy init) ───────────────────────────────────────────
_thai = _oic = _court = _civil = _criminal = None

def thai():
    global _thai
    if _thai is None:
        _thai = LawSearcher(
            index_path   = f"{DATA_DIR}/thailaw_faiss_new/thailaw_faiss.index",
            meta_path    = f"{DATA_DIR}/thailaw_faiss_new/thailaw_meta.pkl",
            snippet_path = f"{DATA_DIR}/thai_law_snippets_new.pkl",
            id_field     = "sysid",
            kw_fields    = ("title",),
        )
    return _thai

def oic():
    global _oic
    if _oic is None:
        _oic = LawSearcher(
            index_path   = f"{DATA_DIR}/oic_law_faiss/oic_law_faiss.index",
            meta_path    = f"{DATA_DIR}/oic_law_faiss/oic_law_meta.pkl",
            snippet_path = f"{DATA_DIR}/oic_snippets.pkl",
            id_field     = "sysid",
            kw_fields    = ("title",),
        )
    return _oic

def court():
    global _court
    if _court is None:
        _court = LawSearcher(
            index_path   = f"{DATA_DIR}/supremecourt_faiss/supremecourt_faiss.index",
            meta_path    = f"{DATA_DIR}/supremecourt_faiss/supremecourt_meta.pkl",
            snippet_path = f"{DATA_DIR}/court_snippets.pkl",
            id_field     = "issueid",
            kw_fields    = ("category", "lawids"),
        )
    return _court

def civil():
    global _civil
    if _civil is None:
        _civil = LawSearcher(
            index_path   = f"{DATA_DIR}/civil_faiss/civil_faiss.index",
            meta_path    = f"{DATA_DIR}/civil_faiss/civil_meta.pkl",
            snippet_path = f"{DATA_DIR}/civil_snippets.pkl",
            id_field     = "sysid",
            kw_fields    = ("title",),
        )
    return _civil

def criminal():
    global _criminal
    if _criminal is None:
        _criminal = LawSearcher(
            index_path   = f"{DATA_DIR}/criminal_faiss/criminal_faiss.index",
            meta_path    = f"{DATA_DIR}/criminal_faiss/criminal_meta.pkl",
            snippet_path = f"{DATA_DIR}/criminal_snippets.pkl",
            id_field     = "sysid",
            kw_fields    = ("title",),
        )
    return _criminal


# ── FastAPI ──────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Legal RAG API", version="1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def warmup():
    for getter in [thai, oic, court, civil, criminal]:
        try: getter()
        except Exception as e: print(f"[warmup] {e}", flush=True)
    model()
    print("[legal-rag] warmup complete", flush=True)


_HTML = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
def root(): return FileResponse(f"{_HTML}/index.html")
@app.get("/search")
def search_page(): return FileResponse(f"{_HTML}/search.html")
@app.get("/analyze")
def analyze_page(): return FileResponse(f"{_HTML}/analyze.html")
@app.get("/draft")
def draft_page(): return FileResponse(f"{_HTML}/draft.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


@app.get("/api/explain")
def explain_endpoint(q: str = Query(..., min_length=1, max_length=300)):
    """Plain-Thai summary via MiniMax — no monetary amounts, CTA to consult lawyer."""
    ctx = []
    for label, getter in [("แพ่งและพาณิชย์", civil), ("อาญา", criminal),
                           ("กฎหมายอื่น", thai), ("กฎ คปภ.", oic), ("ฎีกา", court)]:
        try:
            for r in getter().search(q, top_k=2):
                title   = r.get("title") or f"คดี {r.get('issueid','')} ({r.get('year','')})"
                snippet = r.get("snippet", "")[:200]
                ctx.append(f"[{label}] {title}: {snippet}")
        except Exception:
            pass

    context_str = "\n".join(ctx) or "ไม่พบข้อมูลที่เกี่ยวข้อง"

    prompt = f"""คุณคือผู้ช่วยอธิบายกฎหมายไทย ให้ประชาชนทั่วไปเข้าใจง่าย

คำถาม: {q}

ข้อมูลกฎหมายที่พบ:
{context_str}

อธิบาย 3 ส่วนโดยสังเขป:
1. เรื่องนี้คืออะไร (ภาษาชาวบ้าน ไม่เกิน 2 ประโยค)
2. กฎหมายที่เกี่ยวข้อง (ชื่อ / มาตรา)
3. ข้อควรรู้สำคัญ (1-2 ข้อ)

กฎเหล็ก:
- ปิดท้ายด้วย: "หากต้องการคำปรึกษาเพิ่มเติม ติดต่อทนายความ TonyGroup ได้ทันที"
- ตอบภาษาไทย ชัดเจน ครอบคลุม"""

    try:
        msg = claude().messages.create(
            model="MiniMax-Text-01",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        explanation = msg.content[0].text
    except Exception as e:
        explanation = f"(ไม่สามารถโหลดคำอธิบายได้: {e})"

    return {"query": q, "explanation": explanation}


@app.get("/api/search")
def search_endpoint(
    q:  str = Query(..., min_length=1, max_length=300),
    db: str = Query("all", pattern="^(all|thai|oic|court|civil|criminal)$"),
    k:  int = Query(5, ge=1, le=10),
):
    results, errors = {}, {}
    for label, getter in [("thai", thai), ("oic", oic), ("court", court),
                           ("civil", civil), ("criminal", criminal)]:
        if db not in ("all", label):
            continue
        try:
            results[label] = getter().search(q, top_k=k)
        except Exception as e:
            errors[label] = str(e)
    return {"query": q, "results": results, "errors": errors}


# ── POST /api/analyze ────────────────────────────────────────────────────────
from pydantic import BaseModel
from typing import List, Optional

class AnalyzeRequest(BaseModel):
    facts: str
    question: str
    format: str = "memo"          # memo | timeline | compare
    categories: List[str] = []

@app.post("/api/analyze")
@limiter.limit("20/minute")
def analyze_endpoint(request: Request, body: AnalyzeRequest):
    """Structured legal memo via MiniMax — searches all 3 DBs, no word cap."""
    ctx_lines = []
    for label, getter in [("แพ่งและพาณิชย์", civil), ("อาญา", criminal),
                           ("กฎหมายอื่น", thai), ("กฎ คปภ.", oic), ("ฎีกา", court)]:
        try:
            for r in getter().search(body.question, top_k=3):
                title   = r.get("title") or f"คดี {r.get('issueid','')} ({r.get('year','')})"
                snippet = r.get("snippet", "")[:300]
                ctx_lines.append(f"[{label}] {title}: {snippet}")
        except Exception:
            pass

    context_str = "\n".join(ctx_lines) or "ไม่พบข้อมูลที่เกี่ยวข้อง"

    fmt = body.format.lower()
    if fmt == "timeline":
        structure_instruction = "เรียงลำดับเหตุการณ์ตามเวลา (chronological) โดยใช้หัวข้อ:\n## ลำดับเหตุการณ์\n## บทบัญญัติที่เกี่ยวข้อง\n## ข้อสรุป"
    elif fmt == "compare":
        structure_instruction = "เปรียบเทียบแนวคำพิพากษาโดยใช้หัวข้อ:\n## ประเด็นเปรียบเทียบ\n## แนวคำพิพากษาฝ่าย A\n## แนวคำพิพากษาฝ่าย B\n## ข้อสรุป"
    else:  # memo
        structure_instruction = "ตอบเป็น structured Thai legal memo โดยใช้หัวข้อ:\n## ประเด็นกฎหมาย\n## บทบัญญัติที่เกี่ยวข้อง\n## แนวคำพิพากษา\n## ข้อสรุป"

    prompt = f"""คุณคือนักกฎหมายไทยผู้เชี่ยวชาญ วิเคราะห์ข้อเท็จจริงและตอบคำถามกฎหมายอย่างครบถ้วน

ข้อเท็จจริง:
{body.facts}

คำถาม: {body.question}

ข้อมูลกฎหมายอ้างอิง:
{context_str}

{structure_instruction}

กฎ:
- ตอบภาษาไทย ละเอียด ครบถ้วน
- ระบุจำนวนเงิน ค่าปรับ หรือโทษได้ตามที่กฎหมายกำหนด
- อ้างอิงมาตรา / ฎีกาให้ชัดเจน"""

    try:
        msg = claude().messages.create(
            model="MiniMax-Text-01",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        memo = msg.content[0].text
    except Exception as e:
        return {"error": str(e)}

    return {"memo": memo}


# ── POST /api/draft ──────────────────────────────────────────────────────────
DRAFT_TEMPLATES = {
    "sale_contract": "ร่างสัญญาซื้อขายทรัพย์สินฉบับสมบูรณ์ตามกฎหมายไทย ระบุคู่สัญญา วัตถุแห่งสัญญา ราคา เงื่อนไข และการส่งมอบ",
    "lease_contract": "ร่างสัญญาเช่าทรัพย์สินฉบับสมบูรณ์ตามกฎหมายไทย ระบุคู่สัญญา ทรัพย์ที่เช่า ค่าเช่า ระยะเวลา และเงื่อนไข",
    "loan_contract": "ร่างสัญญากู้ยืมเงินฉบับสมบูรณ์ตามกฎหมายไทย ระบุผู้กู้ ผู้ให้กู้ จำนวนเงิน ดอกเบี้ย กำหนดชำระ และหลักประกัน",
    "power_of_attorney": "ร่างหนังสือมอบอำนาจฉบับสมบูรณ์ตามกฎหมายไทย ระบุผู้มอบ ผู้รับมอบ และขอบเขตอำนาจที่มอบ",
    "notice_letter": "ร่างหนังสือบอกกล่าว/โนติสฉบับสมบูรณ์ตามกฎหมายไทย ระบุผู้ส่ง ผู้รับ ประเด็นที่บอกกล่าว และระยะเวลาที่กำหนด",
    "complaint_letter": "ร่างคำร้องทุกข์ฉบับสมบูรณ์ตามกฎหมายไทย ระบุผู้ร้อง ผู้ถูกร้อง เหตุการณ์ และสิ่งที่ขอให้ดำเนินการ",
    "employment_contract": "ร่างสัญญาจ้างงานฉบับสมบูรณ์ตามกฎหมายแรงงานไทย ระบุนายจ้าง ลูกจ้าง ตำแหน่ง ค่าจ้าง เวลาทำงาน และสิทธิประโยชน์",
}

class DraftRequest(BaseModel):
    template_id: str
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    amount: Optional[str] = None
    # allow extra fields via model_config
    model_config = {"extra": "allow"}

@app.post("/api/draft")
@limiter.limit("20/minute")
def draft_endpoint(request: Request, body: DraftRequest):
    """Generate full Thai legal document from template + fields."""
    template_desc = DRAFT_TEMPLATES.get(body.template_id)
    if not template_desc:
        return {"error": f"ไม่รู้จัก template_id '{body.template_id}'. รองรับ: {', '.join(DRAFT_TEMPLATES)}"}

    # Collect all provided fields
    fields_dict = body.model_dump(exclude_none=True)
    fields_text = "\n".join(f"- {k}: {v}" for k, v in fields_dict.items() if k != "template_id")

    prompt = f"""คุณคือทนายความไทยผู้เชี่ยวชาญ กรุณา{template_desc}

ข้อมูลที่ได้รับ:
{fields_text}

คำสั่ง:
- ร่างเอกสารฉบับสมบูรณ์ ถูกต้องตามรูปแบบกฎหมายไทย
- ใช้ภาษากฎหมายที่เป็นทางการ
- ใส่ช่องลงนาม วันที่ และพยานตามที่เหมาะสม
- ถ้าข้อมูลใดไม่ครบ ให้ใส่ [...] ไว้แทน"""

    try:
        msg = claude().messages.create(
            model="MiniMax-Text-01",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        document = msg.content[0].text
    except Exception as e:
        return {"error": str(e)}

    return {"document": document}



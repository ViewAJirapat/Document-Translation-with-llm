"""Web app: upload a PDF, follow and review the translation side by side, export DOCX/PDF."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, export, translate, worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
APP_DIR = Path(__file__).parent
ACTIVE = {"queued", "extracting", "translating"}
STATUS_LABELS = {
    "queued": "Queued", "extracting": "Reading PDF", "translating": "Translating",
    "done": "Done", "failed": "Failed", "cancelled": "Cancelled",
}


@asynccontextmanager
async def lifespan(_app):
    db.init()
    worker.start()
    yield


app = FastAPI(title="PDF → Thai", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["ACTIVE"] = ACTIVE


def _job_or_404(job_id):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _back(job_id):
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# --- pages --------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "index.html", {
        "jobs": db.list_jobs(), "error": error, "max_mb": config.MAX_UPLOAD_MB,
    })


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = _job_or_404(job_id)
    return templates.TemplateResponse(request, "job.html", {
        "job": job, "blocks": db.get_blocks(job_id), "progress": db.progress(job_id), "now": time.time(),
    })


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: bool = False):
    return templates.TemplateResponse(request, "settings.html", {
        "glossary": db.get_setting("glossary"), "extra_rules": db.get_setting("extra_rules"),
        "llama_url": config.LLAMA_URL, "has_key": bool(config.LLAMA_API_KEY), "saved": saved,
        "base_rules": translate.BASE_RULES,
    })


@app.post("/settings")
def save_settings(glossary: str = Form(""), extra_rules: str = Form("")):
    db.set_setting("glossary", glossary.strip())
    db.set_setting("extra_rules", extra_rules.strip())
    return RedirectResponse("/settings?saved=1", status_code=303)


# --- job actions --------------------------------------------------------------------------

@app.post("/jobs")
async def upload(file: UploadFile, force_ocr: bool = Form(False)):
    head = await file.read(5)
    if head != b"%PDF-":
        return RedirectResponse("/?error=" + quote("That file is not a PDF."), status_code=303)
    # Write to a temporary name first so the worker never sees a half-written upload.
    tmp = config.UPLOAD_DIR / f"tmp-{uuid.uuid4().hex}.pdf"
    size, limit = len(head), config.MAX_UPLOAD_MB * 1024 * 1024
    try:
        with open(tmp, "wb") as f:
            f.write(head)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"The PDF is larger than {config.MAX_UPLOAD_MB} MB.")
                f.write(chunk)
    except ValueError as e:
        tmp.unlink(missing_ok=True)
        return RedirectResponse("/?error=" + quote(str(e)), status_code=303)
    job_id = db.create_job(Path(file.filename or "document.pdf").name, force_ocr)
    tmp.rename(db.upload_path(job_id))
    worker.notify()
    return _back(job_id)


@app.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    if _job_or_404(job_id)["status"] in ACTIVE:
        db.update_job(job_id, status="cancelled")
    return _back(job_id)


@app.post("/jobs/{job_id}/resume")
def resume(job_id: str):
    job = _job_or_404(job_id)
    if job["status"] not in ACTIVE:
        db.update_job(job_id, status="translating" if db.get_blocks(job_id) else "queued", error=None)
        worker.notify()
    return _back(job_id)


@app.post("/jobs/{job_id}/retranslate")
def retranslate_all(job_id: str):
    job = _job_or_404(job_id)
    if job["status"] not in ACTIVE:
        db.reset_translations(job_id)
        db.update_job(job_id, status="translating", error=None)
        worker.notify()
    return _back(job_id)


@app.post("/jobs/{job_id}/delete")
def delete(job_id: str):
    _job_or_404(job_id)
    db.delete_job(job_id)
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}/export.{fmt}")
def export_file(job_id: str, fmt: str):
    job = _job_or_404(job_id)
    title = Path(job["filename"]).stem
    blocks = db.get_blocks(job_id)
    if fmt == "docx":
        data = export.build_docx(blocks, title)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "pdf":
        data = export.build_pdf(blocks, title)
        media = "application/pdf"
    else:
        raise HTTPException(404, "Unknown export format")
    name = f"{title}_th.{fmt}"
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(data, media_type=media, headers={"Content-Disposition": disposition})


# --- JSON API (used by the page scripts) --------------------------------------------------

@app.get("/api/server")
def api_server():
    return translate.server_status()


@app.get("/api/jobs")
def api_jobs():
    return [
        {"id": j["id"], "status": j["status"], "label": STATUS_LABELS[j["status"]], "error": j["error"],
         "pages": j["pages"], "done": j["done"], "total": j["total"]}
        for j in db.list_jobs()
    ]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, since: float = 0):
    now = time.time()
    job = _job_or_404(job_id)
    return {
        "status": job["status"], "label": STATUS_LABELS[job["status"]], "error": job["error"],
        "pages": job["pages"], "model": job["model"], **db.progress(job_id), "now": now,
        "blocks": [dict(b) for b in db.blocks_since(job_id, since)],
    }


@app.post("/api/blocks/{block_id}")
def api_save_block(block_id: int, fields: dict = Body(...)):
    if db.get_block(block_id) is None:
        raise HTTPException(404, "Block not found")
    changes = {k: str(v) for k, v in fields.items() if k in ("source", "translation")}
    if "translation" in changes:
        changes["status"] = "done"  # a hand-written translation counts as done
    if changes:
        db.update_block(block_id, **changes)
    return {"ok": True}


@app.post("/api/blocks/{block_id}/retranslate")
def api_retranslate_block(block_id: int):
    block = db.get_block(block_id)
    if block is None:
        raise HTTPException(404, "Block not found")
    try:
        text = translate.translate_block(
            block["source"], block["kind"],
            translate.parse_glossary(db.get_setting("glossary")),
            db.get_setting("extra_rules").splitlines(),
        )
    except translate.LlamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    db.update_block(block_id, translation=text, status="done")
    return {"translation": text}


@app.get("/health")
def health():
    return {"ok": True}

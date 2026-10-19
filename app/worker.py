"""Background worker: one job at a time, since there is one llama-server (and one GPU) behind it.

Job flow: queued -> extracting -> translating -> done. Each translated block is saved as soon
as it arrives, so a restart or a lost llama-server connection resumes where it stopped.
"""
import logging
import threading

from . import db, translate

log = logging.getLogger(__name__)
_wake = threading.Event()


def notify():
    """Wake the worker now instead of at its next poll."""
    _wake.set()


def start():
    threading.Thread(target=_loop, name="worker", daemon=True).start()


def _loop():
    while True:
        job = db.next_job()
        if job is None:
            _wake.wait(timeout=5)
            _wake.clear()
            continue
        try:
            _process(job)
        except Exception as e:  # noqa: BLE001 - any failure must end up on the job, not kill the thread
            log.exception("job %s failed", job["id"])
            db.update_job(job["id"], status="failed", error=f"{type(e).__name__}: {e}")


def _process(job):
    job_id = job["id"]
    if job["status"] == "queued":
        from . import extract  # imported lazily: loading docling takes several seconds

        db.update_job(job_id, status="extracting", error=None)
        blocks, pages = extract.extract(db.upload_path(job_id), force_ocr=bool(job["force_ocr"]))
        if not blocks:
            raise RuntimeError("No text was found in this PDF, even with OCR.")
        current = db.get_job(job_id)
        if current is None:
            return  # deleted while extracting
        db.replace_blocks(job_id, blocks)
        if current["status"] != "extracting":
            db.update_job(job_id, pages=pages)  # cancelled while extracting; keep the blocks for a resume
            return
        db.update_job(job_id, status="translating", pages=pages)

    status = translate.server_status()
    if not status["ok"]:
        raise translate.LlamaError(status["error"])
    db.update_job(job_id, model=status["model"], error=None)

    glossary = translate.parse_glossary(db.get_setting("glossary"))
    extra_rules = db.get_setting("extra_rules").splitlines()
    while (block := db.next_pending_block(job_id)) is not None:
        current = db.get_job(job_id)
        if current is None or current["status"] != "translating":
            return  # deleted or cancelled from the UI
        text = translate.translate_block(block["source"], block["kind"], glossary, extra_rules)
        db.update_block(block["id"], translation=text, status="done")
    db.update_job(job_id, status="done")

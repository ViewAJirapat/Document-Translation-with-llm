import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

import app as app_pkg
from app import db, translate

FAKE_BLOCKS = [
    {"kind": "heading", "level": 1, "text": "Results", "page": 1},
    {"kind": "paragraph", "text": "The method works.", "page": 1},
    {"kind": "code", "text": "x = 1", "page": 2, "verbatim": True},
]


@pytest.fixture(scope="module")
def client():
    # Stand-ins for docling and llama-server, so this tests the web app and worker only.
    fake_extract = types.ModuleType("app.extract")
    fake_extract.extract = lambda path, force_ocr=False: (FAKE_BLOCKS, 2)
    mp = pytest.MonkeyPatch()
    mp.setitem(sys.modules, "app.extract", fake_extract)
    mp.setattr(app_pkg, "extract", fake_extract, raising=False)
    mp.setattr(translate, "server_status", lambda: {"ok": True, "model": "fake.gguf"})
    mp.setattr(translate, "translate_block", lambda text, kind="paragraph", g=(), r=(): f"TH[{text}]")
    from app.main import app

    with TestClient(app) as c:
        yield c
    mp.undo()


def wait_for(client, job_id, statuses, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.2)
    raise AssertionError(f"job stuck in {job['status']}")


def upload(client, data=b"%PDF-1.4 fake", name="report.pdf"):
    return client.post("/jobs", files={"file": (name, data, "application/pdf")}, follow_redirects=False)


def test_rejects_files_that_are_not_pdf(client):
    r = upload(client, b"hello", "notes.txt")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?error=")


def test_full_job_flow(client):
    r = upload(client)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[1]

    job = wait_for(client, job_id, {"done", "failed"})
    assert job["status"] == "done", job["error"]
    assert (job["done"], job["total"], job["pages"]) == (2, 2, 2)

    blocks = db.get_blocks(job_id)
    assert [b["translation"] for b in blocks] == ["TH[Results]", "TH[The method works.]", None]
    assert blocks[2]["status"] == "skip"

    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200 and "TH[The method works.]" in page.text

    # Hand edit, then exports.
    assert client.post(f"/api/blocks/{blocks[1]['id']}", json={"translation": "วิธีนี้ได้ผล"}).json() == {"ok": True}
    assert db.get_block(blocks[1]["id"])["translation"] == "วิธีนี้ได้ผล"

    docx = client.get(f"/jobs/{job_id}/export.docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert "report_th.docx" in docx.headers["content-disposition"]
    pdf = client.get(f"/jobs/{job_id}/export.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_retranslate_all_and_delete(client):
    job_id = upload(client).headers["location"].rsplit("/", 1)[1]
    wait_for(client, job_id, {"done"})
    client.post(f"/jobs/{job_id}/retranslate", follow_redirects=False)
    assert wait_for(client, job_id, {"done"})["done"] == 2

    assert client.post(f"/jobs/{job_id}/delete", follow_redirects=False).status_code == 303
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert not db.upload_path(job_id).exists()


def test_pages_render(client):
    assert client.get("/").status_code == 200
    assert client.post("/settings", data={"glossary": "dataset = ชุดข้อมูล", "extra_rules": ""},
                       follow_redirects=False).status_code == 303
    assert db.get_setting("glossary") == "dataset = ชุดข้อมูล"
    assert "ชุดข้อมูล" in client.get("/settings").text

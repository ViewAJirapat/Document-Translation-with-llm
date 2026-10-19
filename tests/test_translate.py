import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import config, tables, translate


# --- prompt and text handling -------------------------------------------------------------

def test_parse_glossary_handles_comments_and_keep_english():
    text = "# comment\nmachine learning = การเรียนรู้ของเครื่อง\noverfitting =\nno equals sign\n = orphan"
    assert translate.parse_glossary(text) == [
        ("machine learning", "การเรียนรู้ของเครื่อง"),
        ("overfitting", ""),
    ]


def test_prompt_only_includes_glossary_terms_present_in_text():
    glossary = [("dataset", "ชุดข้อมูล"), ("overfitting", ""), ("compiler", "คอมไพเลอร์")]
    prompt = translate.build_prompt("The Dataset shows overfitting.", "paragraph", glossary, ["Be brief."])
    assert 'Translate "dataset" as "ชุดข้อมูล".' in prompt
    assert 'Keep the term "overfitting" in English.' in prompt
    assert "compiler" not in prompt
    assert "Be brief." in prompt
    assert prompt.endswith("Source Text:\nThe Dataset shows overfitting.")


def test_table_prompt_adds_table_rule():
    assert translate.TABLE_RULE in translate.build_prompt("| a |\n|---|\n| b |", "table")
    assert translate.TABLE_RULE not in translate.build_prompt("plain", "paragraph")


def test_split_text_keeps_every_word_and_respects_limit():
    text = " ".join(f"Sentence number {i} has some words." for i in range(300))
    parts = translate.split_text(text, max_words=100)
    assert len(parts) > 1
    assert all(len(p.split()) <= 100 for p in parts)
    assert " ".join(parts).split() == text.split()


def test_split_text_cuts_a_single_giant_sentence():
    text = " ".join(["word"] * 250)
    parts = translate.split_text(text, max_words=100)
    assert [len(p.split()) for p in parts] == [100, 100, 50]


def test_table_split_repeats_header_and_roundtrips():
    rows = [["Item", "Notes"]] + [[f"item {i}", "some longer note text here"] for i in range(40)]
    md = tables.render(rows)
    chunks = tables.split(md, max_words=50)
    assert len(chunks) > 1
    assert all(tables.parse(c)[0] == ["Item", "Notes"] for c in chunks)
    body = [r for c in chunks for r in tables.parse(c)[1:]]
    assert body == rows[1:]


# --- llama-server client against a fake server --------------------------------------------

class FakeLlama:
    """Minimal stand-in for llama-server: replies from a queue and records requests."""

    def __init__(self):
        self.replies = []  # (status, body dict or str)
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, body):
                data = (json.dumps(body) if isinstance(body, dict) else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append(("GET", self.path, dict(self.headers), None))
                self._reply(*fake.replies.pop(0))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(("POST", self.path, dict(self.headers), body))
                self._reply(*fake.replies.pop(0))

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def chat(self, content):
        return (200, {"choices": [{"message": {"content": content}}]})


@pytest.fixture
def llama(monkeypatch):
    fake = FakeLlama()
    monkeypatch.setattr(config, "LLAMA_URL", fake.url)
    monkeypatch.setattr(config, "LLAMA_API_KEY", "secret-key")
    monkeypatch.setattr(translate.time, "sleep", lambda s: None)
    yield fake
    fake.server.shutdown()


def test_complete_sends_api_key_and_strips_think_block(llama):
    llama.replies.append(llama.chat("<think>hmm</think>\nสวัสดี"))
    assert translate.complete("prompt", 100) == "สวัสดี"
    method, path, headers, body = llama.requests[0]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert headers["Authorization"] == "Bearer secret-key"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_complete_retries_server_errors(llama):
    llama.replies += [(500, "boom"), (503, "busy"), llama.chat("ได้แล้ว")]
    assert translate.complete("prompt", 100) == "ได้แล้ว"
    assert len(llama.requests) == 3


def test_complete_gives_up_after_three_server_errors(llama):
    llama.replies += [(500, "boom")] * 3
    with pytest.raises(translate.LlamaError, match="after 3 attempts"):
        translate.complete("prompt", 100)


def test_complete_does_not_retry_bad_api_key(llama):
    llama.replies.append((401, {"error": "invalid key"}))
    with pytest.raises(translate.LlamaError, match="API key"):
        translate.complete("prompt", 100)
    assert len(llama.requests) == 1


def test_unreachable_server_raises_llama_error(monkeypatch):
    monkeypatch.setattr(config, "LLAMA_URL", "http://127.0.0.1:9")  # nothing listens on the discard port
    monkeypatch.setattr(translate.time, "sleep", lambda s: None)
    with pytest.raises(translate.LlamaError):
        translate.complete("prompt", 100)
    assert translate.server_status()["ok"] is False


def test_server_status_reports_model_file_name(llama):
    llama.replies.append((200, {"data": [{"id": "C:\\models\\typhoon-translate1.5-4b.Q8_0.gguf"}]}))
    assert translate.server_status() == {"ok": True, "model": "typhoon-translate1.5-4b.Q8_0.gguf"}


def test_table_translation_falls_back_to_cells_when_model_breaks_structure(llama):
    md = "| Item | Qty |\n|---|---|\n| Mouse | 5 |"
    llama.replies += [llama.chat("ตาราง: เมาส์ 5 ชิ้น"), llama.chat("รายการ"), llama.chat("จำนวน"), llama.chat("เมาส์")]
    out = translate.translate_block(md, "table")
    assert tables.parse(out) == [["รายการ", "จำนวน"], ["เมาส์", "5"]]
    assert len(llama.requests) == 4  # whole table once, then 3 text cells ("5" is left alone)


def test_table_translation_keeps_model_output_when_shape_matches(llama):
    md = "| Item | Qty |\n|---|---|\n| Mouse | 5 |"
    llama.replies.append(llama.chat("| รายการ | จำนวน |\n|---|---|\n| เมาส์ | 5 |"))
    assert tables.parse(translate.translate_block(md, "table")) == [["รายการ", "จำนวน"], ["เมาส์", "5"]]
    assert len(llama.requests) == 1

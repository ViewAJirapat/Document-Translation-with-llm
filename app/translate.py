"""Prompt building and the llama-server client (OpenAI-compatible /v1/chat/completions)."""
import re
import time

import httpx

from . import config, tables

# Prompt format from the Typhoon Translate model card. Rule 1 matters: without it the model
# leaves most technical terms in English (see bench/ results).
PROMPT = (
    "Translate the following English text into Thai, strictly following the rules below "
    "and return only the translated text.\nRules:\n{rules}\nSource Text:\n{text}"
)
BASE_RULES = [
    "Translate technical terms into Thai when a common Thai term exists; you may add the English term in parentheses on first use.",
    "Keep numbers, people's names, URLs, code and Markdown formatting (headings, lists, tables, bold) unchanged.",
    "Use natural, formal written Thai.",
]
TABLE_RULE = "The text is a Markdown table: keep every row, column and | separator exactly; translate only the text inside the cells."

# A chunk this size (~800 prompt tokens) plus its Thai output fits comfortably in an 8k context.
MAX_WORDS = 600
THINK = re.compile(r"<think>.*?</think>\s*", re.S)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
HAS_LETTERS = re.compile(r"[A-Za-z]")


class LlamaError(Exception):
    """llama-server is unreachable or refused the request; the job should stop, not skip blocks."""


def parse_glossary(text):
    """Lines of 'English = Thai'. An empty or identical right side means 'keep in English'."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        en, th = (part.strip() for part in line.split("=", 1))
        if en:
            entries.append((en, th))
    return entries


def build_prompt(text, kind="paragraph", glossary=(), extra_rules=()):
    rules = list(BASE_RULES)
    if kind == "table":
        rules.append(TABLE_RULE)
    lower = text.lower()
    for en, th in glossary:
        # Only terms that occur in this chunk, so long glossaries don't crowd the prompt.
        if en.lower() in lower:
            rules.append(f'Keep the term "{en}" in English.' if not th or th == en else f'Translate "{en}" as "{th}".')
    rules += [r.strip() for r in extra_rules if r.strip()]
    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
    return PROMPT.format(rules=numbered, text=text)


def split_text(text, max_words=MAX_WORDS):
    """Split on sentence boundaries into chunks of at most max_words (a single huge sentence is cut by words)."""
    if len(text.split()) <= max_words:
        return [text]
    chunks, current = [], []
    for sentence in SENTENCE_END.split(text):
        words = sentence.split()
        while len(words) > max_words:
            if current:
                chunks.append(" ".join(current))
                current = []
            chunks.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if current and len(current) + len(words) > max_words:
            chunks.append(" ".join(current))
            current = []
        current += words
    if current:
        chunks.append(" ".join(current))
    return chunks


def _headers():
    headers = {"Content-Type": "application/json"}
    if config.LLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLAMA_API_KEY}"
    return headers


def server_status():
    """{'ok': True, 'model': name} or {'ok': False, 'error': message}; used by the UI and the worker."""
    try:
        r = httpx.get(f"{config.LLAMA_URL}/v1/models", headers=_headers(), timeout=5)
        if r.status_code == 401:
            return {"ok": False, "error": "llama-server rejected the API key (401). Check LLAMA_API_KEY."}
        r.raise_for_status()
        models = r.json().get("data") or []
        name = models[0]["id"] if models else "unknown model"
        return {"ok": True, "model": re.split(r"[\\/]", name)[-1]}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return {"ok": False, "error": f"Cannot reach llama-server at {config.LLAMA_URL}: {e}"}


def complete(prompt, max_tokens, attempts=3):
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    last_error = None
    for attempt in range(attempts):
        try:
            r = httpx.post(f"{config.LLAMA_URL}/v1/chat/completions", json=payload,
                           headers=_headers(), timeout=config.LLAMA_TIMEOUT)
        except httpx.TransportError as e:
            last_error = e
        else:
            if r.status_code == 401:
                raise LlamaError("llama-server rejected the API key (401). Check LLAMA_API_KEY.")
            if r.status_code < 500:
                if r.status_code >= 400:
                    raise LlamaError(f"llama-server returned {r.status_code}: {r.text[:300]}")
                return THINK.sub("", r.json()["choices"][0]["message"]["content"]).strip()
            last_error = f"HTTP {r.status_code}: {r.text[:300]}"
        if attempt < attempts - 1:
            time.sleep(2 * 2 ** attempt)
    raise LlamaError(f"llama-server request failed after {attempts} attempts: {last_error}")


def _translate_chunk(text, kind, glossary, extra_rules):
    # Thai output runs ~3 tokens per English word with Typhoon Translate; leave generous headroom.
    max_tokens = min(4096, max(256, len(text.split()) * 6))
    return complete(build_prompt(text, kind, glossary, extra_rules), max_tokens)


def translate_table(md, glossary=(), extra_rules=()):
    out_rows = []
    for i, chunk in enumerate(tables.split(md, MAX_WORDS)):
        src = tables.parse(chunk)
        got = tables.parse(_translate_chunk(chunk, "table", glossary, extra_rules))
        if tables.shape(got) != tables.shape(src):
            # The model broke the table structure: fall back to translating cell by cell.
            cache = {}
            got = []
            for row in src:
                new_row = []
                for cell in row:
                    if HAS_LETTERS.search(cell):
                        if cell not in cache:
                            cache[cell] = _translate_chunk(cell, "paragraph", glossary, extra_rules)
                        new_row.append(cache[cell].replace("|", "/").replace("\n", " "))
                    else:
                        new_row.append(cell)
                got.append(new_row)
        out_rows += got if i == 0 else got[1:]  # each chunk repeats the header row
    return tables.render(out_rows)


def translate_block(text, kind="paragraph", glossary=(), extra_rules=()):
    if kind == "table":
        return translate_table(text, glossary, extra_rules)
    return " ".join(_translate_chunk(part, kind, glossary, extra_rules) for part in split_text(text))

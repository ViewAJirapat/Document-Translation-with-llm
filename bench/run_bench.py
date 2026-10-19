"""Benchmark English -> Thai translation models through llama-server.

Usage (from the project root):
  python bench/run_bench.py                      # every model in models.json, then the report
  python bench/run_bench.py --models translate-4b
  python bench/run_bench.py --report-only        # rebuild bench/report.html from saved results

Only the standard library is used. Models whose GGUF is not in models/ yet are skipped.
"""
import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
RESULTS = BENCH / "results"
LOGS = BENCH / "logs"
MODELS_DIR = ROOT / "models"
LLAMA_SERVER = ROOT / "tools" / "llama.cpp" / "llama-server.exe"
PORT = 8090
BASE_URL = f"http://127.0.0.1:{PORT}"
CTX = 8192

# Prompt format recommended on the Typhoon Translate model card; used for every model so the
# comparison is fair.
PROMPT = (
    "Translate the following English text into Thai, strictly following the rules below "
    "and return only the translated text.\nRules:\n{rules}\nSource Text:\n{text}"
)
# Without rule 1, Translate 4B leaves most technical terms in English (abstract: 40-58% Thai).
BASE_RULES = [
    "Translate technical terms into Thai when a common Thai term exists; you may add the English term in parentheses on first use.",
    "Keep numbers, people's names, URLs, code and Markdown formatting (headings, lists, tables, bold) unchanged.",
    "Use natural, formal written Thai.",
]

THAI = re.compile(r"[฀-๿]")
LATIN = re.compile(r"[A-Za-z]")
CJK = re.compile(r"[぀-ヿ㐀-鿿]")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
PREAMBLE = re.compile(r"^\s*(here|sure|translation|thai translation|คำแปล|แปลว่า)", re.I)
THINK = re.compile(r"<think>.*?</think>\s*", re.S)
MARKDOWN = {"table rows": r"^\s*\|", "numbered items": r"^\s*\d+\.\s", "headings": r"^\s*#+\s"}


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def gpu_mem_used_mb():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip().splitlines()[0])


def http_json(url, payload=None, timeout=600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_server(model_path, log_path):
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(LLAMA_SERVER), "-m", str(model_path), "-ngl", "99", "-c", str(CTX), "-np", "1",
         "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=log, stderr=subprocess.STDOUT,
    )
    start = time.time()
    while time.time() - start < 300:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited early, see {log_path}")
        try:
            if http_json(f"{BASE_URL}/health", timeout=5).get("status") == "ok":
                return proc, time.time() - start
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    proc.kill()
    raise RuntimeError(f"llama-server did not become ready, see {log_path}")


def stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def translate(item):
    rules = BASE_RULES + item.get("rules", [])
    prompt = PROMPT.format(rules="\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1)), text=item["text"])
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "seed": 42,
        "max_tokens": 3072,
        # Qwen3-based models otherwise may spend tokens "thinking" first.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    t0 = time.time()
    resp = http_json(f"{BASE_URL}/v1/chat/completions", payload)
    return resp["choices"][0]["message"]["content"], resp.get("timings", {}), time.time() - t0


def check(item, raw):
    flags = []
    if "<think>" in raw:
        flags.append("thinking tags")
    out = THINK.sub("", raw).strip()
    if PREAMBLE.search(out):
        flags.append("preamble")
    if CJK.search(out):
        flags.append("CJK characters: " + "".join(CJK.findall(out)))
    missing = sorted(n for n in set(NUMBER.findall(item["text"])) if n not in out)
    if missing:
        flags.append("numbers missing: " + ", ".join(missing))
    for term in item.get("expect", []):
        if term.lower() not in out.lower():
            flags.append(f"glossary miss: {term}")
    for name, pattern in MARKDOWN.items():
        src_n = len(re.findall(pattern, item["text"], re.M))
        out_n = len(re.findall(pattern, out, re.M))
        if src_n and src_n != out_n:
            flags.append(f"{name}: {out_n}/{src_n}")
    thai, latin = len(THAI.findall(out)), len(LATIN.findall(out))
    thai_ratio = thai / max(1, thai + latin)
    if thai_ratio < 0.6:
        flags.append(f"low Thai ratio {thai_ratio:.0%}")
    if len(item["text"]) >= 60:
        length_ratio = len(out) / len(item["text"])
        if not 0.5 <= length_ratio <= 2.0:
            flags.append(f"length ratio {length_ratio:.2f}")
    return out, flags, thai_ratio


def run_model(model, items):
    model_path = MODELS_DIR / model["file"]
    if not model_path.exists():
        print(f"skip {model['id']}: {model_path.name} not downloaded yet")
        return
    RESULTS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    print(f"\n== {model['name']}")
    baseline_mb = gpu_mem_used_mb()
    proc, load_s = start_server(model_path, LOGS / f"{model['id']}.log")
    try:
        vram_mb = gpu_mem_used_mb() - baseline_mb
        print(f"   loaded in {load_s:.1f}s, VRAM +{vram_mb} MB")
        results = []
        for item in items:
            raw, timings, wall_s = translate(item)
            out, flags, thai_ratio = check(item, raw)
            results.append({"id": item["id"], "output": out, "raw": raw, "timings": timings,
                            "wall_s": round(wall_s, 2), "flags": flags, "thai_ratio": round(thai_ratio, 3)})
            print(f"   {item['id']:<13} {timings.get('predicted_n', 0):>5} tok "
                  f"{timings.get('predicted_per_second', 0):6.1f} tok/s  {wall_s:5.1f}s  {'; '.join(flags)}")
    finally:
        stop_server(proc)
    record = {"model": model, "vram_mb": vram_mb, "load_s": round(load_s, 1), "items": results}
    (RESULTS / f"{model['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(record, items):
    words = sum(len(i["text"].split()) for i in items)
    pred_n = sum(r["timings"].get("predicted_n", 0) for r in record["items"])
    pred_ms = sum(r["timings"].get("predicted_ms", 0) for r in record["items"])
    prompt_n = sum(r["timings"].get("prompt_n", 0) for r in record["items"])
    prompt_ms = sum(r["timings"].get("prompt_ms", 0) for r in record["items"])
    wall = sum(r["wall_s"] for r in record["items"])
    gen_tps = pred_n / (pred_ms / 1000) if pred_ms else 0
    return {
        "gen_tps": gen_tps,
        "prompt_tps": prompt_n / (prompt_ms / 1000) if prompt_ms else 0,
        "tok_per_word": pred_n / words if words else 0,
        # Rough real-world estimate: output generation dominates, so scale wall time by word count.
        "min_per_10k_words": wall / words * 10_000 / 60 if words else 0,
        "flags": sum(len(r["flags"]) for r in record["items"]),
        "wall": wall,
    }


def build_report(items):
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS.glob("*.json"))]
    if not records:
        print("no results yet")
        return
    order = {m["id"]: i for i, m in enumerate(json.loads((BENCH / "models.json").read_text(encoding="utf-8")))}
    records.sort(key=lambda r: order.get(r["model"]["id"], 99))
    # Re-run the checks so the report reflects the current heuristics, not those at run time.
    by_id = {i["id"]: i for i in items}
    for r in records:
        for res in r["items"]:
            if res["id"] in by_id:
                res["output"], res["flags"], res["thai_ratio"] = check(by_id[res["id"]], res["raw"])
    e = html.escape

    rows = []
    for r in records:
        s = summarize(r, items)
        rows.append(
            f"<tr><td>{e(r['model']['name'])}</td><td>{r['vram_mb'] / 1024:.1f} GB</td>"
            f"<td>{s['gen_tps']:.1f}</td><td>{s['prompt_tps']:.0f}</td><td>{s['tok_per_word']:.2f}</td>"
            f"<td>{s['wall']:.0f} s</td><td>{s['min_per_10k_words']:.0f} min</td><td>{s['flags']}</td></tr>"
        )

    sections = []
    for item in items:
        cells = [f"<div class='cell src'><h4>English source</h4><pre>{e(item['text'])}</pre></div>"]
        for r in records:
            res = next((x for x in r["items"] if x["id"] == item["id"]), None)
            if res is None:
                continue
            badges = "".join(f"<span class='flag'>{e(f)}</span>" for f in res["flags"]) or "<span class='ok'>no flags</span>"
            t = res["timings"]
            cells.append(
                f"<div class='cell'><h4>{e(r['model']['name'])}</h4>"
                f"<div class='meta'>{t.get('predicted_n', 0)} tok · {t.get('predicted_per_second', 0):.1f} tok/s · {res['wall_s']} s</div>"
                f"<div class='badges'>{badges}</div><pre>{e(res['output'])}</pre></div>"
            )
        rules = "".join(f"<li>{e(x)}</li>" for x in item.get("rules", []))
        rules_html = f"<ul class='rules'>{rules}</ul>" if rules else ""
        sections.append(
            f"<section><h3>{e(item['kind'])} <code>{e(item['id'])}</code></h3>{rules_html}"
            f"<div class='grid' style='--cols:{len(cells)}'>{''.join(cells)}</div></section>"
        )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thai Model Benchmark</title>
<style>
:root {{ --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --card:#fff; --flag-bg:#fef3c7; --flag-fg:#92400e; --ok:#15803d; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --line:#44403c; --card:#292524; --flag-bg:#78350f; --flag-fg:#fef3c7; --ok:#4ade80; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.6 "Sarabun","Leelawadee UI","Noto Sans Thai",Tahoma,sans-serif; }}
main {{ max-width:1400px; margin:0 auto; }}
table {{ border-collapse:collapse; width:100%; margin:12px 0 32px; font-variant-numeric:tabular-nums; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; }}
th {{ color:var(--muted); font-weight:600; font-size:13px; }}
.note {{ color:var(--muted); font-size:13px; }}
section {{ margin:28px 0; }}
.grid {{ display:grid; grid-template-columns:repeat(var(--cols), minmax(0, 1fr)); gap:12px; }}
@media (max-width: 900px) {{ .grid {{ grid-template-columns:1fr; }} }}
.cell {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; min-width:0; }}
.cell.src {{ opacity:.85; }}
.cell h4 {{ margin:0 0 4px; font-size:13px; }}
.meta {{ color:var(--muted); font-size:12px; }}
pre {{ white-space:pre-wrap; word-break:break-word; font:inherit; margin:8px 0 0; }}
.flag {{ display:inline-block; background:var(--flag-bg); color:var(--flag-fg); border-radius:4px; padding:0 6px; margin:4px 4px 0 0; font-size:12px; }}
.ok {{ color:var(--ok); font-size:12px; }}
.rules {{ color:var(--muted); font-size:13px; margin:0 0 8px; }}
</style></head><body><main>
<h1>English → Thai model benchmark</h1>
<p class="note">GTX 1080 Ti · llama.cpp CUDA 12.4 · temperature 0.2 · context {CTX}. Flags are automatic heuristics; judge translation quality by reading the outputs.</p>
<table><thead><tr><th>Model</th><th>VRAM</th><th>Gen tok/s</th><th>Prompt tok/s</th><th>Thai tok / English word</th><th>Test-set time</th><th>Est. per 10k words</th><th>Flags</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
{''.join(sections)}
</main></body></html>"""
    out = BENCH / "report.html"
    out.write_text(page, encoding="utf-8")
    print(f"report: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", help="model ids from models.json (default: all)")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    items = load_jsonl(BENCH / "testset.jsonl")
    if not args.report_only:
        models = json.loads((BENCH / "models.json").read_text(encoding="utf-8"))
        for model in models:
            if not args.models or model["id"] in args.models:
                run_model(model, items)
    build_report(items)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

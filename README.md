# Document-Translation-with-llm

A self-hosted web app that translates English PDF documents into Thai with a local LLM served by
[llama.cpp](https://github.com/ggml-org/llama.cpp), and exports the result as a Thai DOCX or PDF.

- **Import:** text PDFs and scanned PDFs. [docling](https://github.com/docling-project/docling) reads the layout
  (headings, paragraphs, lists, tables, two-column pages) and Tesseract OCRs scanned pages.
- **Translate:** block by block through `llama-server`'s OpenAI-compatible API, with a glossary and a side-by-side
  review editor. Each finished block is saved, so a restart or lost connection resumes where it stopped.
- **Export:** DOCX (Thai fonts set the way Word needs them) and PDF (correct Thai shaping and line breaking).
- **Deployment:** the app runs in Docker; `llama-server` runs outside it, on the same PC or another machine.

## How it fits together

```
Browser ──► app container (FastAPI · docling + Tesseract · WeasyPrint)  ──HTTP──►  llama-server (GPU)
              port 8000, data in the "appdata" volume                    LLAMA_URL   same PC or another PC
```

## 1. Run llama-server

Any machine with llama.cpp and a GPU works. The default model is **Typhoon Translate 1.5 4B**, which won the
benchmark in [`bench/`](bench/) (best accuracy, ~46 tokens/s and 5.3 GB VRAM on a GTX 1080 Ti).

1. Download a llama.cpp build from its [releases](https://github.com/ggml-org/llama.cpp/releases). On Windows with
   an NVIDIA card take `llama-<build>-bin-win-cuda-12.4-x64.zip` plus `cudart-llama-bin-win-cuda-12.4-x64.zip`
   and unzip both into `tools/llama.cpp/`. (Pascal cards such as the GTX 10xx need CUDA 12, not 13.)
2. Download the model into `models/`:
   [`typhoon-translate1.5-4b.Q8_0.gguf`](https://huggingface.co/mradermacher/typhoon-translate1.5-4b-GGUF)
   (4.3 GB), or the official Q4_K_M from [typhoon-ai](https://huggingface.co/typhoon-ai/typhoon-translate1.5-4b-gguf).
3. Start it:

   ```powershell
   .\tools\start-llama-server.ps1
   ```

   It listens on `127.0.0.1:8080`, which is enough when the app runs on the same PC.

### llama-server on another PC

On the GPU machine, listen on the network and require an API key:

```powershell
.\tools\start-llama-server.ps1 -BindHost 0.0.0.0 -ApiKey "<long random string>"
```

Allow port 8080 from your local network only (run as administrator):

```powershell
New-NetFirewallRule -DisplayName "llama-server" -Direction Inbound -Protocol TCP -LocalPort 8080 -RemoteAddress LocalSubnet -Action Allow
```

Then set `LLAMA_URL=http://<that PC's IP>:8080` and the same `LLAMA_API_KEY` in the app's `.env`.

## 2. Run the app

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine on Linux).

```bash
cp .env.example .env        # then edit LLAMA_URL / LLAMA_API_KEY if needed
docker compose up -d --build
```

Open <http://localhost:8000>. The first build takes several minutes and produces a ~4.4 GB image (docling,
CPU-only PyTorch, Tesseract and Thai fonts).
The header shows the model name when the app can reach llama-server.

### Using it

1. **Upload** an English PDF. Scanned PDFs are detected and OCR'd automatically; tick *Force OCR* only when a PDF's
   text layer is garbage.
2. **Review** while it translates: English on the left, Thai on the right. Edit either side (it saves when you leave
   the box), or press ↻ to translate one block again.
3. **Glossary** (Settings): one `English = ไทย` per line; leave the Thai side empty to keep a term in English.
   *Re-translate all* applies a changed glossary to a finished job.
4. **Download** DOCX or PDF. Untranslated blocks are exported in English.

## Configuration

Set in `.env` (read by `docker compose`):

| Variable | Default | Meaning |
|---|---|---|
| `LLAMA_URL` | `http://host.docker.internal:8080` | llama-server address. `host.docker.internal` is the PC running Docker. |
| `LLAMA_API_KEY` | *(empty)* | Must match llama-server's `--api-key`. |
| `DOCX_FONT` | `Leelawadee UI` | Font name written into DOCX files (must exist where the file is opened). |
| `DOCX_FONT_SIZE` | `11` | Point size. For *TH Sarabun New* use 16. |

Also read by the app (add them under `environment:` in `docker-compose.yml` to change):
`LLAMA_TIMEOUT` (600 s per request), `MAX_UPLOAD_MB` (200), `PDF_FONT` (CSS font list for PDF export).

## Known limitations

- **PDF export text layer:** the PDF looks right, but copying Thai text out of it gives slightly wrong characters
  (a WeasyPrint limitation with Thai glyphs such as ำ). Use the DOCX when you need to copy or edit text.
- **OCR quality:** scans can lose superscripts (m³ → m?) and pick up table borders as `/` or `|`. Fix these in the
  review editor.
- **Layout is reflowed, not preserved:** the output is a clean single-column document. Pictures, page headers and
  footers are dropped; code, formulas and bibliography entries are kept in English.
- **One job at a time**, English source only, no login (the app listens on `127.0.0.1` only).
- **Speed:** about 11 minutes per 10,000 English words with Translate 4B on a GTX 1080 Ti, plus a few seconds per page
  for reading the PDF (more for OCR) on the CPU.

## Development

```
app/          FastAPI app: extract.py (docling), translate.py (prompt + llama client), worker.py, export.py
tests/        pytest suite (fake llama-server, export checks); make_samples.py builds sample PDFs
bench/        model benchmark: run_bench.py, testset.jsonl, results/, report.html
tools/        start-llama-server.ps1 (llama.cpp binaries go here, git-ignored)
```

Run the tests inside the app image:

```bash
docker run --rm -v "$PWD/tests:/app/tests:ro" ll-trans sh -c "pip install -q --user pytest && python -m pytest -q tests"
```

Re-run the model benchmark (needs llama.cpp in `tools/llama.cpp/` and the GGUFs listed in `bench/models.json`):

```bash
python bench/run_bench.py
```

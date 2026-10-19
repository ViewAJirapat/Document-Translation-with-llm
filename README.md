# Document-Translation-with-llm

A self-hosted web app that translates English PDF documents into Thai with a local LLM served by
[llama.cpp](https://github.com/ggml-org/llama.cpp), and exports the result as a Thai DOCX or PDF.

- **Import:** text PDFs and scanned PDFs (OCR with Tesseract), read with [docling](https://github.com/docling-project/docling).
- **Translate:** paragraph by paragraph through `llama-server`'s OpenAI-compatible API, with a glossary and
  a side-by-side review editor. Default model: Typhoon Translate 1.5 4B.
- **Export:** DOCX (Thai complex-script fonts set correctly for Word) and PDF (WeasyPrint, Thai shaping and line breaking).
- **Runs in Docker;** `llama-server` runs outside it, on the same PC or another machine.

> Status: v1 in development. Setup instructions will follow once it has been tested end to end.

`bench/` holds the model benchmark (English → Thai on a GTX 1080 Ti) used to choose the default model.

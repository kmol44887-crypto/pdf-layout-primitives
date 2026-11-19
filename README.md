# pdf-layout-primitives

A small command server that exposes a handful of **PDF primitives** over [PyMuPDF](https://github.com/pymupdf/PyMuPDF): open a file, read text blocks with their fonts and positions, find table cells, sample a page image, erase rectangles, write given HTML text into a given box with given fonts, copy a page into another, subset fonts, save.

It makes **no layout decision of its own**. A program that drives it decides what to read and what to write (reading order, paragraphs, how a translation fits a box, font and size choices, tables, languages), and sends the result as JSON instructions. This is how a translated PDF can keep the layout of the original.

PyMuPDF is licensed under the **GNU AGPL v3.0**, so this component is released under the same licence (see `LICENSE`). It uses PyMuPDF unmodified.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -I pdfprims.py serve
```

It reads one JSON request per line on stdin and writes one JSON reply per line on stdout (anything a library prints goes to stderr). The full list of requests is at the top of `pdfprims.py`. Example session:

```
{"op": "open", "doc": "a", "path": "input.pdf"}
{"ok": true, "file_size": 20871, "header_ok": true, "needs_pass": false, "encrypted": false, "pages": 3}
{"op": "dict", "doc": "a", "page": 0}
{"ok": true, "blocks": [ ...text blocks with lines, spans, fonts, sizes, colours, boxes... ]}
{"op": "redact", "doc": "a", "page": 0, "items": [{"rect": [72, 70, 300, 90], "fill": [1, 1, 1]}]}
{"op": "htmlbox", "doc": "a", "page": 0, "rect": [72, 70, 300, 90], "html": "<div>Bonjour</div>", "css": "", "scale_low": 0.7}
{"op": "save", "doc": "a", "path": "output.pdf"}
{"op": "quit"}
```

Replies are `{"ok": true, ...}` or `{"ok": false, "error": "bad_request" | "open_failed" | "failed"}`. The program opens no network connection and needs no environment variables or configuration. Treat the files you give it as untrusted input: run it as a separate process, ideally with resource limits and the network denied.

## Fonts

`fonts/` holds Noto Sans and Noto Sans Arabic (SIL Open Font License 1.1; the licence texts are included). A request can refer to them by file name in `@font-face` rules passed in `css` (for example `src:url(NotoSans-Regular.ttf)`). Checksums are in `fonts/README.md`.

## Python path note

`python -I` ignores `PYTHONPATH`. If PyMuPDF is installed in another environment than the interpreter that starts this program, put its import directories in the environment variable `PDF_COMPONENT_EXTRA_PATH` as a JSON list of strings.

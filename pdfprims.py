"""PDF primitives over PyMuPDF: a thin, decision-free command server.

A standalone program, run as a separate process: `python -I pdfprims.py serve`. It reads one JSON request per line on stdin and writes one JSON reply per line on stdout. It makes no
layout decision of its own: it opens a file, reads text blocks and fonts with their positions, finds table cells, samples a page image, erases given rectangles, writes a given HTML
text into a given box with given fonts, copies a page into another, subsets the fonts and saves. Everything that decides WHAT to ask for lives in the program that drives it.
It opens no network connection and needs no environment variables; run it with the network denied and resource limits if you can.

*** LICENCE: AGPL-3.0 (see LICENSE). It uses PyMuPDF (AGPL-3.0, https://github.com/pymupdf/PyMuPDF), unmodified. ***

Requests: {"op": <name>, ...}. Replies: {"ok": true, ...} or {"ok": false, "error": "<closed word>"} (the words: bad_request, open_failed, failed). Documents are kept under names
you choose ("doc" in the requests). Pages are 0-based. Rectangles are [x0, y0, x1, y1] in points.

  open        {doc, path}                      -> {file_size, header_ok, needs_pass, encrypted, pages}   (header_ok: the file starts with %PDF-)
  page        {doc, page}                      -> {width, height}
  text        {doc, page}                      -> {text}                       (the plain text layer of the page)
  dict        {doc, page, clip?}               -> {blocks}                     (PyMuPDF's get_text("dict")["blocks"] as JSON; images are left out)
  images      {doc, page}                      -> {images: [{bbox}]}           (get_image_info)
  tables      {doc, page}                      -> {tables: [{bbox, cells: [rect | null]}]}     (find_tables; {tables: []} when it fails)
  pixmap      {doc, page, dpi, path}           -> {width, height, n}           (the page as raw RGB bytes, row by row, written to `path`)
  jpeg        {doc, page, zoom, quality, path} -> {width, height}              (the page as a JPEG written to `path`)
  redact      {doc, page, items: [{rect, fill: [r, g, b]}]}  -> {}            (the text under each rectangle is removed, images and vector graphics stay; the area is filled)
  htmlbox     {doc, page, rect, html, css, scale_low}        -> {spare, scale}   (insert_htmlbox with the fonts of ./fonts; css may refer to them by file name)
  htmlbox_dry {width, height, rect, html, css, scale_low}    -> {spare, scale}   (the same on a scratch page: nothing is written anywhere)
  subset_fonts {doc}                           -> {}                           (replace embedded fonts by subsets; needs fontTools, skipped without it)
  new_doc     {doc}                            -> {}
  new_page    {doc, width, height}             -> {page}                       (appends a blank page, returns its index)
  show_page   {doc, page, rect, src, src_page} -> {}                           (draw page `src_page` of document `src` into `rect` of this page)
  save        {doc, path}                      -> {}                           (garbage=4, deflate)
  close       {doc}                            -> {}
  quit        {}                               (the program ends)
"""
from __future__ import annotations

import json
import os
import sys

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
MAX_REPLY_BYTES = 40_000_000


class BadRequest(Exception):
    pass


def _pymupdf():
    extra = os.environ.get("PDF_COMPONENT_EXTRA_PATH")
    if extra:
        sys.path[:0] = [p for p in json.loads(extra) if isinstance(p, str)]
    import pymupdf
    return pymupdf


class Server:
    def __init__(self):
        self.mu = _pymupdf()
        self.docs: dict = {}
        self._archive = None

    def archive(self):
        if self._archive is None:
            self._archive = self.mu.Archive(FONT_DIR)
        return self._archive

    def doc(self, req, key="doc"):
        name = req.get(key)
        if not isinstance(name, str) or name not in self.docs:
            raise BadRequest
        return self.docs[name]

    def page(self, req):
        document, number = self.doc(req), req.get("page")
        if not isinstance(number, int) or not 0 <= number < document.page_count:
            raise BadRequest
        return document[number]

    def rect(self, value):
        if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(isinstance(v, (int, float)) for v in value):
            raise BadRequest
        return self.mu.Rect(*value)

    # ------------------------------------------------------------------------------------------------------------------------------------------------------- operations

    def op_open(self, req):
        path, name = req.get("path"), req.get("doc")
        if not isinstance(path, str) or not isinstance(name, str):
            raise BadRequest
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            header_ok = handle.read(5) == b"%PDF-"
        if not header_ok:
            return {"file_size": size, "header_ok": False, "needs_pass": False, "encrypted": False, "pages": 0}
        try:
            document = self.mu.open(path)
        except Exception:                                              # noqa: BLE001 - a file MuPDF cannot open
            raise OpenFailed from None
        self.docs[name] = document
        return {"file_size": size, "header_ok": True, "needs_pass": bool(document.needs_pass), "encrypted": bool(document.is_encrypted), "pages": document.page_count}

    def op_page(self, req):
        rect = self.page(req).rect
        return {"width": rect.width, "height": rect.height}

    def op_text(self, req):
        return {"text": self.page(req).get_text()}

    def op_dict(self, req):
        clip = self.rect(req["clip"]) if req.get("clip") is not None else None
        blocks = self.page(req).get_text("dict", clip=clip)["blocks"]
        return {"blocks": [b for b in blocks if b.get("type") == 0]}

    def op_images(self, req):
        return {"images": [{"bbox": list(info["bbox"])} for info in self.page(req).get_image_info()]}

    def op_tables(self, req):
        try:
            found = self.page(req).find_tables().tables
            return {"tables": [{"bbox": list(t.bbox), "cells": [list(c) if c is not None else None for c in t.cells]} for t in found]}
        except Exception:                                              # noqa: BLE001 - table finding is best effort
            return {"tables": []}

    def op_pixmap(self, req):
        pix = self.page(req).get_pixmap(dpi=int(req["dpi"]), colorspace=self.mu.csRGB, alpha=False)
        with open(req["path"], "wb") as handle:
            handle.write(pix.samples)
        return {"width": pix.width, "height": pix.height, "n": pix.n}

    def op_jpeg(self, req):
        zoom = float(req["zoom"])
        pix = self.page(req).get_pixmap(matrix=self.mu.Matrix(zoom, zoom), colorspace=self.mu.csRGB, alpha=False)
        with open(req["path"], "wb") as handle:
            handle.write(pix.tobytes("jpg", jpg_quality=int(req["quality"])))
        return {"width": pix.width, "height": pix.height}

    def op_redact(self, req):
        page = self.page(req)
        for item in req["items"]:
            fill = tuple(float(v) for v in item["fill"])
            page.add_redact_annot(self.rect(item["rect"]), fill=fill)
        page.apply_redactions(images=self.mu.PDF_REDACT_IMAGE_NONE, graphics=self.mu.PDF_REDACT_LINE_ART_NONE)
        return {}

    def op_htmlbox(self, req):
        spare, scale = self.page(req).insert_htmlbox(self.rect(req["rect"]), str(req["html"]), css=str(req["css"]), archive=self.archive(), scale_low=float(req["scale_low"]))
        return {"spare": spare, "scale": scale}

    def op_htmlbox_dry(self, req):
        scratch = self.mu.open()
        try:
            page = scratch.new_page(width=float(req["width"]), height=float(req["height"]))
            spare, scale = page.insert_htmlbox(self.rect(req["rect"]), str(req["html"]), css=str(req["css"]), archive=self.archive(), scale_low=float(req["scale_low"]))
        finally:
            scratch.close()
        return {"spare": spare, "scale": scale}

    def op_subset_fonts(self, req):
        try:
            self.doc(req).subset_fonts()
        except Exception:                                              # noqa: BLE001 - optional: fontTools missing, or nothing to subset
            pass
        return {}

    def op_new_doc(self, req):
        name = req.get("doc")
        if not isinstance(name, str):
            raise BadRequest
        self.docs[name] = self.mu.open()
        return {}

    def op_new_page(self, req):
        document = self.doc(req)
        document.new_page(width=float(req["width"]), height=float(req["height"]))
        return {"page": document.page_count - 1}

    def op_show_page(self, req):
        self.page(req).show_pdf_page(self.rect(req["rect"]), self.doc(req, "src"), int(req["src_page"]))
        return {}

    def op_save(self, req):
        self.doc(req).save(str(req["path"]), garbage=4, deflate=True, deflate_fonts=True)
        return {}

    def op_close(self, req):
        self.doc(req).close()
        del self.docs[req["doc"]]
        return {}


class OpenFailed(Exception):
    pass


def serve() -> int:
    replies = os.fdopen(os.dup(1), "w", encoding="utf-8")          # the replies go to the original stdout; anything a library prints (PyMuPDF prints notices) goes to stderr instead
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    server = Server()
    for line in sys.stdin:
        try:
            req = json.loads(line)
            op = req["op"]
            if op == "quit":
                return 0
            handler = getattr(server, "op_" + op, None) if isinstance(op, str) else None
            if handler is None:
                raise BadRequest
            reply = {"ok": True, **handler(req)}
            data = json.dumps(reply, ensure_ascii=False)
            if len(data) > MAX_REPLY_BYTES:
                raise MemoryError
        except OpenFailed:
            data = json.dumps({"ok": False, "error": "open_failed"})
        except (BadRequest, KeyError, TypeError, ValueError):
            data = json.dumps({"ok": False, "error": "bad_request"})
        except MemoryError:
            data = json.dumps({"ok": False, "error": "failed"})
        except Exception:                                              # noqa: BLE001 - nothing of a hostile file's parser may leak: a closed word
            data = json.dumps({"ok": False, "error": "failed"})
        replies.write(data + "\n")
        replies.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        raise SystemExit(serve())
    sys.stderr.write("usage: python -I pdfprims.py serve\n")
    raise SystemExit(2)

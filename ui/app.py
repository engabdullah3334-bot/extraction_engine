# -*- coding: utf-8 -*-
"""
extraction_engine/ui/app.py
===========================
Minimalist, calm, zero-dependency standalone Web UI for `ExtractionEngine`.
Sole purpose: clearly present `ExtractionEngine`'s mission and execute structured
document extraction without intruding on core engine logic.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from extraction_engine import ExtractionEngine

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Extraction Engine</title>
<style>
  :root { --bg:#f8fafc; --card:#ffffff; --border:#e2e8f0; --text:#0f172a; --muted:#64748b; --accent:#334155; --accent-hover:#1e293b; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }
  .wrap { max-width:860px; margin:2.5rem auto; padding:0 1.25rem; }
  h1 { font-size:1.35rem; font-weight:600; margin:0 0 1rem; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:1.25rem 1.4rem; margin-bottom:1.1rem; }
  .label { font-size:0.73rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); margin-bottom:0.35rem; }
  .mission { margin:0 0 0.75rem; color:#334155; font-size:0.94rem; }
  .contracts { font-size:0.83rem; color:var(--muted); padding-top:0.65rem; border-top:1px solid var(--border); }
  code, pre { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; background:#f1f5f9; padding:0.12rem 0.38rem; border-radius:4px; font-size:0.82rem; }
  .row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:0.85rem; margin-bottom:0.9rem; }
  label.field { display:block; font-size:0.83rem; color:var(--muted); margin-bottom:0.3rem; }
  select, input[type="file"] { width:100%; padding:0.52rem 0.65rem; border:1px solid var(--border); border-radius:7px; font-size:0.86rem; background:#fff; }
  .actions { display:flex; gap:0.6rem; margin-top:0.8rem; flex-wrap:wrap; }
  button { padding:0.56rem 1.05rem; border-radius:7px; border:1px solid transparent; font-size:0.87rem; font-weight:500; cursor:pointer; background:var(--accent); color:#fff; }
  button:hover { background:var(--accent-hover); }
  button.secondary { background:#fff; color:var(--text); border-color:var(--border); }
  .metrics { display:grid; grid-template-columns:repeat(4,1fr); gap:0.75rem; margin-bottom:1rem; }
  .metric { border:1px solid var(--border); border-radius:8px; padding:0.7rem 0.9rem; background:#f8fafc; }
  .metric .v { font-size:1.25rem; font-weight:600; }
  .metric .k { font-size:0.76rem; color:var(--muted); }
  table { width:100%; border-collapse:collapse; font-size:0.85rem; margin-bottom:0.9rem; }
  th, td { text-align:left; padding:0.5rem 0.65rem; border-bottom:1px solid var(--border); vertical-align:top; }
  th { color:var(--muted); font-weight:600; background:#f8fafc; }
  pre { padding:0.85rem; overflow-x:auto; border:1px solid var(--border); border-radius:7px; white-space:pre-wrap; }
  .hidden { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Extraction Engine</h1>
  <div class="card">
    <div class="label">Engine Mission</div>
    <p class="mission">Parses verified documents (<code>PDF</code>, <code>DOCX</code>, <code>HTML</code>, <code>TXT</code>, <code>MD</code>) into a structured <code>ExtractedDocument</code> separating headings, body texts, structured tables, and media.</p>
    <div class="contracts"><b>Input:</b> Safe file path &nbsp;&middot;&nbsp; <b>Output:</b> <code>ExtractedDocument</code> (<code>headings</code>, <code>texts</code>, <code>tables</code>, <code>media</code>)</div>
  </div>

  <div class="card">
    <div class="row">
      <div>
        <label class="field">Input Source</label>
        <select id="srcMode" onchange="document.getElementById('upRow').style.display=(this.value==='upload'?'block':'none')">
          <option value="sample">Built-in Structured Sample (.md)</option>
          <option value="upload">Upload Document</option>
        </select>
      </div>
      <div>
        <label class="field">OCR Mode</label>
        <select id="ocrEnable">
          <option value="false">Disabled (Digital Fast Path)</option>
          <option value="true">Enabled (Scanned Pages / Images)</option>
        </select>
      </div>
      <div>
        <label class="field">OCR Language</label>
        <select id="ocrLang">
          <option value="ara">ara</option>
          <option value="eng">eng</option>
          <option value="ara+eng">ara+eng</option>
        </select>
      </div>
    </div>
    <div id="upRow" style="display:none; margin-bottom:0.8rem;">
      <label class="field">Choose Document (.pdf, .docx, .html, .txt, .md)</label>
      <input type="file" id="docFile" accept=".pdf,.docx,.html,.htm,.txt,.md">
    </div>
    <div class="actions">
      <button onclick="runExtract()">Extract Structure</button>
      <button class="secondary hidden" id="btnJson" onclick="dl('json')">Download JSON</button>
      <button class="secondary hidden" id="btnMd" onclick="dl('md')">Download Markdown</button>
    </div>
  </div>

  <div id="outCard" class="card hidden">
    <div class="metrics" id="metricsBox"></div>
    <div class="label">Separated Headings</div>
    <div id="headingsBox"></div>
    <div class="label" style="margin-top:1rem;">Separated Texts (Paragraphs &amp; Lists)</div>
    <div id="textsBox"></div>
    <div class="label" style="margin-top:1rem;">Separated Tables (Markdown)</div>
    <pre id="tablesBox"></pre>
  </div>
</div>

<script>
let currentRes = null;
function toB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result.split(',')[1] || '');
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}
function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
async function runExtract() {
  const mode = document.getElementById('srcMode').value;
  const payload = {
    mode: mode,
    enable_ocr: document.getElementById('ocrEnable').value === 'true',
    ocr_lang: document.getElementById('ocrLang').value
  };
  if (mode === 'upload') {
    const f = document.getElementById('docFile').files[0];
    if (!f) { alert('Select a document first.'); return; }
    payload.file_name = f.name;
    payload.file_b64 = await toB64(f);
  }
  const resp = await fetch('/api/extract', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
  });
  const data = await resp.json();
  if (!data.ok) { alert(data.error); return; }
  currentRes = data;
  const d = data.document;
  document.getElementById('outCard').classList.remove('hidden');
  document.getElementById('btnJson').classList.remove('hidden');
  document.getElementById('btnMd').classList.remove('hidden');
  document.getElementById('metricsBox').innerHTML = [
    ['Headings', d.headings.length],
    ['Text Items', d.texts.length],
    ['Tables', d.tables.length],
    ['Media', d.media.length]
  ].map(([k,v]) => `<div class="metric"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

  document.getElementById('headingsBox').innerHTML = d.headings.length
    ? `<table><tr><th>#</th><th>Lvl</th><th>Section Path</th><th>Page</th></tr>` +
      d.headings.map(h => `<tr><td>${h.index}</td><td>H${h.level}</td><td>${esc(h.section_path || h.text)}</td><td>${h.page_number}</td></tr>`).join('') + `</table>`
    : '<p style="font-size:0.85rem;color:#64748b;">No headings detected.</p>';

  document.getElementById('textsBox').innerHTML = d.texts.length
    ? `<table><tr><th>#</th><th>Role</th><th>Text</th></tr>` +
      d.texts.slice(0, 12).map(t => `<tr><td>${t.index}</td><td><code>${esc(t.role)}</code></td><td>${esc(t.text)}</td></tr>`).join('') + `</table>`
    : '<p style="font-size:0.85rem;color:#64748b;">No text items detected.</p>';

  document.getElementById('tablesBox').textContent = d.tables.length
    ? d.tables.map(tb => tb.markdown).join('\\n\\n')
    : 'No tables detected.';
}
function dl(kind) {
  if (!currentRes) return;
  const content = kind === 'json' ? JSON.stringify(currentRes.document, null, 2) : currentRes.markdown;
  const blob = new Blob([content], {type: kind === 'json' ? 'application/json' : 'text/markdown'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = kind === 'json' ? 'extracted_document.json' : 'extracted_document.md';
  a.click();
}
</script>
</body>
</html>
"""


def execute_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode", "sample")
    enable_ocr = bool(payload.get("enable_ocr", False))
    ocr_lang = str(payload.get("ocr_lang") or "ara")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        if mode == "upload":
            fname = payload.get("file_name") or "document.txt"
            doc_path = tmp / Path(fname).name
            doc_path.write_bytes(base64.b64decode(payload.get("file_b64") or ""))
        else:
            doc_path = tmp / "sample_architecture.md"
            doc_path.write_text(
                "# Part 1: Modular Design\n\n"
                "Independent engines communicate through clean data contracts.\n\n"
                "## Section 1.1: Core Principles\n\n"
                "- Single entry facade\n"
                "- Zero cross-engine imports\n\n"
                "| Element | Separated Collection |\n"
                "| --- | --- |\n"
                "| Section Titles | `doc.headings` |\n"
                "| Paragraphs & Lists | `doc.texts` |\n"
                "| Structured Grids | `doc.tables` |\n",
                encoding="utf-8",
            )
        try:
            extractor = ExtractionEngine(enable_ocr=enable_ocr, ocr_lang=ocr_lang, media_dir=tmp / "media")
            doc = extractor.extract(doc_path)
            return {"ok": True, "document": doc.to_dict(), "markdown": doc.to_markdown()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class ExtractionUIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/extract":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = json.loads(self.rfile.read(length).decode("utf-8"))
        out = json.dumps(execute_extraction(raw), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _pick_port(preferred: int = 8702) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve_ui(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> None:
    actual_port = port or _pick_port(8702)
    server = ThreadingHTTPServer((host, actual_port), ExtractionUIHandler)
    url = f"http://{host}:{actual_port}"
    print(f"Extraction Engine UI running at {url} (Press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Extraction Engine UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Extraction Engine Standalone UI")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    serve_ui(port=args.port, open_browser=not args.no_browser)

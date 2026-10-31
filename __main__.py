# -*- coding: utf-8 -*-
"""
extraction_engine/__main__.py
=============================
Standalone CLI, UI launcher, and self-test runner for `extraction_engine`.

Commands:
    python -m extraction_engine --ui
    python -m extraction_engine --self-test
    python -m extraction_engine path/to/file.pdf --out-json out.json --out-md out.md
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from extraction_engine import ExtractionEngine
else:
    from .engine import ExtractionEngine


def _configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def launch_ui() -> None:
    app_path = Path(__file__).resolve().parent / "ui" / "app.py"
    subprocess.run([sys.executable, str(app_path)], check=False)


def run_self_test() -> None:
    _configure_utf8()
    print("=" * 64)
    print("  ExtractionEngine — Standalone Self-Test Suite")
    print("=" * 64)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        ext_engine = ExtractionEngine(enable_ocr=False, media_dir=tmp / "media")

        # 1. Structured Markdown with Headings + Paragraphs + Lists + Table (RTL Arabic test payload)
        sample_md = tmp / "sample_bilingual.md"
        sample_md.write_text(
            "# الباب الأول: هندسة النظم\n\n"
            "هذه فقرة تمهيدية تشرح بنية المحركات المستقلة.\n\n"
            "## الفصل الأول: الجداول والبيانات\n\n"
            "- عنصر قائمة أول\n"
            "- عنصر قائمة ثانٍ\n\n"
            "| المحرك | الوظيفة |\n"
            "| --- | --- |\n"
            "| الأمان | فحص الملفات |\n"
            "| الاستخراج | فصل البنية |\n",
            encoding="utf-8",
        )
        doc_md = ext_engine.extract(sample_md)
        assert len(doc_md.headings) == 2
        assert doc_md.headings[1].section_path == "الباب الأول: هندسة النظم > الفصل الأول: الجداول والبيانات"
        assert len(doc_md.texts) == 3  # 1 paragraph + 2 list items
        assert len(doc_md.tables) == 1 and doc_md.tables[0].col_count == 2
        assert doc_md.metadata.language == "ara"
        print(
            f"  [PASS] Markdown extraction -> headings={len(doc_md.headings)}, "
            f"texts={len(doc_md.texts)}, tables={len(doc_md.tables)}"
        )

        # 2. HTML extraction test (if lxml installed)
        try:
            import lxml  # noqa: F401

            sample_html = tmp / "page.html"
            sample_html.write_text(
                "<html dir='rtl'><head><title>Test</title></head><body>"
                "<h1>Main Heading</h1><p>Sample paragraph inside HTML.</p>"
                "<table><tr><th>Col 1</th><th>Col 2</th></tr>"
                "<tr><td>Val A</td><td>Val B</td></tr></table>"
                "</body></html>",
                encoding="utf-8",
            )
            doc_html = ext_engine.extract(sample_html)
            assert len(doc_html.headings) == 1
            assert len(doc_html.texts) == 1
            assert len(doc_html.tables) == 1
            print(
                f"  [PASS] HTML extraction     -> headings={len(doc_html.headings)}, "
                f"texts={len(doc_html.texts)}, tables={len(doc_html.tables)}"
            )
        except ImportError:
            print("  [SKIP] HTML extraction     -> skipped (lxml not installed)")

        # 3. DOCX round-trip export & re-extraction test (if python-docx installed)
        try:
            import docx  # noqa: F401

            out_docx = tmp / "exported.docx"
            doc_md.to_docx(out_docx)
            assert out_docx.exists()
            doc_docx = ext_engine.extract(out_docx)
            assert len(doc_docx.tables) == 1
            assert len(doc_docx.texts) + len(doc_docx.headings) >= 3
            print(
                f"  [PASS] DOCX export+extract -> tables={len(doc_docx.tables)}, "
                f"blocks={len(doc_docx.texts) + len(doc_docx.headings)}"
            )
        except ImportError:
            print("  [SKIP] DOCX export+extract -> skipped (python-docx not installed)")

    print("=" * 64)
    print("  ALL EXTRACTION ENGINE SELF-TESTS PASSED SUCCESSFULLY!")
    print("=" * 64)


def main() -> None:
    _configure_utf8()
    parser = argparse.ArgumentParser(description="ExtractionEngine Standalone CLI & UI")
    parser.add_argument("file", nargs="?", help="Path to safe document (.pdf, .docx, .html, .txt, .md)")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR on scanned pages/images")
    parser.add_argument("--lang", type=str, default="ara", help="OCR language (ara, eng, ara+eng)")
    parser.add_argument("--out-json", type=str, default=None, help="Save structured JSON output to path")
    parser.add_argument("--out-md", type=str, default=None, help="Save Markdown output to path")
    parser.add_argument("--out-docx", type=str, default=None, help="Save styled DOCX output to path")
    parser.add_argument("--ui", action="store_true", help="Launch calm standalone Web UI")
    parser.add_argument("--self-test", action="store_true", help="Run standalone self-test suite")
    args = parser.parse_args()

    if args.ui:
        launch_ui()
        return

    if args.self_test:
        run_self_test()
        return

    if not args.file:
        parser.print_help()
        sys.exit(1)

    engine = ExtractionEngine(enable_ocr=not args.no_ocr, ocr_lang=args.lang)
    doc = engine.extract(args.file)

    print(f"File     : {doc.metadata.file_name} ({doc.metadata.source_format.upper()})")
    print(f"Pages    : {doc.metadata.total_pages} | Lang: {doc.metadata.language}")
    print(f"Headings : {len(doc.headings)}")
    print(f"Texts    : {len(doc.texts)}")
    print(f"Tables   : {len(doc.tables)}")
    print(f"Media    : {len(doc.media)}")

    if args.out_json:
        doc.to_json(args.out_json)
        print(f"Saved JSON -> {args.out_json}")
    if args.out_md:
        doc.to_markdown(args.out_md)
        print(f"Saved MD   -> {args.out_md}")
    if args.out_docx:
        doc.to_docx(args.out_docx)
        print(f"Saved DOCX -> {args.out_docx}")


if __name__ == "__main__":
    main()

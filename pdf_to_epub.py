#!/usr/bin/env python3
"""
Convert a PDF to EPUB.

- Fixed mode preserves visual layout by converting pages to SVG.
- Reflow mode extracts text into flowing XHTML for reader features.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import fitz  # PyMuPDF


DEFAULT_INPUT_DIR = Path("input_pdfs")
DEFAULT_OUTPUT_DIR = Path("output_epubs")


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _safe_id(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.strip())
    return cleaned.strip("_") or "book"


def _content_opf(title: str, creator: str, page_count: int, reflow: bool) -> str:
    manifest_items = []
    spine_items = []
    if reflow:
        manifest_items.append(
            '    <item id="chapter1" href="pages/chapter_0001.xhtml" media-type="application/xhtml+xml"/>'
        )
        spine_items.append('    <itemref idref="chapter1"/>')
    else:
        for i in range(1, page_count + 1):
            manifest_items.append(
                f'    <item id="p{i}" href="pages/page_{i:04d}.xhtml" media-type="application/xhtml+xml"/>'
            )
            manifest_items.append(
                f'    <item id="svg{i}" href="images/page_{i:04d}.svg" media-type="image/svg+xml"/>'
            )
            spine_items.append(f'    <itemref idref="p{i}"/>')

    manifest = "\n".join(manifest_items)
    spine = "\n".join(spine_items)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="bookid"
         xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:{_safe_id(title)}-{page_count}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{html.escape(creator)}</dc:creator>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-03-23T00:00:00Z</meta>
    <meta property="rendition:layout">{"reflowable" if reflow else "pre-paginated"}</meta>
    <meta property="rendition:orientation">auto</meta>
    <meta property="rendition:spread">none</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>
"""


def _nav_xhtml(title: str, page_count: int, reflow: bool) -> str:
    if reflow:
        page_links = '          <li><a href="pages/chapter_0001.xhtml">Content</a></li>'
    else:
        page_links = "\n".join(
            f'          <li><a href="pages/page_{i:04d}.xhtml">Page {i}</a></li>'
            for i in range(1, page_count + 1)
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
  <head>
    <title>{html.escape(title)}</title>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>{html.escape(title)}</h1>
      <ol>
{page_links}
      </ol>
    </nav>
  </body>
</html>
"""


def _page_xhtml(svg_name: str, width_pt: float, height_pt: float, page_number: int) -> str:
    view_box = f"0 0 {width_pt:.2f} {height_pt:.2f}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
  <head>
    <title>Page {page_number}</title>
    <meta name="viewport" content="width={width_pt:.2f}, height={height_pt:.2f}"/>
    <style>
      html, body {{
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
      }}
      object {{
        width: 100%;
        height: 100%;
      }}
    </style>
  </head>
  <body>
    <object data="../images/{svg_name}" type="image/svg+xml" aria-label="Page {page_number}" viewBox="{view_box}"></object>
  </body>
</html>
"""


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _join_wrapped_lines(lines: list[str]) -> str:
    if not lines:
        return ""

    out = lines[0]
    for nxt in lines[1:]:
        prev = out.rstrip()
        # Keep hyphenated words together when line wraps mid-word.
        if prev.endswith("-") and nxt and nxt[:1].islower():
            out = prev[:-1] + nxt.lstrip()
            continue

        prev_last = prev[-1:] if prev else ""
        starts_like_continuation = nxt[:1].islower() or nxt[:1].isdigit()
        if prev_last and prev_last not in ".!?;:" and starts_like_continuation:
            out = f"{prev} {nxt.lstrip()}"
        else:
            out = f"{prev}\n{nxt}"

    return out


def _paragraphize_block_text(text: str) -> list[str]:
    raw_lines = [_normalize_spaces(ln) for ln in text.splitlines()]
    raw_lines = [ln for ln in raw_lines if ln]
    if not raw_lines:
        return []

    joined = _join_wrapped_lines(raw_lines)
    paras = [_normalize_spaces(p) for p in joined.split("\n")]
    return [p for p in paras if p]


def _extract_reflow_paragraphs(page: fitz.Page) -> list[str]:
    paragraphs: list[str] = []
    blocks = page.get_text("blocks", sort=True)
    for block in blocks:
        # block tuple: (x0, y0, x1, y1, text, block_no, block_type)
        text = block[4] if len(block) > 4 else ""
        if not text.strip():
            continue
        paragraphs.extend(_paragraphize_block_text(text))
    return paragraphs


def _reflow_xhtml(title: str, body_html: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
  <head>
    <title>{html.escape(title)}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <style>
      body {{
        line-height: 1.5;
        margin: 0 auto;
        max-width: 42em;
        padding: 1.2em;
      }}
      p {{
        margin: 0 0 1em;
        text-align: left;
      }}
      .page-break {{
        display: none;
      }}
    </style>
  </head>
  <body>
{body_html}
  </body>
</html>
"""


def convert_pdf_to_epub(
    pdf_path: Path, epub_path: Path, title: str | None, author: str, reflow: bool
) -> None:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    if page_count == 0:
        raise ValueError("Input PDF has no pages.")

    book_title = title or pdf_path.stem

    with ZipFile(epub_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML, compress_type=ZIP_DEFLATED)

        zf.writestr(
            "OEBPS/nav.xhtml",
            _nav_xhtml(book_title, page_count, reflow),
            compress_type=ZIP_DEFLATED,
        )
        zf.writestr(
            "OEBPS/content.opf",
            _content_opf(book_title, author, page_count, reflow),
            compress_type=ZIP_DEFLATED,
        )

        if reflow:
            parts = []
            for idx in range(page_count):
                page = doc.load_page(idx)
                page_no = idx + 1
                parts.append(
                    f'<span class="page-break" epub:type="pagebreak" title="Page {page_no}" id="p{page_no}"></span>'
                )
                paragraphs = _extract_reflow_paragraphs(page)
                if paragraphs:
                    parts.extend(f"<p>{html.escape(p)}</p>" for p in paragraphs)
            chapter = _reflow_xhtml(book_title, "\n".join(parts))
            zf.writestr(
                "OEBPS/pages/chapter_0001.xhtml",
                chapter,
                compress_type=ZIP_DEFLATED,
            )
        else:
            for idx in range(page_count):
                page = doc.load_page(idx)
                page_number = idx + 1
                rect = page.rect
                svg_bytes = page.get_svg_image(text_as_path=False).encode("utf-8")
                svg_name = f"page_{page_number:04d}.svg"
                xhtml_name = f"page_{page_number:04d}.xhtml"

                zf.writestr(f"OEBPS/images/{svg_name}", svg_bytes, compress_type=ZIP_DEFLATED)
                zf.writestr(
                    f"OEBPS/pages/{xhtml_name}",
                    _page_xhtml(svg_name, rect.width, rect.height, page_number),
                    compress_type=ZIP_DEFLATED,
                )

    doc.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert PDF to EPUB (fixed layout or reflowable)."
    )
    parser.add_argument(
        "input_pdf",
        type=Path,
        help="PDF path or filename in input_pdfs/",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output EPUB path (default: output_epubs/<input-name>.epub)",
    )
    parser.add_argument("--title", type=str, default=None, help="Optional EPUB title")
    parser.add_argument("--author", type=str, default="Unknown", help="EPUB author metadata")
    parser.add_argument(
        "--reflow",
        action="store_true",
        help="Generate reflowable EPUB for dark mode and text sizing support.",
    )
    return parser.parse_args(argv)


def resolve_input_path(input_arg: Path) -> Path:
    # If user passes just a filename, default to input_pdfs/<filename>.
    if input_arg.parent == Path("."):
        return DEFAULT_INPUT_DIR / input_arg
    return input_arg


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    input_pdf = resolve_input_path(args.input_pdf)
    output = args.output or (DEFAULT_OUTPUT_DIR / input_pdf.with_suffix(".epub").name)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        convert_pdf_to_epub(input_pdf, output, args.title, args.author, args.reflow)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Created EPUB: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

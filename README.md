# Epubify

Simple CLI tool to convert PDF files to EPUB.

## Folders

- `input_pdfs/` -> place source PDFs here
- `output_epubs/` -> converted EPUB files are saved here

## Install

```bash
cd /Users/adi/Code/epub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Convert

Fixed layout (keeps original PDF look):

```bash
python pdf_to_epub.py "book.pdf"
```

Reflow mode (supports dark mode and text size in Apple Books):

```bash
python pdf_to_epub.py "book.pdf" --reflow
```

Optional custom output:

```bash
python pdf_to_epub.py "book.pdf" -o "output_epubs/book-new.epub"
```

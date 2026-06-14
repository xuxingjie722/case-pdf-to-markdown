#!/usr/bin/env python3
"""
Extract and clean text from PDF case files or Markdown text files.

Usage:
    python extract_text.py <input.pdf|input.md|input.txt> [--pages 1-50] [--output cleaned.txt]

Output:
    Cleaned plain text with page markers (===PAGE_N===), headers/footers removed,
    and normalized line breaks. Suitable for downstream document splitting.
"""

import argparse
import re
import sys
from pathlib import Path


def extract_pdf_text(pdf_path: str, page_range: tuple | None = None) -> dict[int, str]:
    """Extract text from PDF using PyMuPDF (fitz). Returns {page_num: text}."""
    # Import shared utility from same directory
    sys.path.insert(0, str(Path(__file__).parent))
    from pdf_utils import extract_text_pymupdf
    return extract_text_pymupdf(Path(pdf_path), page_range)


def read_text_file(file_path: str) -> str:
    """Read a plain text or Markdown file."""
    for encoding in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Cannot decode file: {file_path}")


def remove_headers_footers(pages: dict[int, str]) -> dict[int, str]:
    """
    Remove repetitive headers/footers from each page.
    Strategy: detect lines that repeat across >50% of pages and remove them.
    """
    if len(pages) < 3:
        return pages

    # Collect first and last 2 lines of each page as header/footer candidates
    from collections import Counter
    header_candidates = []
    footer_candidates = []

    for text in pages.values():
        lines = text.strip().split('\n')
        if len(lines) >= 3:
            header_candidates.append(lines[0].strip())
            footer_candidates.append(lines[-1].strip())
        if len(lines) >= 4:
            header_candidates.append(lines[1].strip())
            footer_candidates.append(lines[-2].strip())

    # Lines that appear in >50% of pages are likely headers/footers
    threshold = max(len(pages) * 0.5, 2)
    header_counts = Counter(header_candidates)
    footer_counts = Counter(footer_candidates)
    headers_to_remove = {h for h, c in header_counts.items() if c >= threshold and len(h) > 1}
    footers_to_remove = {f for f, c in footer_counts.items() if c >= threshold and len(f) > 1}

    cleaned = {}
    for num, text in pages.items():
        lines = text.strip().split('\n')
        if len(lines) >= 3:
            if lines[0].strip() in headers_to_remove:
                lines[0] = ''
            if lines[1].strip() in headers_to_remove:
                lines[1] = ''
            if lines[-1].strip() in footers_to_remove:
                lines[-1] = ''
            if len(lines) >= 4 and lines[-2].strip() in footers_to_remove:
                lines[-2] = ''
        cleaned[num] = '\n'.join(line for line in lines if line.strip())

    return cleaned


def normalize_text(pages: dict[int, str]) -> str:
    """
    Normalize text: merge broken paragraphs, normalize whitespace, add page markers.
    """
    result_parts = []
    for num in sorted(pages.keys()):
        text = pages[num].strip()
        if not text:
            result_parts.append(f"===PAGE_{num}===")
            continue

        # Normalize whitespace
        text = re.sub(r'[ \t]+', ' ', text)

        # Merge lines that look like broken paragraphs
        # A line ending without Chinese punctuation (。！？；：""）) likely continues
        lines = text.split('\n')
        merged = []
        buffer = ''
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if buffer:
                    merged.append(buffer)
                    buffer = ''
                continue
            # If previous line doesn't end with sentence-ending punctuation, merge
            if buffer and not re.search(r'[。！？；：」』）\)\.!\?;:\-]$', buffer):
                buffer += stripped
            else:
                if buffer:
                    merged.append(buffer)
                buffer = stripped

        if buffer:
            merged.append(buffer)

        result_parts.append(f"===PAGE_{num}===")
        result_parts.extend(merged)

    return '\n'.join(result_parts)


def clean_punctuation(text: str) -> str:
    """Normalize full-width/half-width issues common in PDF extraction."""
    # Normalize full-width numbers to half-width
    # Keep Chinese punctuation unchanged
    replacements = {
        '　': ' ',   # full-width space
        ' ': ' ',   # non-breaking space
        '\r': '\n',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text


def main():
    parser = argparse.ArgumentParser(
        description="Extract and clean text from PDF case files or text files"
    )
    parser.add_argument('input', help="Input file (PDF, MD, or TXT)")
    parser.add_argument('--pages', '-p', help="Page range, e.g. '1-50'")
    parser.add_argument('--output', '-o', help="Output file path (default: stdout)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.stderr.write(f"Error: file not found: {args.input}\n")
        sys.exit(1)

    # Parse page range
    page_range = None
    if args.pages:
        parts = args.pages.split('-')
        page_range = (int(parts[0]), int(parts[1]) if len(parts) > 1 else int(parts[0]))

    # Extract text
    suffix = input_path.suffix.lower()
    if suffix == '.pdf':
        pages = extract_pdf_text(str(input_path), page_range)
        pages = remove_headers_footers(pages)
        text = normalize_text(pages)
    elif suffix in ('.md', '.txt', '.text'):
        text = read_text_file(str(input_path))
        # Add a single page marker for non-PDF files
        text = f"===PAGE_1===\n{text}"
    else:
        sys.stderr.write(f"Error: unsupported file type: {suffix}\n")
        sys.exit(1)

    text = clean_punctuation(text)

    # Output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"Cleaned text saved to: {args.output}")
    else:
        print(text)


if __name__ == '__main__':
    main()

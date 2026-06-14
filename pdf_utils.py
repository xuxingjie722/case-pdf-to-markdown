#!/usr/bin/env python3
"""
Shared PDF utilities for case-file-reviewer scripts.

Uses PyMuPDF (fitz) for PDF inspection, text extraction,
scanned-page detection, splitting, and hashing.
"""

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


# ── PDF Info ──────────────────────────────────────────────────────────

def get_pdf_info(pdf_path: Path) -> dict:
    """Analyze a PDF and return metadata dict.

    Keys: path, pages, size_mb, has_text, is_scanned, needs_ocr, needs_split
    """
    stat = pdf_path.stat()
    size_mb = stat.st_size / (1024 * 1024)

    doc = fitz.open(str(pdf_path))
    pages = doc.page_count

    # Sample up to 5 pages to detect text presence
    sample_indexes = _sample_pages(pages, 5)
    text_char_counts = []
    image_counts = []
    for i in sample_indexes:
        page = doc[i]
        text = page.get_text() or ""
        text_char_counts.append(len(text.strip()))
        image_counts.append(len(page.get_images()))

    doc.close()

    pages_with_text = sum(1 for c in text_char_counts if c >= 50)
    pages_with_images = sum(1 for c in image_counts if c > 0)
    text_ratio = pages_with_text / max(len(sample_indexes), 1)
    is_scanned = text_ratio < 0.3 and pages_with_images > 0

    # Decision flags
    needs_ocr = is_scanned
    needs_split = pages > 200 or size_mb > 190  # mineru extract 实际限制: 200页 / 200MB

    return {
        "path": str(pdf_path),
        "pages": pages,
        "size_mb": round(size_mb, 2),
        "has_text": text_ratio >= 0.5,
        "is_scanned": is_scanned,
        "needs_ocr": needs_ocr,
        "needs_split": needs_split,
        "text_ratio": round(text_ratio, 2),
    }


def _sample_pages(total: int, count: int) -> list[int]:
    """Return evenly-spaced page indexes for sampling."""
    if total <= count:
        return list(range(total))
    step = max(total // count, 1)
    indexes = [i for i in range(0, total, step)]
    # Ensure we include the last page
    if indexes[-1] != total - 1:
        indexes.append(total - 1)
    return indexes[:count]


# ── Text Extraction ───────────────────────────────────────────────────

def extract_text_pymupdf(
    pdf_path: Path,
    page_range: Optional[tuple[int, int]] = None,
) -> dict[int, str]:
    """Extract text from PDF using PyMuPDF. Returns {page_num: text}."""
    pages = {}
    doc = fitz.open(str(pdf_path))
    total = doc.page_count
    start_page = (page_range[0] if page_range else 1)
    end_page = (page_range[1] if page_range else total)

    for i in range(start_page - 1, min(end_page, total)):
        page_num = i + 1
        text = doc[i].get_text() or ""
        pages[page_num] = text

    doc.close()
    return pages


# ── Scanned Detection ─────────────────────────────────────────────────

def is_scanned(pdf_path: Path, sample_pages: int = 5) -> bool:
    """Return True if PDF is primarily scanned (image-based with no text layer)."""
    info = get_pdf_info(pdf_path)
    return info["is_scanned"]


# ── PDF Splitting ─────────────────────────────────────────────────────

def split_pdf(
    pdf_path: Path,
    output_dir: Path,
    chunk_pages: int = 200,
) -> list[Path]:
    """Split a large PDF into chunks of chunk_pages pages each.

    Returns list of paths to split PDFs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    total = doc.page_count
    base_name = pdf_path.stem
    split_paths = []

    for start in range(0, total, chunk_pages):
        end = min(start + chunk_pages, total)
        chunk_doc = fitz.open()  # new empty PDF
        chunk_doc.insert_pdf(doc, from_page=start, to_page=end - 1)

        chunk_num = start // chunk_pages + 1
        chunk_path = output_dir / f"{base_name}_分卷{chunk_num:03d}.pdf"
        chunk_doc.save(str(chunk_path))
        chunk_doc.close()
        split_paths.append(chunk_path)

    doc.close()
    return split_paths


# ── MD5 Hashing ───────────────────────────────────────────────────────

def pdf_md5(pdf_path: Path) -> str:
    """Compute MD5 hash of file for change detection."""
    hasher = hashlib.md5()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# ── External Tool Runner ──────────────────────────────────────────────

def run_tool(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """Run an external tool with timeout and error capture."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

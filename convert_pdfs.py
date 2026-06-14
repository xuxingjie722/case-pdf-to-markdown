#!/usr/bin/env python3
"""
案卷 PDF → Markdown 转换脚本

用法:
    python3 convert_pdfs.py a.pdf b.pdf           # 转换指定 PDF
    python3 convert_pdfs.py --case "案件名"         # 转换指定案件的全部案卷 PDF
    python3 convert_pdfs.py --force a.pdf          # 强制重新转换
    python3 convert_pdfs.py --dry-run a.pdf        # 试运行

工具优先级: MinerU extract → MinerU flash-extract → markitdown → PyMuPDF 本地
输出目录: {PDF所在目录}/{PDF文件名}md/
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# Ensure sibling scripts are importable
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from pdf_utils import get_pdf_info, pdf_md5, run_tool, split_pdf, extract_text_pymupdf

# ── Constants ─────────────────────────────────────────────────────────

# 默认工作区路径，不存在时回退到当前目录
_DEFAULT_WORKSPACE = Path("/Users/jason/办公区/1.未结案件")
WORKSPACE_ROOT = _DEFAULT_WORKSPACE if _DEFAULT_WORKSPACE.is_dir() else Path.cwd()
STATE_FILE_NAME = ".convert_state.json"

# MinerU limits (actual API limits)
FLASH_MAX_MB = 10
FLASH_MAX_PAGES = 20
EXTRACT_MAX_MB = 190
EXTRACT_MAX_PAGES = 200  # mineru extract 实际限制 200 页

# ── Discovery ─────────────────────────────────────────────────────────

def discover_files(
    pdf_paths: list[str] | None = None,
    case_name: str | None = None,
) -> list[Path]:
    """Resolve user input to a list of absolute PDF paths."""
    if pdf_paths:
        result = []
        for p in pdf_paths:
            path = Path(p)
            if not path.is_absolute():
                path = WORKSPACE_ROOT / path
            if not path.exists():
                print(f"⚠ 文件不存在，跳过: {path}")
                continue
            if path.suffix.lower() != ".pdf":
                print(f"⚠ 非 PDF 文件，跳过: {path}")
                continue
            result.append(path.resolve())
        return result

    if case_name:
        case_dir = WORKSPACE_ROOT / case_name
        if not case_dir.is_dir():
            # Try fuzzy match
            matches = [d for d in WORKSPACE_ROOT.iterdir()
                       if d.is_dir() and case_name in d.name]
            if len(matches) == 1:
                case_dir = matches[0]
            elif len(matches) > 1:
                print(f"⚠ 匹配到多个案件文件夹: {[m.name for m in matches]}")
                sys.exit(1)
            else:
                print(f"✗ 未找到案件文件夹: {case_name}")
                sys.exit(1)
        return _scan_case_pdfs(case_dir)

    print("✗ 请提供 PDF 路径或 --case 案件名")
    sys.exit(1)


def _scan_case_pdfs(case_dir: Path) -> list[Path]:
    """Recursively find all PDFs in a case folder, excluding output dirs."""
    pdfs = []
    for pdf_path in case_dir.rglob("*.pdf"):
        # Skip files inside output directories (name ends with 'md')
        if pdf_path.parent.name.endswith("md"):
            continue
        pdfs.append(pdf_path)
    return sorted(pdfs, key=lambda p: (-p.stat().st_size, p.name))


# ── Tool Selection ────────────────────────────────────────────────────

def select_tool(info: dict) -> str:
    """Choose the best conversion tool based on PDF characteristics.

    Returns one of: 'flash', 'extract', 'extract_split', 'markitdown', 'local'
    """
    size_mb = info["size_mb"]
    pages = info["pages"]
    needs_ocr = info["needs_ocr"]
    has_text = info["has_text"]
    needs_split = info["needs_split"]

    # Scanned PDF or needs OCR → must use extract (flash doesn't OCR well)
    if needs_ocr or not has_text:
        if needs_split:
            return "extract_split"
        return "extract"

    # Small text-based PDF → flash
    if size_mb <= FLASH_MAX_MB and pages <= FLASH_MAX_PAGES:
        return "flash"

    # Large text-based → extract or split
    if needs_split:
        return "extract_split"
    if size_mb <= EXTRACT_MAX_MB and pages <= EXTRACT_MAX_PAGES:
        return "extract"

    # Fallback
    return "extract_split"


# ── Conversion Dispatchers ────────────────────────────────────────────

def convert_one(
    pdf_path: Path,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Convert a single PDF. Returns result dict."""
    start_time = time.time()

    # Output: {pdf所在目录}/{pdf文件名去掉.pdf}md/
    output_dir = pdf_path.parent / f"{pdf_path.stem}md"
    output_md = output_dir / f"{pdf_path.stem}.md"

    # Check state
    state = _load_state(output_dir)
    rel_path = str(pdf_path.relative_to(WORKSPACE_ROOT)
                   if pdf_path.is_relative_to(WORKSPACE_ROOT)
                   else pdf_path.name)
    current_md5 = pdf_md5(pdf_path)

    if not force and rel_path in state.get("files", {}):
        prev = state["files"][rel_path]
        if prev.get("md5") == current_md5 and prev.get("status") == "success":
            if output_md.exists():
                return {
                    "pdf_path": str(pdf_path),
                    "output_path": str(output_md),
                    "tool_used": prev.get("tool", "unknown"),
                    "status": "skipped",
                    "duration_s": round(time.time() - start_time, 1),
                    "reason": "已转换，源文件未变",
                }

    if dry_run:
        return {
            "pdf_path": str(pdf_path),
            "output_path": str(output_md),
            "tool_used": "dry-run",
            "status": "dry_run",
            "duration_s": 0,
        }

    # Classify PDF
    print(f"  分析 PDF...", end=" ")
    try:
        info = get_pdf_info(pdf_path)
    except Exception as e:
        return {
            "pdf_path": str(pdf_path),
            "output_path": "",
            "tool_used": "",
            "status": "failed",
            "duration_s": round(time.time() - start_time, 1),
            "error": f"无法读取 PDF: {e}",
        }

    tool = select_tool(info)
    print(f"{info['pages']}页 / {info['size_mb']}MB "
          f"/ {'扫描件' if info['is_scanned'] else '文字版'} → {tool}")

    # Run conversion
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if tool == "flash":
            _convert_via_mineru_flash(pdf_path, output_dir, info)
        elif tool == "extract":
            _convert_via_mineru_extract(pdf_path, output_dir, info)
        elif tool == "extract_split":
            _convert_via_split(pdf_path, output_dir, info)
        elif tool == "markitdown":
            _convert_via_markitdown(pdf_path, output_md)
        elif tool == "local":
            _convert_via_pymupdf(pdf_path, output_md)
        else:
            raise ValueError(f"未知工具: {tool}")

        # Verify output exists
        if not output_md.exists() or output_md.stat().st_size == 0:
            raise RuntimeError("输出文件为空或不存在")

        # Add metadata header
        _add_metadata_header(output_md, pdf_path, tool, info)

        duration = round(time.time() - start_time, 1)
        result = {
            "pdf_path": str(pdf_path),
            "output_path": str(output_md),
            "tool_used": tool,
            "status": "success",
            "duration_s": duration,
            "pages": info["pages"],
            "size_mb": info["size_mb"],
        }

    except Exception as e:
        # Fallback chain — 逐级降级
        err_msg = str(e)[:200]
        print(f"  ⚠ {tool} 失败: {err_msg}，尝试降级...")

        # 构建降级链
        if tool == "extract" and (info["pages"] > EXTRACT_MAX_PAGES or info["size_mb"] > EXTRACT_MAX_MB):
            chain = [("extract_split", "自动拆分 → extract"), ("markitdown", "markitdown"), ("local", "PyMuPDF 本地")]
        elif tool in ("extract", "extract_split", "flash"):
            chain = [("markitdown", "markitdown"), ("local", "PyMuPDF 本地")]
        else:
            chain = [("local", "PyMuPDF 本地")]

        saved_tool = tool
        saved_result = None
        for fb_tool, fb_label in chain:
            try:
                print(f"  → 降级到 {fb_label}...")
                if fb_tool == "extract_split":
                    _convert_via_split(pdf_path, output_dir, info)
                elif fb_tool == "markitdown":
                    _convert_via_markitdown(pdf_path, output_md)
                elif fb_tool == "local":
                    _convert_via_pymupdf(pdf_path, output_md)

                if not output_md.exists() or output_md.stat().st_size == 0:
                    raise RuntimeError(f"{fb_tool} 输出为空")
                _add_metadata_header(output_md, pdf_path, fb_tool, info)

                duration = round(time.time() - start_time, 1)
                saved_result = {
                    "pdf_path": str(pdf_path),
                    "output_path": str(output_md),
                    "tool_used": fb_tool,
                    "status": "success",
                    "duration_s": duration,
                    "fallback": True,
                    "fallback_from": saved_tool,
                    "fallback_reason": err_msg,
                }
                break  # 成功，跳出降级链

            except Exception as fb_err:
                print(f"  ⚠ {fb_label} 也失败: {str(fb_err)[:150]}")
                saved_result = {
                    "pdf_path": str(pdf_path),
                    "output_path": str(output_md) if output_md.exists() else "",
                    "tool_used": fb_tool,
                    "status": "failed",
                    "duration_s": round(time.time() - start_time, 1),
                    "error": str(fb_err)[:200],
                }
                continue  # 尝试下一级

        result = saved_result

    # Update state
    _update_state(output_dir, rel_path, current_md5, result)
    return result


# ── Tool-specific converters ──────────────────────────────────────────

def _convert_via_mineru_flash(pdf_path: Path, output_dir: Path, info: dict):
    """Convert using mineru flash-extract (fast, no token required)."""
    cmd = [
        "mineru-open-api", "flash-extract",
        str(pdf_path),
        "-o", str(output_dir),
        "--language", "ch",
    ]
    if info.get("needs_ocr"):
        cmd.append("--ocr")

    result = run_tool(cmd, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"flash-extract 失败: {result.stderr[:200]}")
    _collect_mineru_output(output_dir, pdf_path.stem)


def _convert_via_mineru_extract(pdf_path: Path, output_dir: Path, info: dict):
    """Convert using mineru extract (high precision, token required)."""
    cmd = [
        "mineru-open-api", "extract",
        str(pdf_path),
        "-o", str(output_dir),
        "-f", "md",
        "--language", "ch",
        "--timeout", "600",
    ]
    if info.get("needs_ocr"):
        cmd.append("--ocr")

    result = run_tool(cmd, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"extract 失败: {result.stderr[:200]}")
    _collect_mineru_output(output_dir, pdf_path.stem)


def _collect_mineru_output(output_dir: Path, stem: str):
    """MinerU creates output in subdirectories. Find and move .md to target."""
    # MinerU output patterns:
    #   flash: {output_dir}/{stem}/flash_extract.md  or  {output_dir}/flash_extract.md
    #   extract: {output_dir}/{stem}/{stem}.md  or  {output_dir}/{stem}.md
    target = output_dir / f"{stem}.md"

    # If already at the right place, done
    if target.exists():
        return

    # Search for the output file
    candidates = list(output_dir.glob(f"**/{stem}.md")) + \
                 list(output_dir.glob("**/flash_extract.md")) + \
                 list(output_dir.glob("**/*.md"))

    for md_file in candidates:
        if md_file == target:
            continue
        if md_file.stat().st_size > 0:
            shutil.move(str(md_file), str(target))
            # Clean up empty dirs left by mineru
            parent = md_file.parent
            if parent != output_dir and not any(parent.iterdir()):
                parent.rmdir()
            return

    raise RuntimeError(f"MinerU 输出未找到: 在 {output_dir} 中未搜索到 .md 文件")


def _convert_via_split(pdf_path: Path, output_dir: Path, info: dict):
    """Split large PDF, convert chunks, then merge."""
    print(f"  拆分 PDF（{info['pages']}页）...")
    temp_dir = Path(tempfile.mkdtemp(prefix="pdf_split_"))

    try:
        chunks = split_pdf(pdf_path, temp_dir, chunk_pages=200)
        print(f"  → {len(chunks)} 个分卷")

        merged_parts = []
        for i, chunk_path in enumerate(chunks, 1):
            print(f"  转换分卷 {i}/{len(chunks)}: {chunk_path.name}...")
            chunk_output_dir = temp_dir / f"chunk_{i}"
            chunk_output_dir.mkdir(exist_ok=True)

            _convert_via_mineru_extract(chunk_path, chunk_output_dir, info)
            chunk_md = chunk_output_dir / f"{chunk_path.stem}.md"

            # Collect from mineru subdirectory
            candidates = list(chunk_output_dir.glob("**/*.md"))
            if candidates and candidates[0] != chunk_md:
                shutil.move(str(candidates[0]), str(chunk_md))

            if chunk_md.exists():
                text = chunk_md.read_text(encoding="utf-8")
                merged_parts.append(f"\n\n<!-- 分卷 {i}/{len(chunks)} -->\n\n{text}")

        # Write merged output
        output_md = output_dir / f"{pdf_path.stem}.md"
        output_md.write_text("".join(merged_parts), encoding="utf-8")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _convert_via_markitdown(pdf_path: Path, output_md: Path):
    """Convert using Microsoft markitdown CLI."""
    cmd = ["markitdown", str(pdf_path), "-o", str(output_md)]
    result = run_tool(cmd, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"markitdown 失败: {result.stderr[:200]}")


def _convert_via_pymupdf(pdf_path: Path, output_md: Path):
    """Local extraction using PyMuPDF. No network, fallback only."""
    pages = extract_text_pymupdf(pdf_path)
    lines = []
    for num in sorted(pages.keys()):
        text = pages[num].strip()
        lines.append(f"\n<!-- page {num} -->\n")
        if text:
            lines.append(text)

    output_md.write_text("".join(lines), encoding="utf-8")


# ── Metadata Header ───────────────────────────────────────────────────

def _add_metadata_header(md_path: Path, source_pdf: Path, tool: str, info: dict):
    """Prepend a metadata comment header to the markdown file."""
    source_name = source_pdf.name
    header = (
        f"<!--\n"
        f"  source: {source_name}\n"
        f"  tool: {tool}\n"
        f"  pages: {info.get('pages', '?')}\n"
        f"  size_mb: {info.get('size_mb', '?')}\n"
        f"  is_scanned: {info.get('is_scanned', '?')}\n"
        f"  converted: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"-->\n\n"
    )
    content = md_path.read_text(encoding="utf-8")
    md_path.write_text(header + content, encoding="utf-8")


# ── State Management ──────────────────────────────────────────────────

def _load_state(output_dir: Path) -> dict:
    """Load conversion state from .convert_state.json."""
    state_path = output_dir / STATE_FILE_NAME
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {"files": {}}
    return {"files": {}}


def _update_state(output_dir: Path, rel_path: str, md5_hash: str, result: dict):
    """Update conversion state for a single file."""
    state = _load_state(output_dir)
    state["files"][rel_path] = {
        "output": str(Path(result.get("output_path", "")).name) if result.get("output_path") else "",
        "status": result["status"],
        "tool": result.get("tool_used", ""),
        "pages": result.get("pages", 0),
        "size_mb": result.get("size_mb", 0),
        "duration_s": result.get("duration_s", 0),
        "md5": md5_hash,
        "converted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    state_path = output_dir / STATE_FILE_NAME
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Report ────────────────────────────────────────────────────────────

def print_summary(results: list[dict]):
    """Print a conversion summary."""
    total = len(results)
    success = sum(1 for r in results if r["status"] == "success")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    dry_run = sum(1 for r in results if r["status"] == "dry_run")
    failed = sum(1 for r in results if r["status"] == "failed")
    fallback = sum(1 for r in results if r.get("fallback"))

    print("\n" + "=" * 50)
    print("  转换汇总")
    print("=" * 50)
    print(f"  总计:    {total}")
    if dry_run:
        print(f"  待转换:  {dry_run}")
    else:
        print(f"  成功:    {success}")
        if fallback:
            print(f"  降级:    {fallback}")
        print(f"  跳过:    {skipped}")
        if failed:
            print(f"  失败:    {failed}")

    if failed:
        print("\n  失败文件:")
        for r in results:
            if r["status"] == "failed":
                name = Path(r["pdf_path"]).name
                print(f"    ✗ {name}: {r.get('error', '未知错误')}")

    if fallback:
        print("\n  降级文件:")
        for r in results:
            if r.get("fallback"):
                name = Path(r["pdf_path"]).name
                print(f"    ⚠ {name} → {r['tool_used']}: {r.get('fallback_reason', '')}")

    if not dry_run and success:
        print(f"\n  输出目录: {{PDF文件名}}md/")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="案卷 PDF → Markdown 转换工具（MinerU 优先，自动降级）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 convert_pdfs.py a.pdf b.pdf
  python3 convert_pdfs.py --case "陈文舒非法吸收公众存款案202604"
  python3 convert_pdfs.py --force a.pdf
  python3 convert_pdfs.py --dry-run a.pdf
        """,
    )
    parser.add_argument(
        "paths", nargs="*",
        help="一个或多个 PDF 文件路径",
    )
    parser.add_argument(
        "--case", type=str, default=None,
        help="案件文件夹名，自动扫描其下所有案卷 PDF",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="强制重新转换（默认跳过已转换且源文件未变的 PDF）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅列出待转换文件，不实际执行",
    )
    args = parser.parse_args()

    if not args.paths and not args.case:
        parser.print_help()
        sys.exit(1)

    # Discover files
    pdf_files = discover_files(
        pdf_paths=args.paths if args.paths else None,
        case_name=args.case,
    )

    if not pdf_files:
        print("未找到需要转换的 PDF 文件")
        sys.exit(0)

    print(f"\n找到 {len(pdf_files)} 个 PDF 文件\n")

    # Convert each file
    results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        name = pdf_path.name
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        print(f"[{i}/{len(pdf_files)}] {name} ({size_mb:.1f}MB)")

        result = convert_one(
            pdf_path=pdf_path,
            force=args.force,
            dry_run=args.dry_run,
        )

        # Print per-file result
        status = result["status"]
        if status == "success":
            dur = result.get("duration_s", 0)
            tool = result.get("tool_used", "")
            fb = " (降级)" if result.get("fallback") else ""
            print(f"  ✓ {tool}{fb}  {dur:.1f}s → {Path(result.get('output_path', '')).name}")
        elif status == "skipped":
            print(f"  ⊘ {result.get('reason', '已跳过')}")
        elif status == "dry_run":
            print(f"  → {Path(result.get('output_path', '')).name}")
        elif status == "failed":
            print(f"  ✗ {result.get('error', '未知错误')}")
        print()

        results.append(result)

    print_summary(results)


if __name__ == "__main__":
    main()

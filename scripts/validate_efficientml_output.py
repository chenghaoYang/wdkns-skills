#!/usr/bin/env python3
"""Validate and recompile one EfficientML lecture PDF deliverable."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp")
FORBIDDEN_PATTERNS = {
    "[cite] placeholder": re.compile(r"\[cite\]", re.IGNORECASE),
    "TODO marker": re.compile(r"\bTODO\b", re.IGNORECASE),
    "template placeholder": re.compile(
        r"在此填写|Replace this block|请在生成笔记时填入|正文内容开始", re.IGNORECASE
    ),
}


class ValidationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--min-pages", type=int, default=5)
    parser.add_argument("--min-figures", type=int, default=4)
    return parser.parse_args()


def command_output(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stdout[-5000:]}"
        )
    return result.stdout


def discover_single(directory: Path, suffix: str) -> Path:
    files = sorted(path for path in directory.glob(f"*{suffix}") if path.is_file())
    if len(files) != 1:
        raise ValidationError(
            f"Expected exactly one top-level {suffix} in {directory}; found {len(files)}: {files}"
        )
    return files[0]


def resolve_graphic(tex_dir: Path, raw: str) -> Path | None:
    raw = raw.strip()
    candidate = tex_dir / raw
    if candidate.exists():
        return candidate.resolve()
    if candidate.suffix:
        return None
    for extension in IMAGE_EXTENSIONS:
        extended = candidate.with_suffix(extension)
        if extended.exists():
            return extended.resolve()
    return None


def pdf_page_count(pdfinfo: str) -> int:
    match = re.search(r"^Pages:\s+(\d+)\s*$", pdfinfo, re.MULTILINE)
    if not match:
        raise ValidationError("pdfinfo did not report a page count")
    return int(match.group(1))


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    deliverables = run_dir / "deliverables"
    validation_dir = run_dir / "validation"
    build_dir = validation_dir / "xelatex-build"
    rendered_dir = validation_dir / "rendered-pages"
    report_path = validation_dir / "validation-report.json"
    validation_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "run_dir": str(run_dir),
        "status": "failed",
        "errors": [],
        "checks": {},
    }

    try:
        lecture_data = json.loads(
            (run_dir / "lecture.json").read_text(encoding="utf-8")
        )
        expected_video_url = lecture_data["lecture"]["video_url"]

        tex_path = discover_single(deliverables, ".tex")
        delivered_pdf = discover_single(deliverables, ".pdf")
        if tex_path.stem != delivered_pdf.stem:
            raise ValidationError(
                f"Final TeX/PDF basenames differ: {tex_path.name} vs {delivered_pdf.name}"
            )

        tex = tex_path.read_text(encoding="utf-8")
        required_tokens = [
            r"\documentclass",
            r"\begin{document}",
            r"\end{document}",
            r"\section{总结与延伸}",
        ]
        missing_tokens = [token for token in required_tokens if token not in tex]
        if missing_tokens:
            raise ValidationError(f"Missing required LaTeX tokens: {missing_tokens}")

        section_count = len(re.findall(r"\\section\s*\{", tex))
        subsection_count = len(re.findall(r"\\subsection\s*\{", tex))
        summary_count = tex.count("本章小结")
        if section_count < 3 or subsection_count < 3 or summary_count < 2:
            raise ValidationError(
                "Document structure is too thin: "
                f"sections={section_count}, subsections={subsection_count}, summaries={summary_count}"
            )
        if expected_video_url not in tex:
            raise ValidationError(
                "The exact lecture video URL is missing from the final TeX"
            )

        forbidden_hits = [
            name for name, pattern in FORBIDDEN_PATTERNS.items() if pattern.search(tex)
        ]
        if forbidden_hits:
            raise ValidationError(f"Forbidden placeholders remain: {forbidden_hits}")

        graphic_refs = re.findall(
            r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}", tex
        )
        if len(graphic_refs) < args.min_figures:
            raise ValidationError(
                f"Expected at least {args.min_figures} included graphics; found {len(graphic_refs)}"
            )
        missing_graphics = [
            raw for raw in graphic_refs if resolve_graphic(tex_path.parent, raw) is None
        ]
        if missing_graphics:
            raise ValidationError(f"Missing includegraphics assets: {missing_graphics}")

        provenance_count = len(
            re.findall(r"\d{2}:\d{2}:\d{2}\s*--\s*\d{2}:\d{2}:\d{2}", tex)
        )
        if provenance_count < 1:
            raise ValidationError(
                "No concrete HH:MM:SS--HH:MM:SS figure provenance was found"
            )

        for executable in ("xelatex", "pdfinfo", "pdftoppm"):
            if shutil.which(executable) is None:
                raise ValidationError(f"Required executable is missing: {executable}")

        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True)
        xelatex = [
            "xelatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-output-directory={build_dir}",
            tex_path.name,
        ]
        first_log = command_output(xelatex, cwd=tex_path.parent)
        second_log = command_output(xelatex, cwd=tex_path.parent)
        (validation_dir / "xelatex-pass-1.log").write_text(
            first_log, encoding="utf-8"
        )
        (validation_dir / "xelatex-pass-2.log").write_text(
            second_log, encoding="utf-8"
        )

        rebuilt_pdf = build_dir / f"{tex_path.stem}.pdf"
        if not rebuilt_pdf.exists():
            raise ValidationError(f"XeLaTeX did not produce {rebuilt_pdf}")

        delivered_info = command_output(["pdfinfo", str(delivered_pdf)])
        rebuilt_info = command_output(["pdfinfo", str(rebuilt_pdf)])
        (validation_dir / "delivered-pdfinfo.txt").write_text(
            delivered_info, encoding="utf-8"
        )
        (validation_dir / "rebuilt-pdfinfo.txt").write_text(
            rebuilt_info, encoding="utf-8"
        )
        pages = pdf_page_count(rebuilt_info)
        if pages < args.min_pages:
            raise ValidationError(
                f"PDF is too short: {pages} pages; minimum is {args.min_pages}"
            )

        if rendered_dir.exists():
            shutil.rmtree(rendered_dir)
        rendered_dir.mkdir(parents=True)
        representative_pages = sorted({1, max(1, (pages + 1) // 2), pages})
        rendered_files: list[str] = []
        for page in representative_pages:
            prefix = rendered_dir / f"page-{page:03d}"
            command_output(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-png",
                    "-r",
                    "144",
                    str(rebuilt_pdf),
                    str(prefix),
                ]
            )
            rendered = prefix.with_suffix(".png")
            if not rendered.exists() or rendered.stat().st_size == 0:
                raise ValidationError(f"Representative page did not render: {page}")
            rendered_files.append(str(rendered.relative_to(run_dir)))

        source_map = deliverables / "source-map.md"
        qa_report = deliverables / "qa-report.md"
        if not source_map.exists() or source_map.stat().st_size < 200:
            raise ValidationError("deliverables/source-map.md is missing or too small")
        if not qa_report.exists() or qa_report.stat().st_size < 200:
            raise ValidationError("deliverables/qa-report.md is missing or too small")

        report["status"] = "passed"
        report["checks"] = {
            "tex": str(tex_path.relative_to(run_dir)),
            "delivered_pdf": str(delivered_pdf.relative_to(run_dir)),
            "rebuilt_pdf": str(rebuilt_pdf.relative_to(run_dir)),
            "pages": pages,
            "sections": section_count,
            "subsections": subsection_count,
            "section_summaries": summary_count,
            "included_graphics": len(graphic_refs),
            "timestamp_provenance_entries": provenance_count,
            "rendered_pages": representative_pages,
            "rendered_files": rendered_files,
        }
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        report["errors"].append(str(exc))
        write_report(report_path, report)
        print(f"[validate] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

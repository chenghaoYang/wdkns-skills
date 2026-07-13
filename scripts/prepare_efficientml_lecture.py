#!/usr/bin/env python3
"""Prepare one MIT 6.5940 EfficientML lecture for a multi-agent Codex render.

This script deliberately performs all network acquisition before Codex starts. The
Codex job can therefore run with a workspace-only permission profile and treat the
source directory as immutable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    REPO_ROOT / "courses" / "mit-6.5940-efficientml-2023" / "manifest.json"
)
DEFAULT_RUN_ROOT = REPO_ROOT / ".runs" / "mit-6.5940-efficientml-2023"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".ass"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class PreparationError(RuntimeError):
    """Raised when a required lecture source cannot be prepared."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--lecture",
        required=True,
        help="Lecture number or zero-padded id, for example 1 or 01.",
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument(
        "--cookies-file",
        type=Path,
        help="Optional Netscape-format YouTube cookies file.",
    )
    parser.add_argument("--max-height", type=int, default=1080)
    parser.add_argument(
        "--skip-downloads",
        action="store_true",
        help="Create metadata/prompts only. Intended for static tests.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PreparationError(f"Manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PreparationError(f"Invalid JSON in {path}: {exc}") from exc


def select_lecture(manifest: dict[str, Any], requested: str) -> dict[str, Any]:
    normalized = str(int(requested)) if requested.isdigit() else requested.strip()
    for lecture in manifest.get("lectures", []):
        if normalized in {str(lecture["number"]), str(int(lecture["id"]))}:
            return lecture
    raise PreparationError(f"Lecture {requested!r} is not present in the manifest")


def ensure_directories(run_dir: Path) -> dict[str, Path]:
    paths = {
        "run": run_dir,
        "source": run_dir / "source",
        "video": run_dir / "source" / "video",
        "slides": run_dir / "source" / "slides",
        "work": run_dir / "work",
        "agents": run_dir / "work" / "agents",
        "deliverables": run_dir / "deliverables",
        "logs": run_dir / "logs",
        "validation": run_dir / "validation",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def dropbox_download_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["dl"] = ["1"]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def download_file(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 1024:
        print(f"[prepare] Reusing {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126.0 Safari/537.36"
            )
        },
    )
    print(f"[prepare] Downloading {url} -> {destination}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if temporary.stat().st_size <= 1024:
            raise PreparationError(f"Downloaded file is unexpectedly small: {temporary}")
        temporary.replace(destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise PreparationError(
            f"Failed to download {url} to {destination}: {exc}"
        ) from exc

def run_streaming(command: list[str], log_path: Path, cwd: Path | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    printable = " ".join(command)
    print(f"[prepare] Running: {printable}")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {printable}\n")
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        return_code = process.wait()
    if return_code != 0:
        raise PreparationError(
            f"Command failed with exit code {return_code}; inspect {log_path}"
        )


def yt_dlp_command(
    lecture: dict[str, Any],
    video_dir: Path,
    max_height: int,
    cookies_file: Path | None,
) -> list[str]:
    output_template = str(video_dir / "%(id)s.%(ext)s")
    format_selector = (
        f"bv*[height<={max_height}]+ba/"
        f"b[height<={max_height}]/best[height<={max_height}]/best"
    )
    command = [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--newline",
        "--no-progress",
        "--retries",
        "10",
        "--fragment-retries",
        "10",
        "--extractor-retries",
        "5",
        "--concurrent-fragments",
        "4",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails",
        "jpg",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*,en",
        "--sub-format",
        "vtt",
        "--merge-output-format",
        "mp4",
        "--js-runtimes",
        "node",
        "--remote-components",
        "ejs:github",
        "--format",
        format_selector,
        "--output",
        output_template,
    ]
    if cookies_file:
        command.extend(["--cookies", str(cookies_file.resolve())])
    command.append(lecture["video_url"])
    return command


def files_with_extensions(directory: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )


def relative_paths(paths: Iterable[Path], base: Path) -> list[str]:
    return [str(path.relative_to(base)) for path in paths]


def build_agents_md(course: dict[str, Any], lecture: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""\
        # Multi-agent working agreement

        You are rendering **{course['course_number']} Lecture {lecture['number']}: {lecture['title_en']}**
        into a complete Chinese LaTeX note set and PDF.

        ## Non-negotiable rules

        - Use the installed `$youtube-render-pdf` skill as the governing production standard.
        - The files under `source/` and `lecture.json` are immutable evidence. Never edit, rename, or delete them.
        - Network access is neither required nor allowed. All authoritative video, subtitles, metadata, cover art, and official slides are local.
        - All prose is Chinese unless an English technical term materially improves precision.
        - Do not use OCR as a substitute for direct visual inspection. Inspect frames and rendered slide pages with the image-viewing tool.
        - Never invent a formula, benchmark number, quote, timestamp, diagram label, or speaker claim.
        - Raw video frames need subtitle-aligned time provenance in the final LaTeX. Official slide crops must be identified as slide-derived.
        - Do not emit `[cite]`, `TODO`, template placeholders, or unfinished sections.

        ## Write isolation

        Each subagent owns exactly one directory under `work/agents/<role-or-segment>/` and may write only there.
        Agents may read all source files and other agents' completed outputs, but must not overwrite another agent's files.
        Only the coordinator may write to `deliverables/` and assemble the final document.

        ## Definition of done

        `deliverables/` contains exactly one top-level final `.tex`, one matching compiled `.pdf`, an `assets/`
        directory with every referenced figure, `source-map.md`, and `qa-report.md`. The PDF has been compiled twice,
        checked with `pdfinfo`, and visually inspected after rendering representative pages to images.
        """
    )


def build_task_md(
    course: dict[str, Any], lecture: dict[str, Any], inventory: dict[str, Any]
) -> str:
    subtitle_note = (
        "Timestamped subtitles are available locally."
        if inventory.get("subtitles")
        else "No subtitle file was acquired; use the local video audio/visuals and official slides, and document this limitation."
    )
    return textwrap.dedent(
        f"""\
        $youtube-render-pdf

        立即执行完整生产任务，不要只给计划，也不要停在中间草稿。用户已明确要求使用多个 subagents；
        你必须实际调用多 Agent 工具，并由主协调 Agent 负责最终集成。

        # 任务

        将 MIT {course['course_number'].replace('MIT ', '')}（{course['term']}）第 {lecture['number']} 讲
        **{lecture['title_en']} / {lecture['title_zh']}** 制作为完整、可教学、图文对齐的中文 LaTeX 讲义和最终 PDF。

        - 视频：{lecture['video_url']}
        - 官方课程页：{course['course_page']}
        - 课程章节：{lecture['chapter']}
        - 授课日期：{lecture['date']}
        - 本地证据目录：`source/`
        - 本地课次描述：`lecture.json`
        - 素材状态：{subtitle_note}

        视频中的真实教学过程是第一事实来源；官方 slides 用于校对公式、结构、图表和专有名词，不能代替对视频的理解。

        # 必须采用的多 Agent 编排

        主协调 Agent 先读取 `AGENTS.md`、`lecture.json`、素材清单、视频 metadata 与字幕，再按以下波次执行：

        1. **outline/source-audit agent**：审计素材完整性；建立全局目录、术语表、符号表、时间分段和跨段依赖。
        2. **4--6 个 segment writer agents**：按视频章节或连贯时间窗切分；相邻段保留 30--60 秒重叠。
           每个 writer 直接产出可集成的 `section_*.tex`，并附教学目标、核心机制、公式/代码、证据时间区间、
           必要图像候选及未决疑点。不得只写摘要。
        3. **figure agent**：高召回抽取候选帧，制作 contact sheet 后逐张直接看图，选择完全展开且可读的关键帧；
           必要时裁剪或用 TikZ/PGFPlots/矢量 PDF 重绘。记录每张视频图的具体时间区间和每张课件图的页码。
        4. **math-and-code verifier agent**：逐项核对公式、符号、复杂度、算法步骤、代码与 benchmark 数字，反馈可执行修订。
        5. **consistency editor agent**：检查重复定义、术语漂移、符号冲突、章节衔接、图文错位与内容重复。
        6. 主协调 Agent 完成首版整合后，必须再启动一个独立的 **recall reviewer agent**，基于原始字幕、视频时间轴和
           官方 slides 检查重要细节、有趣例子、限制、反例、实验结论与讲者强调是否漏召回。reviewer 只反馈、不改文件；
           主协调 Agent 根据反馈修订，并可继续与 reviewer 交互，直到其认为信息覆盖已充分。

        同一波次中可并行；不同 agent 必须遵守 `work/agents/<role>/` 的写隔离。不要让多个 agent 同时修改最终 `.tex`。

        # 写作与视觉标准

        - 严格遵守 `$youtube-render-pdf` 的教学结构、公式解释、代码块、高价值盒子、配图、时间脚注和总结要求。
        - 不是逐字字幕，也不是压缩摘要。每个大节要从动机到直觉、机制、推导/证据、例子、局限和本章小结。
        - 首页使用本地官方视频封面；正文纳入所有对理解必要的公式、架构图、流程、表格、曲线和关键帧。
        - 对渐进式 PPT/动画必须寻找信息完整的最终状态；先高召回过采样，再筛除重复或低信息帧。
        - 对公式先用自然语言说明“为什么出现、表达什么”，再展示公式并逐个解释符号。
        - 最后必须有 `\\section{{总结与延伸}}`，包含讲者的实质性收束、全课概念压缩、实践含义、局限和开放问题。
        - 不得出现未经来源支持的扩写；额外推论必须明确标为讲义整理者的综合，并保持与原视频一致。

        # 文件与最终验证

        仅主协调 Agent 写入 `deliverables/`：

        - 一个中文合理命名的完整 `.tex`（从 `\\documentclass` 到 `\\end{{document}}`）；
        - 同名最终 `.pdf`；
        - `deliverables/assets/` 下的封面、视频帧、裁剪图、重绘图；
        - `source-map.md`：章节到视频时间段/课件页的映射；
        - `qa-report.md`：多 Agent 分工、reviewer 反馈闭环、编译与视觉检查结果、已知限制。

        完成前必须：

        1. 用 XeLaTeX 连续编译至少两次并修复全部致命错误；
        2. 用 `pdfinfo` 检查页数和元数据；
        3. 用 `pdftoppm` 渲染首页、中间页、末页以及所有可疑页，再用图像查看工具直接检查乱码、空白、溢出、
           图像裁切、不可读公式和 footnote/figure 分页；
        4. 确认所有 `\\includegraphics` 路径存在，且最终 LaTeX 不含 `[cite]`、TODO 或模板占位符；
        5. 从当前 lecture 工作目录运行 `python3 ../../../scripts/validate_efficientml_output.py --run-dir .`，修到通过为止。

        最终回复只需简洁报告完成情况、文件路径、页数与验证结果；真正交付物必须已经落盘。
        """
    )


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    manifest = load_json(manifest_path)
    lecture = select_lecture(manifest, args.lecture)
    course = manifest["course"]

    run_root = args.run_root.resolve()
    run_dir = run_root / f"lecture-{lecture['id']}-{lecture['slug']}"
    paths = ensure_directories(run_dir)

    if args.cookies_file and not args.cookies_file.exists():
        raise PreparationError(f"Cookies file does not exist: {args.cookies_file}")

    lecture_record = {
        "schema_version": 1,
        "course": course,
        "lecture": lecture,
        "source_policy": {
            "video_is_primary": True,
            "official_slides_are_secondary": True,
            "network_access_during_agent_run": False,
        },
    }
    (run_dir / "lecture.json").write_text(
        json.dumps(lecture_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    slides_path = paths["slides"] / f"lecture-{lecture['id']}-official-slides.pdf"
    if not args.skip_downloads:
        download_file(dropbox_download_url(lecture["slides_url"]), slides_path)
        if slides_path.read_bytes()[:5] != b"%PDF-":
            raise PreparationError(
                f"Official slides download is not a PDF (possible HTML error page): {slides_path}"
            )
        if shutil.which("yt-dlp") is None:
            raise PreparationError("yt-dlp is required but was not found on PATH")
        run_streaming(
            yt_dlp_command(lecture, paths["video"], args.max_height, args.cookies_file),
            paths["logs"] / "source-download.log",
            cwd=run_dir,
        )

    videos = files_with_extensions(paths["video"], VIDEO_EXTENSIONS)
    subtitles = files_with_extensions(paths["video"], SUBTITLE_EXTENSIONS)
    thumbnails = files_with_extensions(paths["video"], IMAGE_EXTENSIONS)
    info_json = sorted(paths["video"].glob("*.info.json"))
    slides = [slides_path] if slides_path.exists() else []

    if not args.skip_downloads:
        if not slides:
            raise PreparationError("Official slides were not downloaded")
        if not videos:
            raise PreparationError(
                "No local video was produced. YouTube may require fresh cookies; "
                "configure YT_DLP_COOKIES_B64 and rerun."
            )
        if not thumbnails:
            raise PreparationError("No official video thumbnail was produced")
        if not info_json:
            raise PreparationError("No yt-dlp info JSON was produced")

    inventory = {
        "ready": not args.skip_downloads,
        "lecture_id": lecture["id"],
        "run_dir": str(run_dir),
        "slides": relative_paths(slides, run_dir),
        "videos": relative_paths(videos, run_dir),
        "subtitles": relative_paths(subtitles, run_dir),
        "thumbnails": relative_paths(thumbnails, run_dir),
        "metadata": relative_paths(info_json, run_dir),
        "notes": (
            []
            if subtitles
            else [
                "No subtitles were acquired. The agent must document the limitation and rely on direct video/slide inspection."
            ]
        ),
    }
    (run_dir / "SOURCE_READY.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "AGENTS.md").write_text(
        build_agents_md(course, lecture), encoding="utf-8"
    )
    (run_dir / "TASK.md").write_text(
        build_task_md(course, lecture, inventory), encoding="utf-8"
    )

    print(json.dumps({"run_dir": str(run_dir), "inventory": inventory}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreparationError as exc:
        print(f"[prepare] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

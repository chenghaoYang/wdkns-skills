# MIT 6.5940 EfficientML（2023 Fall）整套 PDF 讲义生产

本目录把官方课程页与 YouTube 播放列表中的 **23 个正式编号讲次**固化为可重复执行的课程 manifest，并用两层并行完成讲义生产：

1. GitHub Actions matrix 按 lecture 隔离任务，默认最多同时运行 2 讲；
2. 每讲内部由 Codex 主协调 Agent 显式启动多个 subagents，分别负责素材审计/目录、分段写作、配图、公式与代码核验、一致性编辑和独立漏召回复核。

章节分隔行、Student Holiday、Thanksgiving 与 final-project presentation 不作为独立课程 PDF。每讲最终交付一个中文 `.tex`、同名 `.pdf`、全部图片资产、来源映射和 QA 报告。

## 固定来源

- 官方课程：MIT 6.5940, *TinyML and Efficient Deep Learning Computing*, Fall 2023
- 官方课程页：<https://hanlab.mit.edu/courses/2023-fall-65940>
- YouTube 播放列表：<https://www.youtube.com/playlist?list=PL80kAHvQbh-pT4lCkDT53zT8DKmhE0idB>
- 课次、视频 ID、课件链接与中文标题：[`manifest.json`](manifest.json)

视频教学过程是第一事实来源；官方 slides 用于核对公式、结构、图表和专有名词。素材在 Codex 启动前下载到本地，Agent 阶段不依赖网络。

## GitHub Actions 启动条件

工作流：`.github/workflows/render-efficientml-2023.yml`

仓库必须配置：

- `OPENAI_API_KEY`（必需）：供官方 `openai/codex-action@v1` 调用 Codex；
- `YT_DLP_COOKIES_B64`（建议）：Netscape cookies 文件的 base64，用于降低 GitHub runner 被 YouTube 反爬拦截的概率。该值只写入 runner 临时文件，不进入日志和 artifact。

同仓库 pull request 会自动启动全部 23 讲。`workflow_dispatch` 可选择 `all` 或单独重跑某一讲，实现课次级断点续跑。工作流 artifact 名称为：

```text
efficientml-<两位讲次>-<slug>
```

每个 artifact 只上传交付物、验证报告和日志，不上传原始视频。

## 本地单讲运行

先把 skill 安装到 Codex，再准备素材：

```bash
mkdir -p ~/.codex/skills
cp -R skills/youtube-render-pdf ~/.codex/skills/

python3 scripts/prepare_efficientml_lecture.py \
  --lecture 01 \
  --cookies-file /path/to/youtube-cookies.txt
```

脚本会输出工作目录，例如：

```text
.runs/mit-6.5940-efficientml-2023/lecture-01-introduction
```

在该目录启动 Codex，并把 `TASK.md` 作为提示。完成后验证：

```bash
python3 scripts/validate_efficientml_output.py \
  --run-dir .runs/mit-6.5940-efficientml-2023/lecture-01-introduction
```

验证器会重新执行两遍 XeLaTeX、检查结构/占位符/图片路径/时间来源、读取 `pdfinfo`，并用 `pdftoppm` 渲染首页、中间页和末页到 `validation/rendered-pages/`。

## 目录约定

```text
.runs/mit-6.5940-efficientml-2023/lecture-XX-slug/
├── AGENTS.md
├── TASK.md
├── lecture.json
├── SOURCE_READY.json
├── source/                 # 只读：视频、字幕、封面、metadata、官方 slides
├── work/agents/            # 各 subagent 独占目录
├── deliverables/           # 最终 tex/pdf/assets/source-map/qa-report
├── validation/             # 重编译、pdfinfo、代表页渲染、JSON 报告
└── logs/
```

所有 `.runs/` 内容均为生成物，不提交到 Git。

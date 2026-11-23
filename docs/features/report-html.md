# 飞书同源 HTML 报告

本功能把飞书推送的同源内容，以一份排版精美的自包含 HTML 呈现在分析报告中。用户可在任意历史报告（个股、决策仪表盘记录、大盘复盘）的分享菜单里选择「查看 HTML 报告」或「下载 HTML」。

## 能力边界

- HTML 与飞书卡片**信息一致**：两者共用同一份文档解析（`src/notification_sender/feishu_card.py` 的 `parse_report_document()`），不另写一套 Markdown 解析。飞书里展开为字段网格的表格，HTML 同样以字段网格呈现。
- **固定全中文**：HTML 报告不跟随记录语言或 Web 界面语言，文档固定 `<html lang="zh">`。en/ko 记录会使用持久化的中文变体（字段覆盖表 / 大盘复盘中文 Markdown），首次打开时可按需生成并缓存。Web 端「查看 HTML 报告 / 下载 HTML」入口与抽屉文案也固定中文。
- HTML 在视觉上比飞书更丰富：宽屏渐变标题栏、语义色板块标签、双列字段卡片、风险/利好高亮、引用块、页脚声明，支持系统深浅色与打印。
- 单文件、零脚本、零外部资源：CSS 内联，可离线打开，可作为邮件附件，也可在飞书内置浏览器查看。
- **不做**：把 HTML 链接推回飞书、多股聚合日报的独立 HTML、生成报告时落盘 `.html`、交互式图表/K 线、手动主题切换。这些能力如需引入，另开任务。

## 数据流

```text
报告 Markdown（get_markdown_report，与推送同源）
  -> format_feishu_markdown()             （与飞书发送一致）
  -> parse_report_document()              （与飞书卡片共用解析）
  -> render_report_html()                 （src/services/report_html）
  -> 自包含 HTML 字符串
```

en/ko 记录在进入上述链路前，会先由 `src/services/report_translation_service.py` 生成中文变体，持久化到 `report_text_variants`：

```text
analysis_history.raw_result
  -> collect_translatable_texts()         （只挑自然语言字段）
  -> 中文翻译（复用现有 LLM 后端）
  -> report_text_variants.payload_json    （overlay / market_review_markdown）
  -> get_markdown_report_zh()             （中文标签 + 中文正文）
```

渲染器不读取 `structured_payload`，也不补造飞书里没有的数据，因此「信息与飞书一致」由同一份解析保证，并有自动化测试对照飞书卡片的章节标题与字段键值。

## 接口

```text
GET /api/v1/history/{record_id}/report-html
    ?download=1    可选，存在且为 1 时以附件形式下载

POST /api/v1/history/{record_id}/report-zh-variant
    ?force=1       可选，忽略缓存重新生成
```

- 200：返回 `text/html; charset=utf-8`，响应头含 `Cache-Control: no-store`、`Content-Security-Policy: default-src 'none'; img-src data:; style-src 'unsafe-inline'`、`X-Content-Type-Options: nosniff`。
- 404：记录不存在或报告内容为空。
- 409：en/ko 记录尚未生成中文变体（`zh_variant_missing`），Web 端据此展示「生成中文版本」按钮并调用 POST 接口。
- 413：报告超过 500,000 字符（独立上限，不沿用分享图的 `MARKDOWN_TO_IMAGE_MAX_CHARS`）。
- 500 / 503：渲染失败或中文变体生成失败，返回通用错误体，响应不含异常细节。
- 中文记录直接使用正常中文链路；en/ko 记录必须命中已就绪且 `source_hash` 一致的中文变体。缺少 `report_language` 的历史记录按正文内容检测（英文/韩文/英文标题、表格、短句），不会回退成中英混排。
- 下载文件名 `dsa-report-{id}.html`，ID 经字符白名单过滤，避免路径注入。
- 鉴权与同 router 其他历史接口一致，不新增例外。

Web 端用法：历史报告页面的「分享」菜单 → 查看 HTML 报告（抽屉内预览）/ 下载 HTML（直接下载）。点击才请求，页面加载时不预取。若返回 409，抽屉显示「该历史报告尚未生成中文版本」与「生成中文版本」按钮，生成完成后自动重新加载；下载动作会先幂等调用 `POST /report-zh-variant`，再下载文件。桌面端复用同一 HTTP 接口，无需额外 IPC。

## 安全边界

- Jinja2 开启自动转义；正文行内片段先 `html.escape(quote=True)` 再按白名单还原少量标记。
- 行内白名单只识别：`**粗体**`、`*斜体*`、`` `代码` ``、`<font color=...>`；颜色只接受受控语义色，其余标签一律当文本。
- 正文 URL 仅在 `http/https` 时渲染为链接，并加 `rel="noopener noreferrer nofollow" target="_blank"`；其他协议按纯文本。
- 输出不含 `<script>`、`on*` 事件属性、外链资源、`<link>`、`@import`、`javascript:` 链接。
- Web 端用 `<iframe sandbox="allow-popups allow-popups-to-escape-sandbox" srcdoc=...>` 展示：不给 `allow-scripts`、不给 `allow-same-origin`，确保 HTML 无法操作宿主页面或读取 DOM。`allow-popups` 仅用于正文外链在新标签打开。
- 渲染失败抛显式异常 `ReportHtmlRenderError`，由接口层转成 500，不做静默降级。

## 本地预览

```python
from pathlib import Path
from src.services.report_html import render_report_html

markdown_text = Path("reports/example.md").read_text(encoding="utf-8")
html = render_report_html(markdown_text)
Path("report-preview.html").write_text(html, encoding="utf-8")
```

样例输出放在 `local/scratch/`（不入库）：

```bash
ls local/scratch/single_stock_report.html
python scripts/check_html_chinese.py local/scratch/single_stock_report.html
```

## 与飞书卡片解析的关系

`build_feishu_card()` 和 `render_report_html()` 共用 `feishu_card.py` 的私有解析函数，通过公开只读入口暴露：`parse_report_document()`、`plan_field_grid()`、`section_tone()`、`parse_field_row()`、`parse_stock_summary()` 等。

> 维护提示：修改 `feishu_card.py` 的章节解析、字段布局、脚注去重或 `_template_for()` 主题映射时，必须同时评估 HTML 报告的影响并跑 `tests/test_report_html_renderer.py`。脚注去重已抽为 `_dedupe_notes()`，卡片与 HTML 共用，避免两处口径漂移。

模板与样式位于 `src/services/report_html/`（不在根目录 `templates/`，因为 Docker 镜像不包含根目录 `templates/`，不能让 HTML 渲染依赖 `REPORT_TEMPLATES_DIR`）。

## 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `REPORT_ZH_VARIANT_ENABLED` | `true` | 是否为 en/ko 报告生成并缓存中文变体 |
| `REPORT_ZH_VARIANT_ON_DEMAND` | `true` | 历史报告打开 HTML 时是否允许按需生成 |
| `REPORT_ZH_VARIANT_TIMEOUT_SECONDS` | `60` | 单次翻译调用超时 |
| `REPORT_ZH_VARIANT_MAX_CHARS` | `200000` | 单条报告允许翻译的最大源字符数 |

不配置也能运行：默认开启、复用当前分析模型。历史记录可批量回填：

```bash
python scripts/backfill_report_zh_variants.py --dry-run --limit 50
python scripts/backfill_report_zh_variants.py --apply --since 2026-01-01
```

## 已知限制

- en/ko 历史记录第一次打开 HTML 时可能需要生成中文变体；生成完成后缓存，后续读取不再调用模型。
- 中文变体按字段覆盖表翻译自然语言，保留股票代码、指标缩写、数字、URL、代码块与公司专名；翻译质量仍需抽样复核。
- 模型生成的原文如果本身是混合语言，中文化结果以翻译服务输出为准。
- 摘要里的 `🟢N 🟡N 🔴N` 指标胶囊依赖飞书摘要行的 emoji 计数格式；没有该格式时不显示指标条，不影响正文。
- 主题跟随系统 `prefers-color-scheme`，不支持手动切换。
- 打印样式针对 A4 优化，长报告按区块分页（`break-inside: avoid`）。

## 回滚

- 代码：回退 T78 对应提交即可；新增接口与中文变体逻辑随之消失。
- 局部降级：关闭 `REPORT_ZH_VARIANT_ENABLED` / `REPORT_ZH_VARIANT_ON_DEMAND`；HTML 接口对缺变体返回 409，不再混排。
- 数据：新增 `report_text_variants` 表；回滚可保留（不再读取）或按迁移删除，既有 `analysis_history` 字段不变。

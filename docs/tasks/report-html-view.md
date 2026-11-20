# 开发任务书 T45：飞书推送内容的精美 HTML 报告呈现

- 编号：T45（接 STATUS.md 中 T44）
- 类型：feat（后端 + Web，少量文档）
- 日期：2026-11-20
- 状态：已确认，待开发（第 11 节待决问题已全部定稿，2026-11-20）
- 本次定稿范围：H0–H5、H9。H6 / H7 / H8 经确认**不在本任务内**，仅保留章节作为后续参考
- 分支建议：`feat/report-html-view`（一个任务一个分支一个 PR，遵循 AGENTS.md 第 8 节）

---

## 1. 背景与目标

### 1.1 需求原文

> 飞书上发的信息，也可以在分析报告中用精美的 HTML 呈现。

### 1.2 需求理解（请确认）

飞书推送现在已经是结构化卡片（标题栏、语义色板块标签、字段网格、脚注），但这套排版只存在于飞书里。Web 端的报告页只能看 Markdown 渲染或 1080px 分享海报（PNG / PDF），两者都不是"飞书那条消息"的原样复现。

目标：**同一份推送内容，在分析报告里能以一份排版精美、自包含的 HTML 打开、查看、下载。** 内容与飞书卡片一致，视觉在 HTML 里可以更丰富（宽屏、深浅色、打印、手机）。

### 1.3 成功标准

1. Web 端任意一条历史报告（个股、大盘复盘）都能一键打开"HTML 报告"视图，也能下载为单文件 `.html`。
2. HTML 里的章节、字段、摘要、脚注与同一报告推送到飞书的卡片**信息一致**（不增不减），有自动化测试对照。
3. HTML 是单文件、零外部依赖、零脚本，离线、邮件附件、飞书内置浏览器都能打开。
4. 不改变飞书发送行为：`build_feishu_card()` 的输出对既有输入逐字节不变。
5. 不配置任何新参数也能用（Web 端按需生成，不新增必填配置）。

---

## 2. 现状（已读代码，作为改动依据）

| 能力 | 位置 | 说明 |
| --- | --- | --- |
| 飞书发送入口 | `src/notification_sender/feishu_sender.py` `send_to_feishu()` | Webhook 优先，其次 App Bot；报告路由时 `include_disclaimer=True` |
| 飞书卡片构建 | `src/notification_sender/feishu_card.py` `build_feishu_card()` | 输入是 `format_feishu_markdown()` 转换后的 lark_md；内部有 `_extract_footer` / `_parse_sections` / `_split_document` / `_extract_summary` / `_parse_field_cells` / `_color_for_title` / `_template_for` |
| lark_md 转换 | `src/formatters.py` `format_feishu_markdown()` | `##`→`▎ **标题**`，`###`→`· **标题**`，三列以上表格展开为 `• 键：值 \| 键：值` |
| 报告 Markdown 来源 | `src/services/history_service.py` `get_markdown_report()` | 与推送内容同源，Web「完整报告」抽屉已使用 |
| 历史接口 | `api/v1/endpoints/history.py` | 已有 `/{id}/markdown`、`/{id}/share-image`、`/{id}/share-image-html`；`_history_share_image_input()` 负责载入记录 + Markdown |
| 分享海报 | `src/share_image.py` `build_share_image_html()` | 1080px 海报，**用途是转 PNG/PDF，不是报告阅读视图**，布局固定、会裁剪信息，不适合复用成报告 HTML |
| Jinja2 报告渲染 | `src/services/report_renderer.py` | `autoescape=select_autoescape(default=False)`；模板在项目根 `templates/`，由 `REPORT_TEMPLATES_DIR` 指定 |
| Web 分享入口 | `apps/dsa-web/src/components/report/ShareReportButton.tsx`、`ReportMarkdownDrawer.tsx`、`api/history.ts` | 分享菜单有"复制链接 / 导出 PDF / 分享图片" |
| 依赖 | `requirements.txt` | 已有 `markdown2`、`jinja2`，**不需要新增依赖** |

### 2.1 已发现的坑（直接影响设计）

1. **Docker 镜像不包含根目录 `templates/`**：`docker/Dockerfile` 只 `COPY` 了 `*.py`、`api/`、`data_provider/`、`bot/`、`src/`、`strategies/`。因此新 HTML 模板**必须放在 `src/` 下**，不能放 `templates/`，否则容器里渲染会静默返回 None。
2. `report_renderer.render()` 的 `autoescape` 为关闭状态，因为它输出 Markdown。HTML 渲染器**必须单独开启转义**，不能照搬。
3. 分享海报链路对内容有 `MARKDOWN_TO_IMAGE_MAX_CHARS`（默认 15000）上限，该上限是为转图设计的，HTML 视图不应沿用。
4. `feishu_card.py` 的解析函数都是私有的（`_` 前缀），直接跨模块 import 私有符号会让两边互相卡死，需要先暴露一个稳定的只读入口（H1）。
5. `docs/ai/STATUS.md` 第 5 节：`src/config.py`、`config_registry.py`、`system_config_service.py` 为不要大改的上游文件。本任务**尽量不新增配置键**（见 H6 的取舍）。

---

## 3. 范围

### 3.1 范围内

- 从飞书同源内容生成"报告 HTML"的渲染器。
- 历史报告的 HTML 接口（在线查看 / 下载）。
- Web 端入口：分享菜单新增"查看 HTML 报告 / 下载 HTML"，视图用沙箱 iframe 展示。
- 深色 / 浅色自适应、移动端、打印样式。
- 个股报告、决策仪表盘、大盘复盘三类内容；中文 / 英文 / 韩文。
- 测试、文档、CHANGELOG。

### 3.2 非目标（本任务不做）

- 不修改飞书卡片的外观和发送逻辑；不新增飞书卡片组件。
- 不替换或改造 1080px 分享海报与 PNG / PDF 导出。
- 不做交互式图表、K 线图、脚本驱动的折叠 / 主题切换（保持零 JS，见 D4）。
- 不新增公网托管、短链、对象存储；不把 HTML 链接推回飞书（列为可选 H7）。
- 不改 Markdown 报告模板（`templates/report_*.j2`）。
- 不改桌面端 Electron 壳（桌面端复用 Web 页面，自动获得入口）。

---

## 4. 关键设计决策

### D1 数据源：用"飞书同款解析结果"作为唯一中间模型

HTML 不另写一套 Markdown 解析，而是复用 `feishu_card` 的文档解析（章节层级、摘要、字段行、脚注）。这样"HTML 与飞书信息一致"由同一份解析代码保证，而不是靠两边各自实现再靠测试对齐。

流程：

```text
报告 Markdown（get_markdown_report，与推送同源）
  -> format_feishu_markdown()            （与飞书发送一致）
  -> feishu_card.parse_report_document() （H1 新增的公开只读入口）
  -> ReportDocument（title / summary / sections / fields / notes）
  -> report_html_renderer.render_report_html()
  -> 自包含 HTML 字符串
```

备选方案（不采用）：直接解析原始 Markdown。优点是能保留真实表格；缺点是第二套解析，长期会与飞书出现信息漂移。如果评审认为"表格要保留"，作为 D1 的变更点，在 11 节待决问题里确认。

### D2 模板与样式放 `src/services/report_html/`

- `src/services/report_html/__init__.py`
- `src/services/report_html/renderer.py`
- `src/services/report_html/templates/report.html.j2`
- `src/services/report_html/templates/_components.html.j2`
- `src/services/report_html/styles.css`（构建时由 renderer 读取并内联，不对外暴露）

原因：Docker 与桌面打包会带上 `src/`，不依赖 `REPORT_TEMPLATES_DIR`，用户自定义 Markdown 模板目录也不会把 HTML 渲染弄坏。

### D3 内容与飞书一致，视觉上可以比飞书更丰富

- 信息集合以飞书卡片为准；表格在飞书里已被展开为字段网格，HTML 同样用字段网格呈现，保持一致。
- 视觉增强只来自排版：标题栏渐变、摘要指标条、板块语义色、双列字段卡片、风险 / 利好高亮、页脚声明、响应式、深浅色、打印。
- 不使用 `structured_payload` 补充飞书里没有的数据（避免"HTML 比飞书多信息"导致对照测试失去意义）。若要做结构化增强，另开 H8，默认不做。

### D4 安全：零脚本 + 全量转义 + 沙箱展示

- Jinja2 `autoescape=True`。所有来自报告正文的文本先 HTML 转义，再由白名单把 `**粗体**`、`*斜体*`、`<font color=red|green|grey|...>` 转成受控的 `<strong>` / `<em>` / `<span class="tone-*">`。其余标签一律当文本。
- 输出 HTML 不含 `<script>`、`on*` 属性、外链资源、外链字体、`<link>`、`<iframe>`、`javascript:` 链接。报告里的 URL 只在 `http/https` 时渲染为 `<a rel="noopener noreferrer nofollow" target="_blank">`，其他协议按纯文本。
- 折叠使用原生 `<details>`；主题跟随 `prefers-color-scheme`；不做需要 JS 的切换。
- 接口响应头沿用 `share-image-html` 的做法：`Content-Security-Policy: default-src 'none'; img-src data:; style-src 'unsafe-inline'`、`X-Content-Type-Options: nosniff`、`Cache-Control: no-store`。
- Web 端用 `<iframe sandbox srcdoc=...>`（不给 `allow-scripts`、不给 `allow-same-origin`）展示；新窗口 / 下载使用 Blob。

### D5 不新增必填配置，也尽量不新增配置键

Web 按需生成，不需要开关。本地落盘 `.html`（H6）如果要做，默认关闭并按 AGENTS.md 第 1 节同步 `.env.example` 与文档；若评审认为不需要落盘，H6 整体删除，不影响主链路。

### D6 涨跌颜色语义

飞书卡片本身只用语义色标签，不涉及红涨绿跌。HTML 中凡是涨跌颜色，一律取报告里已有的颜色标记（`🟢🟡🔴` 与 `<font color>`），不按市场硬编码；与 `docs/share-images.md` 中"不按市场地区硬编码涨跌色"的口径一致。

---

## 5. 任务拆分

依赖顺序：H0 → H1 → H2 → H3 → H4 → H5 → (H6 / H7 可选) → H9。H2 与 H3 可并行。

---

### H0 样本验证与版式草图（0.5 天）

**目标**：待决问题已在第 11 节定稿，本步只验证"解析结果够用"并确定版式，避免返工。

**步骤**

1. 通读第 11 节决策记录，开发期间不再改动这些结论；如发现必须改，先停下来回到评审。
2. 用 3 份真实样本跑一次 `format_feishu_markdown()` + `_parse_sections()`，确认解析结果足以还原：个股单报告、多股决策仪表盘、大盘复盘（样本取 `tests/fixtures/notification_reports/*.md`）。
3. 画出 HTML 低保真版式（可用 `experimental_image_generate` 或手绘），在 PR 描述中附出。

**验收**：待决问题有结论；样本均能解析出标题、摘要、至少一个板块。

---

### H1 暴露飞书文档解析的公开只读入口（0.5 天）

**目标**：让 HTML 渲染器能复用解析结果，同时**不改变飞书卡片输出**。

**改动文件**

- `src/notification_sender/feishu_card.py`（只新增，不改既有函数的行为）

**设计**

新增 dataclass 与函数：

```python
@dataclass(frozen=True)
class ReportDocument:
    title: str                       # 一级标题，可能为空
    summary: str                     # 副标题摘要
    theme: str                       # _template_for() 的结果：blue/indigo/orange/green
    preamble: List[str]              # 标题与第一个板块之间的行
    sections: List[_Section]         # 复用既有 _Section（level/title/lines/divider_before）
    notes: List[str]                 # 去重后的脚注（含统一声明）

def parse_report_document(content: str, *, include_disclaimer: bool = False) -> ReportDocument: ...
def section_tone(title: str) -> str: ...      # 对 _color_for_title 的公开别名
def parse_field_row(line: str) -> Optional[List[Tuple[str, str]]]: ...   # 对 _parse_field_cells 的公开别名
```

要求：

- `parse_report_document` 内部按 `build_feishu_card` 同样的顺序调用 `_extract_footer` → `_parse_sections` → `_split_document` → `_extract_summary`，不复制逻辑。
- 脚注去重 / 统一声明补齐的逻辑从 `_note()` 中抽出共用的 `_dedupe_notes()`，`_note()` 改为调用它；输出必须与改动前一致。
- 不改 `build_feishu_card()` 的签名和返回值。

**测试**

- `tests/test_feishu_card.py` 全部保持通过且不修改断言。
- 新增 `tests/test_feishu_card_document.py`：对 `tests/fixtures/notification_reports/*.md` 的每个样本，断言 `parse_report_document()` 的标题 / 摘要 / 板块标题与 `build_feishu_card()` 里 header 与 `text_tag` 的内容一致。
- 新增快照：对同一批样本，`build_feishu_card()` 输出 JSON 在改动前后相等（改动前先生成基线，再比较）。

**验收**：飞书相关既有测试 0 失败；基线对比无差异。

---

### H2 HTML 渲染器（2–3 天）

**目标**：`ReportDocument` → 自包含 HTML。

**新增文件**

- `src/services/report_html/__init__.py`（导出 `render_report_html`）
- `src/services/report_html/renderer.py`
- `src/services/report_html/templates/report.html.j2`
- `src/services/report_html/templates/_components.html.j2`
- `src/services/report_html/styles.css`

**对外接口**

```python
def render_report_html(
    markdown: str,
    *,
    report_language: str = "zh",          # zh | en | ko，经 normalize_report_language
    page_title: Optional[str] = None,     # 用于 <title>，缺省取文档标题
    generated_at: Optional[str] = None,   # 页脚时间，缺省不显示
    include_disclaimer: bool = True,      # 与飞书报告路由保持一致
) -> str: ...
```

失败策略：**不吞异常**。渲染异常向上抛 `ReportHtmlRenderError`，由接口层转成 500；不要像 `report_renderer.render()` 那样返回 None 掩盖问题（AGENTS.md 8.1 禁止用静默降级掩盖契约）。

**渲染规则**

| 文档元素 | HTML 结构 |
| --- | --- |
| 一级标题 + 摘要 | `<header class="hero theme-{blue|indigo|orange|green}">`，标题 + 副标题 |
| 摘要中的 `🟢N 🟡N 🔴N` | 指标条 `<ul class="kpis">`，三枚胶囊 |
| 二级板块 `▎` | `<section class="block">` + `<h2 class="tag tone-{section_tone(title)}">` |
| 三级小节 `·` | `<h3>`；标题含风险 / 利好关键词时加 `tone-red` / `tone-green` 左边框 |
| `• 键：值 \| 键：值` | `<dl class="fields">` 双列网格，长值 / 风险建议类整行；奇数个短字段末项拉满，规则与 `feishu_card._is_short_field` 一致 |
| 灰色引用 `<font color='grey'>💬` | `<blockquote class="quote">` |
| 普通段落、列表 | `<p>` / `<ul>`，行内走第 D4 节白名单转换 |
| 分隔线 `divider_before` | `<hr>`，连续或开头不出现 |
| 脚注 | `<footer class="notes">`，含统一 AI 声明；声明字号小于正文 |

**行内转换白名单（D4）**

输入先 `html.escape`，再依次替换 `**x**`、`*x*`、`<font color=...>` 的转义形式。`color` 只接受 `red / green / grey / orange / blue / yellow / purple / indigo / turquoise / wathet`，其余忽略标签保留文字。

**样式要求（精美的验收口径）**

- 设计 token：`--bg --surface --text --muted --border --radius --shadow` 与四个主题渐变；十种语义色各一组深 / 浅配色，文字对比度 ≥ 4.5:1。
- 布局：最大宽度 880px，居中；≥ 720px 字段双列，< 720px 单列；标题栏用主题色渐变，不依赖图片。
- 字体：仅系统字体栈，须含 CJK 回退：`system-ui, -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", "Noto Sans KR", sans-serif`；数字使用 `font-variant-numeric: tabular-nums`。
- 深浅色：`@media (prefers-color-scheme: dark)`。
- 打印：`@media print` 隐藏阴影、背景改白、`section { break-inside: avoid }`，A4 可读。
- 无障碍：语义标签、`lang` 属性跟随报告语言、`<title>` 非空、颜色不是唯一信息载体（标签带文字 / emoji）。
- 体积：典型单股报告 HTML ≤ 60KB，10 只股票仪表盘 ≤ 200KB（CSS 内联约占 10KB）。

**测试**（`tests/test_report_html_renderer.py`）

1. 三类样本（个股、多股、大盘复盘）× 三种语言：渲染成功、含 `<!doctype html>`、`<html lang=...>`、非空 `<title>`。
2. **一致性对照**（核心）：对每个样本，取 `build_feishu_card()` 中全部 `text_tag` 标题与 `fields` 的键值文本，断言都出现在 HTML 文本里；反向断言 HTML 的板块标题集合与解析结果相等。
3. **安全**：构造含 `<script>alert(1)</script>`、`<img onerror=...>`、`javascript:` 链接、`<font color="red" onmouseover=...>` 的正文，断言输出中没有可执行结构：不含 `<script`、`onerror=`、`onmouseover=`、`javascript:`。
4. 无外部依赖：输出中不含 `http://` / `https://` 的 `src=`、`href=`（除正文链接）、`<link`、`@import`、`url(http`。
5. 空 / 极短内容、无章节标题内容、10 级长文：不抛异常且有兜底版式（单个 `<section>` 展示全文）。
6. 确定性：同一输入两次输出逐字节相同（`generated_at` 为外部传入，渲染器内部不取当前时间）。

**验收**：以上测试通过；`python -m py_compile` 通过；用浏览器截图三类样本的浅色 / 深色 / 手机宽度（截图放 PR 描述，不入库）。

---

### H3 视觉打磨与样例页（1 天，可与 H2 并行）

**目标**：把"精美"落到可评审的东西上。

**步骤**

1. 在 `local/scratch/report_html/`（不入库）放 3 份样例输出，用浏览器预览。
2. 按下列检查表逐项过：标题栏、指标条、板块标签、字段卡片、风险 / 利好高亮、引用块、脚注、深色、手机宽度 375px、打印预览。
3. 在 PR 描述附：浅色 / 深色 / 手机 / 打印 4 组截图，以及与飞书卡片的并排对比图（AGENTS.md 第 1 节：改报告渲染效果必须附截图）。

**验收**：评审者在 PR 中确认视觉；无横向滚动条；长股票名 / 长数值不撑破布局（需要 `overflow-wrap: anywhere`）。

---

### H4 后端接口（1 天）

**改动文件**

- `api/v1/endpoints/history.py`
- `api/v1/schemas/`（仅在需要时，见下）
- `src/services/history_service.py`（仅在需要把 Markdown 与语言一并返回时）

**新增接口**

```text
GET /api/v1/history/{record_id}/report-html
    ?download=1      可选，存在则 Content-Disposition: attachment
```

行为：

- 复用 `_history_share_image_input()` 载入记录与 Markdown（404、报告生成失败的错误码沿用）。
- 报告语言取记录自身的 `report_language`，缺失回退全局配置，再回退 `zh`。
- 不做 `MARKDOWN_TO_IMAGE_MAX_CHARS` 限制；为防滥用设置独立常量上限（建议 500_000 字符），超限返回 413，错误码 `report_html_too_large`。
- 响应：`text/html; charset=utf-8`，头部同 D4；下载文件名 `dsa-report-{id}.html`。
- 渲染异常：500，`{"error": "generation_failed", "message": "生成 HTML 报告失败"}`；日志记录原因，响应不含异常细节。
- 鉴权：与同一 router 其他接口一致，不新增例外。

兼容性：纯新增路由，不改既有响应字段。OpenAPI 描述需补充 summary / description / responses。

**测试**（`tests/test_history_report_html_api.py`）

- 200：返回 HTML，响应头含 CSP / nosniff / no-store。
- `download=1`：`Content-Disposition` 正确，文件名只含安全字符。
- 404：记录不存在、Markdown 为空。
- 413：超长内容。
- 500：渲染器抛错时返回通用错误体且不泄露异常文本。
- 开启认证时未登录返回 401 / 403（沿用现有认证夹具）。
- 大盘复盘类型记录（`report_type == "market_review"`）可成功返回。

**验收**：接口测试通过；`./scripts/ci_gate.sh` 通过。

---

### H5 Web 端入口与视图（1.5–2 天）

**改动文件**

- `apps/dsa-web/src/api/history.ts`：新增 `getReportHtml(recordId: number): Promise<string>`（`responseType: 'text'`，沿用 `apiClient` 的认证与错误解析）。
- `apps/dsa-web/src/components/report/ShareReportButton.tsx`：分享菜单新增两项"查看 HTML 报告""下载 HTML"。
- 新增 `apps/dsa-web/src/components/report/ReportHtmlDrawer.tsx`：抽屉内用 `<iframe sandbox srcdoc>` 展示，带加载 / 错误态，结构参照 `ReportMarkdownDrawer.tsx`（懒加载、错误边界、取消过期请求）。
- `apps/dsa-web/src/i18n/uiText.ts` 与 `apps/dsa-web/src/utils/reportLanguage.ts`：新增文案，**新文案只进 `uiText.ts`**（STATUS.md 第 3 节规则）；中 / 英 / 韩齐全。
- `apps/dsa-web/src/utils/reportShare.ts`：如需新增 `buildHtmlFilename()`，与 `safeFilenamePart` 复用，不重复实现。
- 样式沿用 `share-report.css`，不新增全局样式。

**交互要求**

1. 菜单项排序：复制链接 → 查看 HTML 报告 → 下载 HTML → 导出 PDF → 分享图片（按依赖从轻到重，HTML 不依赖转图引擎）。
2. 点击才请求，页面加载时不预取。
3. iframe 必须 `sandbox=""`（不加 `allow-scripts` / `allow-same-origin`）；链接点击由 HTML 内 `target="_blank"` 在新标签打开需要 `allow-popups`，如需则只加 `allow-popups allow-popups-to-escape-sandbox`，并在 PR 描述写明理由。
4. 切换记录 / 关闭抽屉时作废进行中的请求，避免旧响应覆盖新记录（参照 `ShareReportButton` 里 `loadTokenRef` 的做法）。
5. 下载使用 `Blob` + 已有 `downloadBlob`，文件名 `dsa-report-{id}.html`。
6. 桌面端无需特殊 IPC（HTML 通过同一 HTTP 接口取得）。
7. 失败时给出可操作提示（沿用 `getParsedApiError`），不弹空白。

**测试**（vitest）

- `ShareReportButton.test.tsx`：新增菜单项出现、点击触发 `getReportHtml`、下载文件名正确、失败态提示。
- 新增 `ReportHtmlDrawer.test.tsx`：iframe 带 `sandbox` 且不含 `allow-scripts`、`allow-same-origin`；加载 / 错误 / 切换记录不串页。
- `uiText` 的中英韩键一致性测试（若已有则复用，否则补一个）。

**验收**：`cd apps/dsa-web && npm ci && npm run lint && npm run build` 全过，vitest 新增与既有测试 0 失败；附前端截图。

---

### H6（可选）生成报告时同步落盘 `.html`（0.5 天）

**是否做**：见待决问题 Q4。不做则整节删除。

**设计**

- 在 `NotificationService.save_report_to_file()`（`src/notification.py:3102`）旁边新增同名 `.html` 输出，位置与现有 Markdown 报告相同（`reports/`）。
- 仅当新增配置 `REPORT_SAVE_HTML=false`（默认关闭）为 `true` 时生成。
- 同步更新：`src/config.py`（字段）、`src/core/config_registry.py`（元数据）、`src/services/system_config_service.py`（若该键需要校验）、`.env.example`、`docs/full-guide.md` 对应段落、T17 结构性测试。**三处声明必须一次补齐**（STATUS.md 第 3 节）。
- 落盘失败只记日志，不影响通知与主流程。
- 不进 GitHub Actions 默认 workflow 映射（与 `MARKDOWN_TO_IMAGE_CHANNELS` 同口径），在 `docs/notifications.md` 写明。

**风险**：触碰上游冲突高发文件 `src/config.py`。若评审认为收益不足，优先砍掉 H6，而不是为它放宽"最小改动"。

---

### H7（可选）飞书卡片附"查看 HTML 报告"按钮（1 天）

**是否做**：见待决问题 Q5。默认不做。

**前置条件**：必须有一个飞书用户能访问的 Web 服务地址，仓库当前没有这样的统一配置，需要新增 `WEBUI_PUBLIC_BASE_URL` 之类的键，涉及 H6 同级的配置三处同步，并且要处理深链鉴权：未登录用户打开链接怎么办。

**设计要点**

- 只在配置了公开地址且路由为 `report` 时，在卡片末尾追加一个 `action` 按钮（Card JSON 1.0 的 `button` + `url`），不配置时卡片与现在逐字节一致。
- 卡片超过 `FEISHU_MAX_BYTES` 时，按钮是最先被丢弃的元素，不能因为按钮挤掉正文。
- 链接复用已有深链格式 `/?recordId=<id>` 并自动打开 HTML 视图（`parseReportRecordId` 之外再约定一个 `view=html` 参数，同步更新 `reportShare.ts` 与测试）。

---

### H8（可选）结构化增强

默认不做。如果后续想让 HTML 比飞书多出评分环、点位卡等内容，应基于 `AnalysisResult.to_dict()` 另开任务，并明确这会打破"信息与飞书一致"的对照测试，需要重新定义测试口径。

---

### H9 文档、CHANGELOG 与收尾（0.5–1 天）

**文档**

- 新增 `docs/report-html.md`：能力说明、数据流图（见 D1）、接口说明、安全边界（D4）、本地预览方法、已知限制、回滚。
- `docs/notifications.md`「飞书消息卡片排版」一节末尾补一句交叉引用：飞书卡片与 HTML 报告共用 `parse_report_document()`，改卡片解析需同步评估 HTML。
- `docs/share-images.md` 开头补一句区分：分享图是海报（PNG / PDF），HTML 报告是阅读视图。
- `docs/INDEX.md`、`docs/INDEX_EN.md` 增加新文档入口。
- 英文文档：`docs/report-html.md` 只写中文；在交付说明里写明"未提供英文版，原因：同 `docs/share-images.md`，当前仅中文"。若评审要求中英同步，另补 `docs/report-html_EN.md`。
- `README.md` 不动（AGENTS.md 第 1 节）。

**CHANGELOG**（`docs/CHANGELOG.md` 的 `[Unreleased]`，扁平格式）

```text
- [新功能] 历史报告新增 HTML 报告视图：与飞书推送同源的自包含 HTML，支持在线查看与下载，含深色模式与打印样式
- [改进] feishu_card 新增 parse_report_document() 公开只读入口，飞书卡片输出不变
- [测试] 新增报告 HTML 与飞书卡片的信息一致性对照与 XSS 回归测试
```

**AI 治理资产**

- 新增 / 移动文件后执行 `python scripts/gen_ai_codemap.py` 重新生成 `docs/ai/CODEMAP.md`，否则 CI `--check` 会失败。
- 在 `docs/ai/STATUS.md` 第 1 节追加能力行（默认开启、代码锚点），第 2 节追加 T45 状态；`docs/ai/FLOWS.md` 补一条报告 HTML 链路；`docs/ai/PITFALLS.md` 追加"HTML 模板必须放 `src/`，Docker 不带 `templates/`"。
- 改完执行 `python scripts/check_ai_assets.py`。

---

## 6. 文件变更清单

| 类型 | 文件 | 任务 |
| --- | --- | --- |
| 改 | `src/notification_sender/feishu_card.py`（只新增公开入口与 `_dedupe_notes`） | H1 |
| 新 | `src/services/report_html/__init__.py` | H2 |
| 新 | `src/services/report_html/renderer.py` | H2 |
| 新 | `src/services/report_html/templates/report.html.j2` | H2 |
| 新 | `src/services/report_html/templates/_components.html.j2` | H2 |
| 新 | `src/services/report_html/styles.css` | H2 |
| 改 | `api/v1/endpoints/history.py` | H4 |
| 改 | `apps/dsa-web/src/api/history.ts` | H5 |
| 改 | `apps/dsa-web/src/components/report/ShareReportButton.tsx` | H5 |
| 新 | `apps/dsa-web/src/components/report/ReportHtmlDrawer.tsx` | H5 |
| 改 | `apps/dsa-web/src/i18n/uiText.ts`、`apps/dsa-web/src/utils/reportLanguage.ts` | H5 |
| 新 | `tests/test_feishu_card_document.py` | H1 |
| 新 | `tests/test_report_html_renderer.py` | H2 |
| 新 | `tests/test_history_report_html_api.py` | H4 |
| 改/新 | `apps/dsa-web/src/components/report/__tests__/ShareReportButton.test.tsx`、`ReportHtmlDrawer.test.tsx` | H5 |
| 新 | `docs/report-html.md` | H9 |
| 改 | `docs/notifications.md`、`docs/share-images.md`、`docs/INDEX.md`、`docs/INDEX_EN.md`、`docs/CHANGELOG.md` | H9 |
| 改 | `docs/ai/STATUS.md`、`FLOWS.md`、`PITFALLS.md`、重新生成 `CODEMAP.md` | H9 |
| 可选改 | `src/config.py`、`src/core/config_registry.py`、`src/services/system_config_service.py`、`.env.example`、`src/notification.py` | H6 |

不会动：`src/analyzer.py`、`src/storage.py`、`data_provider/**`、`templates/report_*.j2`、`src/share_image.py`、`src/md2img.py`、飞书 sender 的发送逻辑。

---

## 7. 契约与兼容性

- **飞书卡片**：`build_feishu_card()` 输出对既有输入逐字节不变，由 H1 的基线对比测试保证。
- **API**：纯新增 `GET /api/v1/history/{id}/report-html`，不改任何既有字段或枚举。
- **Web**：分享菜单新增两项；既有三项的行为与文案不变。
- **配置**：主链路不新增配置键；H6 / H7 如做则默认关闭。
- **Schema / 存储**：无数据库变更，无迁移。

---

## 8. 验证矩阵

| 改动面 | 命令 / 动作 | 说明 |
| --- | --- | --- |
| Python 语法 | `python -m py_compile <changed_python_files>` | 最低要求 |
| 后端门禁 | `./scripts/ci_gate.sh` | 必跑 |
| 针对性测试 | `python -m pytest tests/test_feishu_card.py tests/test_feishu_card_document.py tests/test_report_html_renderer.py tests/test_history_report_html_api.py tests/test_formatters.py tests/test_notification_sender.py -q` | 飞书既有测试必须全过 |
| 全量离线 | `python -m pytest -m "not network"` | 与基线 9438 passed 比较，不得新增失败 |
| Web | `cd apps/dsa-web && npm ci && npm run lint && npm run build` 加 vitest | 必跑 |
| AI 资产 | `python scripts/gen_ai_codemap.py --check` 与 `python scripts/check_ai_assets.py` | 新增文件后必跑 |
| Docker | 有 Docker 环境时 `docker build` 并在容器内 `python -c "from src.services.report_html import render_report_html"` 渲染一份样本 | 验证模板随镜像打包；无环境则在交付说明里写未验证 |
| 桌面端 | 不改 `apps/dsa-desktop/`，只验证 Web 构建产物 | 说明未跑 Electron 构建 |
| 手工 | 浏览器打开三类样本的浅 / 深 / 手机 / 打印；飞书内置浏览器打开下载的 `.html` | 截图进 PR |

---

## 9. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 复用私有解析函数导致飞书卡片被误改 | 飞书推送回归 | H1 只新增不改行为 + 基线对比测试；PR 里单独一个提交 |
| 报告正文含恶意 HTML（新闻标题 / LLM 输出） | XSS | D4 全量转义 + 白名单 + 沙箱 iframe + CSP；H2 第 3 条安全测试；Web 端禁 `allow-same-origin` |
| 模板没随 Docker 打包 | 容器里 500 | 模板放 `src/`；Docker 验证项 |
| 飞书解析把表格展开成字段后 HTML 不够"像表格" | 视觉不满意 | D3 已声明；若评审要求真实表格，回到 D1 变更方案并重估工期 |
| 样式在极端内容下破版（超长名称、几十个字段） | 观感差 | H3 极端样本检查表；`overflow-wrap: anywhere` |
| 大报告体积大 | 加载慢 | CSS 内联约 10KB；体积目标见 H2；超限 413 |
| iframe `sandbox` 导致链接打不开 | 体验问题 | 仅在必要时加 `allow-popups allow-popups-to-escape-sandbox`，并写进 PR |
| 英文 / 韩文文案缺失 | 界面混排 | H5 只改 `uiText.ts`，补键一致性测试 |
| 上游合并冲突 | 合并成本 | 不碰 STATUS.md 第 5 节大文件（H6 除外，默认不做） |

---

## 10. 回滚方式

- 代码：回退 T45 对应提交即可；新增接口与前端入口随之消失，既有功能不受影响。
- 局部降级：前端隐藏两个菜单项即可停用入口，后端接口可保留。
- 飞书：H1 仅新增公开入口，回滚不影响卡片。
- 数据：无迁移，无需数据回滚。
- 配置：主链路无新增键；若做了 H6 / H7，回滚时删除对应 `.env` 键即可（未知键会被忽略）。

---

## 11. 决策记录（已定稿）

| 编号 | 问题 | 结论 |
| --- | --- | --- |
| Q1 | 需求理解：HTML 是"报告页里能看到飞书同款内容"，而不是"把 HTML 发到飞书" | **已确认**。本任务只做报告页呈现，不做反向推送 |
| Q2 | HTML 是否保留真实表格（飞书里已展开为字段） | **不保留**。v1 与飞书保持一致，用字段网格呈现；D1 不变 |
| Q3 | 多股聚合日报是否也要 HTML | **不做**。v1 只覆盖单条历史记录（个股、决策仪表盘记录、大盘复盘记录） |
| Q4 | 生成报告时是否落盘 `.html`（H6） | **不做**。H6 整节移出本任务 |
| Q5 | 飞书卡片是否加"查看 HTML 报告"按钮（H7） | **不做**。H7 整节移出本任务 |
| Q6 | 主题是否只需跟随系统深浅色 | **是**。零 JS，不做手动切换 |
| Q7 | 英文、韩文是否完整对照 | **是**。页面框架文案三语齐全；正文语言由报告本身决定 |

**对其他章节的影响（以本节为准）**

- 第 3.2 节非目标追加：落盘 `.html`、飞书卡片按钮、多股聚合日报 HTML、结构化增强。
- 第 5 节 H6 / H7 / H8 仅作后续参考，不进入本次排期、验收与 PR。
- 因此本任务**不触碰** `src/config.py`、`config_registry.py`、`system_config_service.py`、`.env.example`、`src/notification.py`，也不新增任何配置键。
- 第 6 节"可选改"一行、第 12 节"可选"一行随之作废；合计工期按 8–9 个工作日。
- 第 14 节验收清单中"若做了 H6 / H7……"一项作废，改为"确认未新增配置键、未改动上述上游大文件"。

---

## 12. 工期与里程碑

| 里程碑 | 内容 | 预估 |
| --- | --- | --- |
| M1 | H0 + H1：方案确认、解析入口、飞书基线 | 1 天 |
| M2 | H2 + H3：渲染器与视觉 | 3–4 天 |
| M3 | H4：后端接口 | 1 天 |
| M4 | H5：Web 入口 | 2 天 |
| M5 | H9：文档、CHANGELOG、AI 资产、全量验证 | 1 天 |
| 可选 | H6 / H7 | 各 0.5–1 天 |

合计约 8–9 个工作日（不含可选项）。

---

## 13. PR 与交付要求

- PR 标题建议：`feat: 历史报告新增飞书同源 HTML 报告视图`。
- 建议拆为 3 个提交，便于审查和单独回滚：① H1 解析入口 + 基线测试；② H2–H4 渲染器与接口；③ H5 前端 + 文档。
- PR 描述必须包含：
  - 原问题 / 目标、方案概述、数据流图；
  - 飞书卡片与 HTML 并排截图，浅 / 深 / 手机 / 打印各一组（截图只放 PR 描述，**不入库**）；
  - 安全设计说明（D4）与对应测试；
  - 验证矩阵执行结果，未执行项与原因（如 Docker、Electron）；
  - 风险点与回滚方式。
- 交付说明按 AGENTS.md 第 9 节：改了什么 / 为什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式。
- 未经确认不执行 `git commit` / `git push` / `git tag`；合入前自行跑 `./scripts/ci_gate.sh`。
- 创建 PR 前先 `git fetch --all --prune`，工作区干净且可 fast-forward 时再 `git pull --ff-only`。

---

## 14. 验收清单（完成定义）

- [x] 第 11 节待决问题已有结论（2026-11-20 已定稿）
- [ ] `build_feishu_card()` 输出对既有样本逐字节不变，`tests/test_feishu_card.py` 未改断言且通过
- [ ] 一致性对照测试通过：HTML 与飞书卡片的板块标题、字段键值一致
- [ ] XSS 回归测试通过；输出不含脚本、事件属性、外链资源
- [ ] `GET /api/v1/history/{id}/report-html` 含 CSP / nosniff / no-store，404 / 413 / 500 / 鉴权用例齐全
- [ ] Web 菜单有"查看 HTML 报告""下载 HTML"，iframe 沙箱，切换记录不串页
- [ ] 中 / 英 / 韩文案齐全且只进 `uiText.ts`
- [ ] 浅色 / 深色 / 手机 / 打印截图已附 PR
- [ ] `./scripts/ci_gate.sh`、`npm run lint`、`npm run build`、vitest、`gen_ai_codemap.py --check`、`check_ai_assets.py` 通过
- [ ] 全量离线测试无新增失败
- [ ] `docs/report-html.md`、`docs/CHANGELOG.md`（扁平格式）、`docs/ai/*` 已更新
- [ ] 未新增任何配置键；未改动 `src/config.py`、`config_registry.py`、`system_config_service.py`、`.env.example`、`src/notification.py`

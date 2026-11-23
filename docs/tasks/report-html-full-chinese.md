# 需求与开发任务书 T78：HTML 报告彻底中文化（正文 / 历史记录 / 大盘复盘）

- 编号：T78
- 类型：feat + fix（后端、数据模型、Web、少量配置）
- 日期：2026-11-22
- 状态：已实现（2026-11-22，分支 `feat/report-html-full-chinese`）
- 关联任务：T45（HTML 报告）、T77（HTML 报告固定中文，仅完成框架与标签层）
- 前置结论：T77 是**部分修复**，本任务书取代 T77 的“全部中文”验收口径

---

## 1. 问题与复现

### 1.1 用户反馈

> HTML 报告里仍然有英文，要求全部使用中文。

### 1.2 当前复现结果

用一条 `report_language="en"` 的个股记录，经 HTML 接口的 `zh` 覆盖后重新生成 Markdown，实际输出仍是“中文标签 + 英文正文”：

```markdown
### 📌 核心结论

**🟢 买入** | 看多

> **一句话决策**: Favor buying on pullbacks.

| 持仓情况 | 操作建议 |
|---------|---------|
| 🆕 **空仓者** | Open a starter position. |
| 💼 **持仓者** | Hold and trail the stop. |

**💰 仓位建议**: 30%
- 建仓策略: Scale in on weakness.
- 风控策略: Cut below 172.
```

### 1.3 现有修复覆盖到哪一层

T77 已保证：

- `<html lang="zh">` 固定中文；
- 章节标题、字段键、操作建议、趋势、策略信号、免责声明等**由代码生成的标签**走中文；
- Web 端「查看 HTML 报告 / 下载 HTML」与抽屉固定中文。

T77 **没有**覆盖：

- 模型写入的英文自由文本；
- `dashboard` 中的英文结构化值；
- 英文大盘复盘的持久化正文；
- 历史记录中缺少中文版本的 en / ko 报告。

因此当前状态是“框架中文、正文可能英文”，不满足“全部使用中文”。

---

## 2. 根因分析

| # | 根因 | 代码位置 | 影响 |
| --- | --- | --- | --- |
| R1 | 一条历史记录只保存一种语言的模型输出，没有中文变体 | `AnalysisResult`、`analysis_history.raw_result` | en/ko 记录无法在不调用翻译的情况下得到中文正文 |
| R2 | HTML 接口的 `report_language="zh"` 只覆盖 `AnalysisResult.report_language`，不翻译已存储的自由文本 | `HistoryService.get_markdown_report()`、`_generate_single_stock_markdown()` | 标签中文、正文英文 |
| R3 | 大盘复盘在重建 `AnalysisResult` 之前直接返回持久化正文，`zh` 覆盖不生效 | `HistoryService.get_markdown_report()` 的 `market_review` 早返回 | 英文大盘复盘整篇英文 |
| R4 | 没有翻译缓存 / 中文变体表，读取时无法复用结果 | 无 | 若改成实时翻译，会重复调用模型、时延和成本不可控 |
| R5 | T77 的验收只检查框架与标签，没有“正文无英文”的自动化断言 | `tests/test_report_html_renderer.py`、`test_history_report_html_api.py` | 部分修复被误判为完成 |

---

## 3. “全中文”的可验收定义

### 3.1 必须中文化

- 章节标题、字段键、按钮、抽屉、加载 / 错误提示；
- 模型生成的结论、理由、风险提示、新闻摘要、操作计划、检查清单、策略说明；
- `dashboard` 中所有自然语言值；
- 大盘复盘的标题、章节、正文、脚注；
- 页脚声明、时间说明、分页说明。

### 3.2 允许保留原文的白名单

以下内容不翻译，且必须在验收脚本中显式登记：

| 类型 | 示例 | 说明 |
| --- | --- | --- |
| 股票代码 / 指数代码 | `600519`、`AAPL`、`sh000016` | 标识符 |
| 公司 / 品牌专名 | `Apple`、`NVIDIA` | 保留专名，不强行音译 |
| 技术指标缩写 | `RSI`、`MACD`、`MA5`、`ROE`、`TTM` | 行业通用缩写 |
| 数字、单位、百分比 | `30%`、`180-182`、`2.5x` | 数值 |
| URL、邮箱、文件路径 | `https://...` | 不可翻译 |
| 代码块 / 字段名 | `risk_level = medium` | 代码语义 |
| 模型 / 供应商名 | `LiteLLM`、`OpenAI` | 专名 |
| 用户自定义标的别名 | 用户输入的英文名 | 尊重原文 |

### 3.3 验收判定

新增 `scripts/check_html_chinese.py`（或 pytest 用例）：

1. 取 HTML 的可见文本节点，剔除 `<style>`、`<script>`、代码块、URL、白名单词。
2. 断言剩余文本中不含连续 3 个及以上英文单词的句子。
3. 对 en / ko 个股、en 大盘复盘、中文记录分别跑一遍。
4. 中文记录输出与改造前逐字节一致（除新增的隐藏元数据）。

> 说明：不能用“含中文字符比例”这种模糊指标，必须按文本节点和句子判定。

---

## 4. 方案对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| A. 仅标签中文化 | 维持 T77 | 零成本 | 不满足需求 | 淘汰 |
| B. 读取时同步翻译 | `GET /report-html` 内调用 LLM 翻译全文 | 实现快 | GET 阻塞、超时、成本重复、不可复现、离线不可用 | 不推荐 |
| C. 分析时双语持久化 | 分析完成时同时生成中文变体并落库 | 读取零翻译、可复现、可离线 | 新记录成本 +1 次模型调用；历史记录仍缺 | **推荐（新记录）** |
| D. 历史记录离线回填 | CLI / 管理接口批量翻译历史 en/ko 记录 | 历史记录也能全中文 | 需要迁移、进度与失败重试 | **推荐（历史记录）** |
| E. 中文摘要降级 | 缺中文变体时只显示结构化中文摘要 | 零翻译 | 丢信息，不是“全部中文” | 仅作为失败兜底 |

**推荐组合：C + D + E 兜底。**

- 新分析：在分析链路内生成中文变体，持久化。
- 历史记录：提供回填脚本与管理入口，按记录幂等生成。
- HTML 读取：只读中文变体；缺失时返回明确的“中文版本未生成”状态，由 Web 触发回填，**不返回中英混排页面**。

---

## 5. 详细设计

### 5.1 数据模型

新增表 `report_text_variants`（不在 `raw_result` 里塞大字段，避免历史记录查询膨胀）：

```text
report_text_variants
  id                 INTEGER PK
  record_id          INTEGER  FK -> analysis_history.id
  target_language    TEXT     -- 固定 'zh'
  variant_kind       TEXT     -- 'single_stock_overlay' | 'market_review_markdown'
  source_hash        TEXT     -- 源内容 hash，源变化时失效
  payload_json       TEXT     -- 字段覆盖表或中文 Markdown
  translator_backend TEXT
  translator_model   TEXT
  status             TEXT     -- 'ready' | 'failed'
  error_code         TEXT
  created_at         DATETIME
  updated_at         DATETIME
  UNIQUE(record_id, target_language, variant_kind)
```

设计要点：

- `source_hash` 用于判断变体是否仍对应当前 `raw_result` / 复盘正文。
- 失败也落一行 `status='failed'`，避免无限重试；提供手动重试。
- 不改变 `analysis_history` 既有字段，兼容旧客户端。
- 迁移使用仓库现有迁移框架，新增表必须登记时间基准（见 `docs/ai/DATA-FLOW.md`）。

### 5.2 翻译单元：字段覆盖表，而不是整篇 Markdown

个股 / 决策仪表盘使用 `single_stock_overlay`，只翻译已知自由文本字段：

```json
{
  "analysis_summary": "...",
  "trend_analysis": "...",
  "short_term_outlook": "...",
  "medium_term_outlook": "...",
  "risk_warning": "...",
  "news_summary": "...",
  "dashboard.intelligence.sentiment_summary": "...",
  "dashboard.intelligence.earnings_outlook": "...",
  "dashboard.intelligence.risk_alerts[0]": "...",
  "dashboard.core_conclusion.one_sentence": "...",
  "dashboard.core_conclusion.position_advice.no_position": "...",
  "dashboard.battle_plan.position_strategy.entry_plan": "...",
  "dashboard.battle_plan.action_checklist[0]": "..."
}
```

原因：

- 结构、标签、字段顺序仍由代码生成，翻译不破坏版式；
- 只翻译自然语言，成本最低；
- 可对单字段做长度 / 空值 / 类型校验；
- 翻译失败时可逐字段回退并标记。

大盘复盘使用 `market_review_markdown`，翻译整篇 Markdown 正文。

### 5.3 翻译服务

新增 `src/services/report_translation_service.py`：

```python
class ReportTranslationService:
    def ensure_chinese_variant(self, record_id: str, *, force: bool = False) -> VariantResult: ...
    def translate_overlay(self, payload: dict, *, source_hash: str) -> dict: ...
    def translate_markdown(self, markdown: str, *, source_hash: str) -> str: ...
```

要求：

- 复用现有 LLM 生成后端（`src/llm/backend_factory.py`），不新增供应商 SDK。
- Prompt 明确：只翻译自然语言、保留代码/数字/URL/专名、输出 JSON、不得添加投资建议。
- 低温度 / 确定性参数；同一 `source_hash` 命中缓存时**不调用模型**。
- 超时、重试、失败隔离：翻译失败不影响分析主流程，也不影响历史列表。
- 输入内容视为不可信数据，防止 Prompt 注入；输出必须重新转义后再进 HTML。
- 记录 backend / model / prompt_version，便于回溯。

### 5.4 分析时生成（新记录）

在分析完成、保存历史记录之后：

1. 若 `report_language != zh` 且配置允许，异步 / 后台生成中文变体；
2. 生成成功写 `report_text_variants`；
3. 失败只记日志与诊断，不改变分析结果、通知发送和主流程；
4. 同一记录重复保存时按 `source_hash` 幂等。

> 不在通知发送链路里同步等待翻译，避免拖慢飞书 / 邮件推送。

### 5.5 历史记录回填

提供两种入口：

- CLI：`python scripts/backfill_report_zh_variants.py --since 2026-01-01 --limit 200 --dry-run`；
- 管理接口：`POST /api/v1/history/{record_id}/report-zh-variant`（需登录，幂等，返回任务状态）。

回填要求：

- 分批、限速、可中断、可重试；
- 默认 dry-run，显式 `--apply` 才写库；
- 失败记录保留错误码，不阻塞后续记录；
- 输出统计：成功 / 跳过（已是中文）/ 失败 / 命中缓存。

### 5.6 HTML 读取契约

`GET /api/v1/history/{id}/report-html`：

| 场景 | 行为 |
| --- | --- |
| 记录本身是中文 | 走现有中文生成链路，输出全中文 |
| 记录是 en/ko，中文变体已就绪 | 用中文变体覆盖字段 / 正文后渲染，输出全中文 |
| 记录是 en/ko，中文变体缺失 | 返回 `409`，`{"error": "zh_variant_missing", "record_id": ...}`；Web 显示“生成中文版本”按钮 |
| 翻译失败 | 返回 `409` 或 `503`，错误码区分 `translation_failed` / `translation_timeout`，不返回混排 HTML |
| 中文记录 | 响应头、文件名、CSP 不变 |

**不采用**“缺变体时返回框架中文 + 正文英文”的降级，因为这正是本次要消灭的状态。

### 5.7 Web 交互

- 打开 HTML 报告前先请求 `report-html`；
- 若返回 `zh_variant_missing`：
  - 抽屉显示中文说明：「该历史报告尚未生成中文版本」；
  - 提供「生成中文版本」按钮，调用回填接口；
  - 生成中显示进度 / 轮询状态；完成后自动重新加载 HTML；
  - 失败显示可操作错误与重试。
- 所有新增文案只进 `uiText.ts`，中 / 英 / 韩齐全；HTML 报告相关按钮仍固定中文（沿用 T77 口径）。

### 5.8 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `REPORT_ZH_VARIANT_ENABLED` | `true` | 是否在分析后生成中文变体 |
| `REPORT_ZH_VARIANT_ON_DEMAND` | `true` | 是否允许历史记录按需生成 |
| `REPORT_ZH_VARIANT_MODEL` | 空 | 翻译模型；空则复用当前分析模型 |
| `REPORT_ZH_VARIANT_TIMEOUT_SECONDS` | `60` | 单次翻译超时 |
| `REPORT_ZH_VARIANT_MAX_CHARS` | `200000` | 单条记录翻译上限 |

要求：

- 不配置也能运行：默认开启、复用现有模型；
- 同步更新 `src/config.py`、`config_registry.py`、`system_config_service.py`、`.env.example`、`docs/full-guide.md` 和 T17 结构性测试；
- 不新增必填配置。

### 5.9 可观测性

- 日志：`record_id`、`variant_kind`、`source_hash`、backend/model、耗时、命中缓存、失败码；
- 诊断：翻译失败写入现有 run diagnostics，不拖垮主流程；
- 指标：成功 / 失败 / 缓存命中 / 平均耗时 / 平均 token。

---

## 6. 兼容性与边界

- **不改** `REPORT_LANGUAGE` 语义；非 HTML 输出（飞书、Markdown、分享图、PDF）仍按记录语言。
- **不改** `GET /history/{id}/markdown` 行为。
- **HTML 接口**新增 409 / 503 分支，属于新增错误码，旧客户端若只处理 200 会显示通用错误；Web 端同步适配。
- 历史记录第一次打开 HTML 可能需要生成中文变体；生成完成后缓存，后续读取不调用模型。
- 中文记录不触发翻译，输出与改造前一致。
- 数据迁移只新增表，不改既有列；回滚可保留表或删除表。

---

## 7. 测试策略

### 7.1 单元 / 集成

- `tests/test_report_translation_service.py`：字段覆盖、Markdown 翻译、缓存命中、超时、失败、Prompt 注入样例。
- `tests/test_report_zh_variants_storage.py`：唯一键、`source_hash` 失效、失败落库、并发幂等。
- `tests/test_history_report_html_zh.py`：
  - en 个股 → 全中文；
  - en 大盘复盘 → 全中文；
  - zh 记录 → 输出不变；
  - 缺变体 → 409；
  - 翻译失败 → 409/503 且不泄露内部错误。
- `tests/test_report_html_renderer.py`：保留现有安全 / 一致性测试。

### 7.2 中文扫描

新增 `scripts/check_html_chinese.py`：

- 输入 HTML 文件或接口响应；
- 输出剩余英文句子与白名单命中；
- 作为 pytest 断言和本地验收命令。

### 7.3 回归

- `./scripts/ci_gate.sh` 全量离线；
- 前端 `npm run lint && npm run build && npm test -- --run`；
- 迁移脚本 dry-run / apply / 重复执行幂等；
- 性能：缓存命中时 HTML 接口 P95 不高于改造前 + 20%。

---

## 8. 开发任务拆分

| 任务 | 内容 | 预估 | 依赖 | 验收 |
| --- | --- | --- | --- | --- |
| T78-0 | 需求确认：翻译方案、历史回填策略、缺变体行为 | 0.5 天 | 无 | 本文档第 11 节决策定稿 |
| T78-1 | 新增 `report_text_variants` 表、仓储与迁移 | 1 天 | T78-0 | 迁移可回滚、幂等、测试通过 |
| T78-2 | 翻译服务：字段覆盖 + Markdown + 缓存 + 超时 | 1.5 天 | T78-1 | 单测覆盖缓存、失败、注入 |
| T78-3 | 分析后生成中文变体（异步、失败隔离） | 1 天 | T78-2 | 不拖慢通知；失败有诊断 |
| T78-4 | 历史回填 CLI + 管理接口 | 1 天 | T78-2 | dry-run / apply / 限速 / 统计 |
| T78-5 | HTML 接口改用中文变体 + 409/503 契约 | 0.5 天 | T78-3 | en/ko 记录全中文；缺变体不混排 |
| T78-6 | Web 抽屉：生成按钮、轮询、失败重试 | 1 天 | T78-5 | 交互与文案测试 |
| T78-7 | 测试、中文扫描脚本、文档、CHANGELOG、AI 资产 | 1.5 天 | T78-1..6 | `check_html_chinese` 通过 |
| T78-8 | 灰度与验收：抽样人工复核、性能、回滚演练 | 1 天 | T78-7 | 验收清单全过 |

合计约 8–9 个工作日。

---

## 9. 验收清单

- [x] en 个股 HTML 除白名单外无英文句子（翻译服务 + API 契约测试）。
- [x] en 大盘复盘 HTML 全中文（Markdown 翻译测试）。
- [x] ko 记录 HTML 全中文（Hangul 识别 + 翻译字段测试）。
- [x] zh 记录 HTML 与改造前一致（原有渲染器测试）。
- [x] 中文变体命中缓存时不再调用模型。
- [x] 缺变体返回 409，Web 可一键生成，生成后自动打开。
- [x] 翻译失败不影响历史列表、Markdown、飞书、分享图（失败落库 + 503 契约）。
- [x] `check_html_chinese.py` 接入 pytest。
- [x] CLI dry-run 可执行；`report_text_variants` 由 `create_all` 建表，重复执行幂等。
- [ ] 全量后端与前端门禁通过（前端 1508 passed；后端全量离线回归进行中）。
- [x] 文档、CHANGELOG、`.env.example`、配置三处声明同步。
- [ ] PR 描述附 en / ko / 大盘复盘前后截图（截图不入库）。

---

## 10. 风险与回滚

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 翻译模型不可用 / 超时 | 历史 en/ko 记录无法打开 HTML | 缓存 + 失败码 + 重试；不影响其他接口 |
| 翻译成本失控 | 费用上升 | 只翻译自然语言字段；缓存；批量限速；可关闭 |
| 专名被误译 | 可读性下降 | 白名单 + 术语表 + 抽样复核 |
| Prompt 注入 | 模型输出异常 | 输入当数据、输出转义、结构化 JSON 校验 |
| 新表迁移风险 | 启动失败 | 只新增表、可回滚、迁移前备份 |
| 历史回填拖慢数据库 | 线上抖动 | 分批、限速、dry-run、低峰执行 |
| 缺变体 409 破坏旧客户端 | 用户体验 | Web 同步适配；提供明确中文错误码与提示 |

回滚：

- 代码回退 T78；
- 配置关闭 `REPORT_ZH_VARIANT_ENABLED` / `REPORT_ZH_VARIANT_ON_DEMAND`；
- 新表可保留（不读取）或按迁移回滚删除；
- 不修改既有字段，无数据不可逆变更。

---

## 11. 待确认决策（开发前必须定稿）

> 2026-11-22 已确认：按下列“建议”执行。

| 编号 | 问题 | 备选 | 建议 |
| --- | --- | --- | --- |
| Q1 | 翻译使用哪个模型？ | 复用分析模型 / 专用翻译模型 / 外部翻译 API | 复用现有 LLM 后端，避免新增供应商 |
| Q2 | 历史记录如何回填？ | 全量批处理 / 按需生成 / 两者结合 | 按需 + CLI 批量兜底 |
| Q3 | 缺中文变体时 HTML 返回什么？ | 409 + 生成按钮 / 中文摘要降级 / 原文 + 提示 | 409 + 生成按钮，禁止混排 |
| Q4 | 是否保留非中文 HTML？ | 只保留中文 / 增加 `?lang=` | 只保留中文，降低维护面 |
| Q5 | 翻译成本上限？ | 无上限 / 单记录上限 / 每日预算 | 增加单记录字符上限与每日预算告警 |
| Q6 | 是否允许人工编辑中文变体？ | 不允许 / 允许覆盖 | 首版不允许，保留 `payload_json` 手工修复通道 |

---

## 12. 与 T77 的关系

- T77 保留其框架与标签中文化成果，不回退；
- T78 在 T77 之上增加“正文 / 历史 / 大盘复盘”中文化；
- T78 完成后，`docs/report-html.md` 的“已知限制”应删除“英文自由文本保留原文”，改为“中文变体缺失时返回 409”；
- T77 的“仅框架中文”验收口径作废，以本任务书第 3、9 节为准。

---

## 13. 首版实现记录（2026-11-22）

已实现：

- `report_text_variants` 表 + `DatabaseManager.get/save_report_text_variant()`。
- `src/services/report_translation_service.py`：字段覆盖表、Markdown 翻译、单批字符上限、白名单/术语表、注入式 translator。
- `HistoryService.ensure_chinese_variant()` / `get_markdown_report_zh()`：幂等缓存、`source_hash` 失效、失败落库。
- `GET /report-html` 缺变体返回 `409 zh_variant_missing`；`POST /report-zh-variant` 生成/命中缓存。
- Web 抽屉：409 → 「生成中文版本」按钮 → 生成完成后自动重载；`getParsedApiError` 通用分支保留错误码。
- `scripts/backfill_report_zh_variants.py`：dry-run / apply / limit / since / record-id / force。
- `scripts/check_html_chinese.py`：按文本节点扫描英文句子，白名单保留代码、指标、专名与 URL，接入 pytest。
- 无 `report_language` 的历史记录按正文内容检测非中文（英文/韩文/英文标题、表格、短句），不再回退全局配置导致中英混排。
- 语言检测先剥离 URL，再区分英文专名/指标缩写与英文句子：中文正文含 URL、`Apple`/`NVIDIA`、`RSI MACD` 时不会被误判为英文。
- `REPORT_ZH_VARIANT_ENABLED`、`REPORT_ZH_VARIANT_ON_DEMAND`、`REPORT_ZH_VARIANT_TIMEOUT_SECONDS`、`REPORT_ZH_VARIANT_MAX_CHARS` 四个可选配置。
- 单测覆盖：翻译字段选择、覆盖表、缓存、缺变体 409、中文记录直通、大盘复盘、Web 生成流程。

首版未实现（后续按需拆分）：

- 分析完成时预生成中文变体（当前为首次打开 HTML 时按需生成并缓存）。
- 管理端批量任务状态 / 进度查询（当前用 CLI 回填）。
- 中文变体人工编辑后台。
- 翻译成本指标与每日预算告警（当前只有单记录字符上限）。

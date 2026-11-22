# 开发任务书 T77：HTML 报告固定中文

- 编号：T77（接 `.claude/reviews/full-repo-audit-tasks-20261121.md` 的 T45–T76）
- 类型：fix（后端 + Web，少量文档）
- 日期：2026-11-22
- 状态：已完成（2026-11-22）
- 关联任务：T45「飞书推送内容的精美 HTML 报告呈现」
- 分支建议：`fix/report-html-chinese-only`（一个任务一个分支一个 PR，遵循 AGENTS.md 第 8 节）

---

## 1. 背景与目标

### 1.1 需求原文

> html报告的部分需要全部使用中文。

### 1.2 现状（已读代码）

- HTML 报告的内容来自历史记录的 Markdown（`HistoryService.get_markdown_report()`），再经 `format_feishu_markdown()` → `parse_report_document()` → `render_report_html()` 输出。
- Markdown 重建时会读取记录的 `report_language`：`zh` / `en` / `ko`，章节标题、字段键、操作建议、趋势、策略标签等都会跟随该语言。
- `render_report_html()` 把 `report_language` 归一化后只用于 `<html lang="...">`；正文语言完全由 Markdown 决定。
- Web 端 `ShareReportButton` 与 `ReportHtmlDrawer` 的「查看 HTML 报告 / 下载 HTML / HTML 报告 / 关闭 / 加载中」等文案来自 `getReportText(reportLanguage)`，会跟随记录语言或界面语言。

因此，中文记录的 HTML 报告本身已经全中文；出现英文只会在记录语言为 `en`/`ko`，或 Web 界面为英文时。

### 1.3 目标口径（已与需求方确认）

**HTML 报告固定中文，不再跟随记录语言或界面语言。**

具体含义：

1. 生成的 HTML 文档固定 `<html lang="zh">`。
2. 生成 HTML 时按 `zh` 重新构建单股 / 决策仪表盘报告：章节标题、字段键、操作建议、趋势、策略信号、免责声明等固定文案全部中文。
3. Web 端 HTML 报告相关入口（查看 / 下载菜单项、抽屉标题、关闭、加载中、成功 / 失败提示）固定中文；其他分享动作（复制链接、导出 PDF、分享图片）保持现有界面语言，避免无关功能被牵连。
4. 已知限制：历史记录中由模型当年写入的英文自由文本（`trend_analysis`、`risk_warning`、`analysis_summary`、资讯摘要等）无法离线翻译，保留原文；大盘复盘的正文来自持久化原文，也不做翻译。这属于「原文保留」，不是框架文案漏翻。

---

## 2. 范围

### 2.1 范围内

- 后端 HTML 渲染器固定中文。
- 历史报告 HTML 接口按 `zh` 重新生成 Markdown。
- `HistoryService.get_markdown_report()` 增加可选的报告语言覆盖参数，默认行为不变。
- Web 端 HTML 报告相关文案固定中文。
- 测试、任务文档、专题文档、CHANGELOG、AI 常青文档。

### 2.2 非目标

- 不翻译模型生成的历史自由文本；不引入翻译服务。
- 不改变飞书卡片、Markdown 报告接口、分享图 / PDF 的既有语言行为。
- 不改 `REPORT_LANGUAGE` 配置语义。
- 不改 HTML 报告的安全边界（零脚本、全量转义、CSP、沙箱 iframe）。

---

## 3. 设计

### 3.1 渲染器固定中文

`src/services/report_html/renderer.py`：

- 删除 `report_language` 参数（仓库内只有历史 HTML 接口和测试调用）。
- `<html lang="zh">` 写死在模板 `report.html.j2`，不再传 `lang`。
- 文档字符串与异常信息保持中文。

不保留「接收语言但忽略」的静默参数，避免调用方误以为语言仍然生效。

### 3.2 历史报告按中文重建

`src/services/history_service.py`：

```python
def get_markdown_report(
    self,
    record_id: str,
    *,
    report_language: Optional[str] = None,
) -> Optional[str]: ...
```

- 默认 `None`：保持现有行为，使用记录自身的 `raw_result.report_language`。
- 传入 `zh`：重建 `AnalysisResult` 后覆盖 `result.report_language`，再生成 Markdown，使 `get_report_labels()`、`localize_*()`、`_label()` 全部走中文。
- 大盘复盘在重建前直接返回持久化正文，语言覆盖不作用于正文，属已知限制。

`api/v1/endpoints/history.py`：

- `_history_share_image_input()` 增加可选 `report_language` 参数并透传。
- `GET /{record_id}/report-html` 固定传 `report_language="zh"`。
- 删除仅供 HTML 接口使用的 `_history_report_language()` 与 `normalize_report_language` 调用（若该导入在文件内仍有其他用途，则保留导入）。
- 分享图接口不传语言覆盖，行为不变。

### 3.3 Web 端

- `ReportHtmlDrawer.tsx`：移除 `reportLanguage` 属性，固定 `getReportText('zh')`。
- `ShareReportButton.tsx`：新增 `htmlText = getReportText('zh')`，仅用于：
  - 菜单项「查看 HTML 报告」「下载 HTML」；
  - HTML 下载进行中的「生成 HTML...」；
  - 查看 / 下载成功与失败提示。
  - 其他菜单项与分享按钮主文案继续使用 `text`。
- `reportLanguage.ts` 保留 `en`/`ko` 文案键，供其他功能使用；不删除键，避免无关契约变化。

---

## 4. 任务拆分

### C1 渲染器固定中文（0.25 天）

- 改 `renderer.py`、`templates/report.html.j2`。
- 更新 `tests/test_report_html_renderer.py`：
  - 不再按 `zh/en/ko` 断言 `lang`；始终断言 `<html lang="zh">`。
  - 增加「英文正文输入仍输出中文框架」用例。

### C2 HTML 接口按中文重建（0.5 天）

- 改 `HistoryService.get_markdown_report()` 与 `_history_share_image_input()`、`get_history_report_html()`。
- 更新 `tests/test_history_report_html_api.py`：
  - 断言接口以 `report_language="zh"` 取 Markdown。
  - 断言渲染器不再接收 `report_language`。
  - 记录语言为 `en` 时仍返回中文框架。
- 在 `tests/test_analysis_history.py` 增加语言覆盖用例：英文记录可按 `zh` 生成中文标签，默认调用仍保持英文。

### C3 Web 文案固定中文（0.5 天）

- 改 `ReportHtmlDrawer.tsx`、`ShareReportButton.tsx`。
- 更新 `ReportHtmlDrawer.test.tsx`、`ShareReportButton.test.tsx`：
  - 英文记录 / 英文界面下，HTML 菜单项与抽屉仍为中文。
  - 其他分享项仍按界面语言渲染。

### C4 文档与收尾（0.5 天）

- 本任务书。
- `docs/report-html.md`：语言行为、已知限制。
- `docs/CHANGELOG.md`：`[Unreleased]` 扁平条目。
- `docs/ai/STATUS.md`、`docs/ai/FLOWS.md`、`docs/ai/PITFALLS.md` 视需要更新。
- 执行 `python scripts/gen_ai_codemap.py --check` / `python scripts/check_ai_assets.py`。

---

## 5. 契约与兼容性

| 面 | 影响 |
| --- | --- |
| `GET /api/v1/history/{id}/report-html` | 路径、响应头、错误码不变；输出语言固定中文 |
| `GET /api/v1/history/{id}/markdown` | 不变，仍使用记录语言 |
| 飞书卡片 / 分享图 / PDF | 不变 |
| `render_report_html()` | 移除 `report_language` 参数；仓库内唯一生产调用方同步更新 |
| `HistoryService.get_markdown_report()` | 仅新增可选关键字参数，默认行为不变 |
| Web 分享菜单 | 仅 HTML 相关文案固定中文；其他项不变 |
| 配置 / Schema / 存储 | 无新增配置，无迁移 |

---

## 6. 验收标准

- [x] 任意语言的记录，`GET /report-html` 输出 `<html lang="zh">`，框架与字段标签为中文。
- [x] 英文记录生成的 HTML 中不再出现 `Stock Analysis Report`、`Core Conclusion`、`Close`、`Volume` 等框架英文（模型原文除外）。
- [x] Web 英文界面下，HTML 菜单项与抽屉文案为中文。
- [x] Markdown 接口、飞书卡片、分享图行为不变。
- [x] 针对性测试通过；`python -m py_compile`、flake8、前端 lint / build / vitest 通过。
- [x] 文档与 CHANGELOG 已更新。

---

## 7. 验证矩阵

| 改动面 | 命令 / 动作 |
| --- | --- |
| Python 语法 | `python -m py_compile src/services/report_html/renderer.py src/services/history_service.py api/v1/endpoints/history.py` |
| 后端针对性测试 | `python -m pytest tests/test_report_html_renderer.py tests/test_history_report_html_api.py tests/test_analysis_history.py -q` |
| 后端门禁 | `./scripts/ci_gate.sh` |
| Web | `cd apps/dsa-web && npm run lint && npm run build && npm test -- --run` |
| AI 资产 | `python scripts/gen_ai_codemap.py --check`、`python scripts/check_ai_assets.py` |
| 手工 | 用英文记录打开 HTML 报告，确认框架中文；检查 Markdown / 飞书不受影响 |

---

## 8. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 英文自由文本仍保留英文，被误判为「没改完」 | 任务书与专题文档明确写出限制；验收只针对框架与结构化标签 |
| 移除 `report_language` 参数影响仓库外调用方 | 该渲染器是内部模块，仓库内调用点已全部更新；变更写入 CHANGELOG |
| Web 英文界面出现中英混排 | 只固定 HTML 相关文案，其他分享项保持界面语言；用测试锁定 |
| 大盘复盘正文语言无法切换 | 明确列为已知限制，不伪造翻译 |

---

## 9. 回滚方式

- 代码：回退 T77 对应提交，HTML 报告恢复跟随记录语言。
- 局部降级：Web 端恢复 `ReportHtmlDrawer` 的 `reportLanguage` 透传即可；后端接口可单独保留固定中文。
- 数据：无迁移，无需数据回滚。
- 配置：无新增键。

---

## 10. 执行记录（2026-11-22）

| 验证项 | 结果 |
| --- | --- |
| 针对性后端测试 | `tests/test_report_html_renderer.py`、`tests/test_history_report_html_api.py`、`tests/test_history_share_image.py`、`tests/test_analysis_history.py` 全通过 |
| 后端全量离线 | `./scripts/ci_gate.sh offline-tests`：**9617 passed / 0 failed / 4 deselected / 1002 subtests passed**（752.14s） |
| 后端门禁 | `./scripts/ci_gate.sh syntax`、`./scripts/ci_gate.sh flake8` 通过 |
| Web | `npm run lint`、`npm run build` 通过；`npm test -- --run`：**1488 passed / 115 files** |
| AI 资产 | `python scripts/check_ai_assets.py`、`python scripts/gen_ai_codemap.py --check` 通过 |
| 未执行 | Docker / Electron 构建（本机无对应环境）；未提交 commit / PR（按 AGENTS.md 需另行确认） |


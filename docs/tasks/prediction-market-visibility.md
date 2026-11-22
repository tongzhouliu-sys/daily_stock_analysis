# 开发任务书：预测市场能力「可见 + 可用」补齐（P1.5）

> 状态：待实施
> 前置：P1 已合并（PR #36）
> 任务类型：`feat`
> 需求文档：`docs/tasks/prediction-market-visibility-requirements.md`
> 影响面：Agent 工具面 / Agent 模式分析 / 分析上下文 / 报告渲染 / Web 前端 / i18n / 部署文档 / 测试

---

## 1. 背景与现状

### 1.1 现状结论

P1 的数据链路是好的：采集 → 落库 → Prompt 注入 → 只读工具。但「用户看不到」是因为运行时还有四个断层：

| 断层 | 代码现状 | 结果 |
| --- | --- | --- |
| 多 Agent 工具白名单 | `src/agent/agents/intel_agent.py:28`、`risk_agent.py:33`、`portfolio_agent.py:48` 的 `tool_names` 未包含 `search_prediction_markets` | `AGENT_ARCH=multi` 时工具不可达 |
| Agent 模式分析 | `src/core/pipeline.py::_build_agent_analysis_artifacts` 只读 `initial_context`，未注入预测市场 | `AGENT_MODE=true` 时无证据 |
| 展示层 | 无 `prediction_markets` 结构化字段；Web 无组件；仅 `analysis_context_pack` 有状态行 | 数据只在 LLM 自由文本里 |
| 部署 | `docker/docker-compose.yml` 用 `env_file: ../.env`；`00-daily-analysis.yml` 用显式 env allowlist | 服务器/Actions 易漏配 |

### 1.2 可复用锚点

| 能力 | 锚点 |
| --- | --- |
| 结构化事件来源 | `PredictionMarketService.query_events()`、`_event_to_dict()` |
| legacy 注入 | `src/core/pipeline.py::_load_prediction_market_context()`（约 3348）、`_build_legacy_analysis_artifacts()` |
| Agent 上下文注入 | `src/agent/executor.py::_build_user_message()`（news_context 注入约 968）、`src/agent/orchestrator.py`（`meaningful_data_keys` 约 1747/1776）、`src/agent/stock_scope.py`（上下文键元组约 15） |
| 分析上下文块 | `src/services/analysis_context_builder.py::_build_prediction_market_block()`（约 478） |
| 报告对象 | `api/v1/report_builder.py::build_analysis_report()`（约 149） |
| 四条 renderer | `src/services/report_renderer.py`、`templates/report_markdown.j2`、`templates/report_wechat.j2`、`src/notification.py`、`src/services/history_service.py` |
| Web 报告 | `apps/dsa-web/src/components/report/ReportDetails.tsx`、`ReportNews.tsx`、`AnalysisContextSummary.tsx`、`ReportMarkdownPanel.tsx` |
| Web 问股 | `apps/dsa-web/src/components/chat/ChatMessageItem.tsx`、`ChatAnswer.tsx`、`ChatLiveProgress.tsx` |
| i18n | `apps/dsa-web/src/i18n/uiText.ts`、`src/report_language.py` |
| 部署 | `docker/docker-compose.yml`、`.github/workflows/00-daily-analysis.yml` |

---

## 2. 目标与非目标

### 2.1 目标

- G1：`AGENT_ARCH=multi` 下 `intel` / `risk`（`portfolio` 可选）可调用 `search_prediction_markets`。
- G2：`AGENT_MODE=true` 分析注入 `prediction_market_context`，与 legacy 一致。
- G3：报告/通知/历史新增确定性「预测市场」小节；Web 报告 + 问股新增结构化证据展示。
- G4：结构化 `prediction_markets` 随分析结果持久化，供 API/renderer/Web 共用。
- G5：部署文档补充 Docker/Actions 的 env 透传与排障命令。
- G6：只读、fail-open、默认关闭、不参与评分/权重。

### 2.2 非目标

- 不做交易/下单/套利；不纳入 `decision_score` / `strategy_synthesis` / 仓位权重。
- 不新增独立页面/导航。
- 不改 provider/model/base URL/回退策略。
- 不采集 politics（除非显式允许）。

---

## 3. 展示规格

### 3.1 报告文本（LLM 自由发挥 + 结构化兜底）

- **不**在 Markdown 模板/通知中强制插入小节；LLM 基于注入的 `prediction_market_context` 自由使用。
- 结构化 `prediction_markets` 通过 Web 报告卡片与问股证据块展示，作为权威兜底。
- 下面仅为 LLM 可能采用的示例格式，不是强制模板：

```markdown
### 预测市场现状
1. Will the Fed cut rates in December? —— 隐含概率 62%（24h成交额 420,000 / 2026-12-31）
   来源：https://kalshi.com/markets/...
2. ...

> 预测市场概率为群体定价，非投资建议，可能受流动性与合规限制影响。
```

- 三语标签与免责声明由 `report_language.py` 统一维护，禁止在模板硬编码中文。
- 空列表不渲染小节；缺失 `close_time` 显示「未提供」。

### 3.2 Web 报告卡片

- 新组件 `PredictionMarketsPanel`，位于 `ReportNews` 之后、`ReportDiagnostics` 之前。
- 每行：事件 + 概率徽标（按概率高低着色）+ 24h 成交额 + 数据时间 + 来源链接。
- 默认折叠或紧凑；无数据不渲染。
- 免责声明常驻卡片底部。

### 3.3 问股证据块

- `search_prediction_markets` 的 `tool_done` 结果在消息内以可折叠证据块展示。
- 复用现有工具卡片样式；无结果时显示「未获取到匹配的预测市场事件」。

---

## 4. 数据与接口契约

### 4.1 结构化证据项

见需求文档 §5.1。字段固定：`source / market_id / question / implied_probability / volume_24h / close_time / category / symbol / market / market_url`。

### 4.2 服务层

新增：

```python
PredictionMarketService.collect_events(
    *, symbol, market, categories, limit
) -> List[Dict[str, Any]]
```

- `build_evidence_context()` 内部改为调用 `collect_events()` 后格式化，保持现有签名与行为。
- `build_market_context()` 复用同一收集逻辑（macro/crypto/commodity + 本市场 equity）。

### 4.3 持久化（独立 sidecar）

采用独立 sidecar 表，不写入 `context_snapshot`：

```text
analysis_prediction_markets
- id (PK)
- analysis_history_id (index, FK -> analysis_history.id)
- source, market_id, question, implied_probability, volume_24h,
  close_time, category, symbol, market, market_url
- created_at
- UNIQUE(analysis_history_id, source, market_id)
```

- 分析历史成功保存后，best-effort 写入 sidecar；失败不影响主分析/历史保存。
- 历史删除在同一事务内清理 sidecar 行。
- 时间字段沿用该表所属时间基准（新增表需在 `tests/test_time_basis_contract.py` 登记）。
- 历史详情读取 sidecar 并映射到 `details.prediction_markets`；实时分析响应直接使用本次内存中的 events。

### 4.4 噪声过滤

新增配置：

```env
PREDICTION_MARKET_FILTER_NOISE=true
PREDICTION_MARKET_MIN_VOLUME_24H=1000
```

过滤规则（仅作用于证据收集与工具检索，`GET /prediction-markets/events` 保留全量）：

- `implied_probability` 为 `None`、`<= 0` 或 `>= 1` 时排除；
- `volume_24h` 为空或 `< PREDICTION_MARKET_MIN_VOLUME_24H` 时排除（阈值 `0` 关闭成交量过滤）。

### 4.5 API

- 现有 `GET /api/v1/prediction-markets/events` 不变。
- 历史/分析报告响应在 `details` 下新增 `prediction_markets: List[Dict]`（追加字段，向后兼容）。

---

## 5. 实现清单

### WS-A：后端可用性

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `src/agent/agents/intel_agent.py` | `tool_names` 增加 `"search_prediction_markets"` |
| 2 | `src/agent/agents/risk_agent.py` | 同上 |
| 3 | `src/agent/agents/portfolio_agent.py` | 加入 `"search_prediction_markets"` |
| 4 | `src/services/prediction_market_service.py` | 新增 `collect_events()` + 噪声过滤（`_is_noise`）；`build_evidence_context` / `build_market_context` 复用 |
| 5 | `src/config.py` + `.env.example` | 新增 `PREDICTION_MARKET_FILTER_NOISE`、`PREDICTION_MARKET_MIN_VOLUME_24H` |
| 6 | `src/core/pipeline.py` | agent 路径写入 `initial_context["prediction_market_context"]` 与 `["prediction_market_events"]`；`_build_agent_analysis_artifacts` 传入 |
| 7 | `src/agent/executor.py` | `_build_user_message` 注入预测市场块（对齐 `news_context`） |
| 8 | `src/agent/orchestrator.py` | `meaningful_data_keys` 等上下文键加入 `prediction_market_context` |
| 9 | `src/agent/stock_scope.py` | 上下文键元组加入 `prediction_market_context` |
| 10 | `src/services/analysis_context_builder.py` | block 的 `items`/metadata 写入结构化 `events` |

### WS-B：sidecar 与展示层

| # | 文件 | 改动 |
| --- | --- | --- |
| 11 | `src/storage.py` | 新增 sidecar 表 `analysis_prediction_markets` |
| 12 | `src/repositories/prediction_market_evidence_repo.py`（新） | sidecar 写入/查询/删除 |
| 13 | `src/services/prediction_market_service.py` / `history_service.py` | 历史保存后 best-effort 写 sidecar；历史删除同步清理 |
| 14 | `api/v1/report_builder.py` + `src/schemas/report_schema.py`（如需） | 历史详情读取 sidecar → `details.prediction_markets`；实时响应使用内存 events |
| 15 | `src/report_language.py` | 新增「预测市场」标签、免责声明、空态（zh/en/ko） |
| 16 | `apps/dsa-web/src/components/report/PredictionMarketsPanel.tsx`（新） | 报告卡片（LLM 自由文本的权威兜底） |
| 17 | `apps/dsa-web/src/components/report/ReportDetails.tsx` | 挂载卡片 |
| 18 | `apps/dsa-web/src/components/chat/ChatMessageItem.tsx` / `ChatAnswer.tsx` | 问股证据块 |
| 19 | `apps/dsa-web/src/i18n/uiText.ts` | 组件文案（zh/en；ko 复用英文结构） |

### WS-C：部署与排障

| # | 文件 | 改动 |
| --- | --- | --- |
| 19 | `docs/DEPLOY.md` / `docs/DEPLOY_EN.md` | Docker Compose / Actions 的 env 透传说明与排障命令 |
| 20 | `.github/workflows/00-daily-analysis.yml`（按需） | 显式透传 `PREDICTION_MARKET_*` / `KALSHI_*`（如希望 Actions 使用该能力） |
| 21 | `docs/CHANGELOG.md` | `[Unreleased]` 追加条目 |
| 22 | `docs/ai/FLOWS.md` / `DATA-FLOW.md` | 链路与字段契约同步 |

---

## 6. 验收用例

1. `AGENT_ARCH=single` 与 `AGENT_ARCH=multi` 下，intel/risk 专家都能调用 `search_prediction_markets`（工具面断言 + 端到端）。
2. `AGENT_MODE=false` 与 `AGENT_MODE=true` 分析都产出 `details.prediction_markets`，且报告出现「预测市场」小节。
3. 无数据 / 关闭时：不渲染小节、不报错、主分析正常。
4. 每条证据可跳转原始合约页；免责声明存在。
5. `zh/en/ko` 标签与免责声明正确；空态不硬编码「无/None」。
6. 历史详情、同步分析、任务状态回退三处 `details.prediction_markets` 一致。
7. 通知渠道包含该小节（或按渠道能力裁剪），单渠道失败不影响主流程。
8. `pytest -m "not network"` 全绿；`npm run lint && npm run build` 通过。

---

## 7. 兼容性与回滚

- 新增字段均为**追加**：`details.prediction_markets`、`PipelineAnalysisArtifacts.prediction_market_events`、上下文键。
- 旧历史记录无该字段时，renderer 与 Web 按「无数据」处理，不报错。
- 工具白名单变更只影响多 Agent 专家，不影响单 Agent。
- 回滚：revert 相关 PR；能力仍由 `PREDICTION_MARKET_ENABLED` 控制，关闭时行为等于现状。

---

## 8. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| 多 Agent 工具面 | `pytest tests/test_multi_agent.py tests/test_fxmacrodata_tools.py`；断言 `tool_names` 含新工具 |
| Agent 模式注入 | `pytest tests/test_agent_pipeline.py`；断言 `initial_context` 与消息包含预测市场块 |
| 报告/通知 | `pytest tests/test_*renderer* tests/test_notification* tests/test_history*` |
| Web | `cd apps/dsa-web && npm run lint && npm test -- --run && npm run build` |
| 文档 | 核对命令、路径、workflow 名称与仓库一致 |

> 涉及报告格式/UI 变化时，PR 描述需附前后截图（AGENTS.md 硬规则）。

---

## 9. PR 拆分建议

- **PR-1（WS-A）**：工具白名单 + Agent 模式注入 + `collect_events`。
- **PR-2（WS-B 后端）**：报告/通知/历史渲染 + `details.prediction_markets`。
- **PR-3（WS-B 前端）**：Web 报告卡片 + 问股证据块 + i18n。
- **PR-4（WS-C）**：部署文档 + Actions 透传（按需）。

每个 PR 独立可回滚，均不改变 P1 的只读/默认关闭/fail-open 边界。

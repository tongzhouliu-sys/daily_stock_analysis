# 开发任务书：预测市场全景（合约数量 / 关注焦点 / 整体态度）

> 状态：待实施
> 前置：P1 + P1.5 已合并（`origin/main`）
> 任务类型：`feat`
> 需求文档：`docs/tasks/prediction-market-overview-requirements.md`
> 影响面：预测市场服务 / Agent 工具 / 个股分析 Prompt / 大盘复盘 Prompt / API / 测试 / 文档

---

## 1. 背景与现状

现有能力是「逐事件证据」：`PredictionMarketService.collect_events()`、`search_prediction_markets` 工具、报告卡片。缺少聚合视角。

| 能力 | 代码锚点 |
| --- | --- |
| 本地事件池查询 | `src/services/prediction_market_service.py::query_events` |
| 噪声过滤 | 同文件 `_is_noise` |
| 逐事件证据 | 同文件 `collect_evidence` / `build_evidence_context` |
| 问股工具 | `src/agent/tools/prediction_market_tools.py` |
| 工具注册 | `src/agent/factory.py::_build_tool_registry` |
| 个股注入 | `src/core/pipeline.py::_load_prediction_market_evidence` |
| 大盘注入 | `src/market_analyzer.py::_load_prediction_market_context` |
| API | `api/v1/endpoints/prediction_markets.py` |
| 专家白名单 | `src/agent/agents/{intel,risk,portfolio}_agent.py` |

## 2. 目标与非目标

### 2.1 目标

- G1：`build_overview()` 输出合约总数、类别/来源分布、热门事件、关注集中度。
- G2：`attitude` 用可解释的 risk-on / risk-off 分组给出倾向与样本数。
- G3：`build_overview_context()` 生成 Markdown 概览，注入个股分析与大盘复盘 Prompt。
- G4：新增只读工具 `get_prediction_market_overview`，并加入 intel/risk/portfolio 白名单。
- G5：新增 `GET /api/v1/prediction-markets/overview`。
- G6：只读、fail-open、默认关闭、不参与评分。

### 2.2 非目标

- 不做交易/下单/套利；不纳入 `decision_score` / `strategy_synthesis` / 仓位。
- 不新增独立页面/导航。
- 不改 provider/model/路由/配置迁移语义。

## 3. 数据与接口契约

### 3.1 `build_overview()` 返回

```json
{
  "enabled": true,
  "total_contracts": 214,
  "by_category": {"macro": 112, "equity": 47, "crypto": 55, "commodity": 0},
  "by_source": {"kalshi": 181, "polymarket": 33},
  "top_events": [{"source": "kalshi", "market_id": "...", "question": "...", "implied_probability": 0.62, "volume_24h": 420000, "category": "macro", "market_url": "..."}],
  "attention_concentration": 0.41,
  "attitude": {
    "label": "中性/分歧 | 偏 risk-on | 偏 risk-off | 样本不足",
    "tilt": 0.02,
    "risk_on": {"count": 20, "avg_probability": 0.55},
    "risk_off": {"count": 15, "avg_probability": 0.53}
  },
  "generated_at": "2026-11-23T..."
}
```

- 未启用：`{"enabled": false, "total_contracts": 0, ...}`。
- 噪声过滤沿用 `_is_noise`。

### 3.2 概览文本

见需求文档 §5.1。固定结构，带免责声明。

### 3.3 注入

- 个股：`_load_prediction_market_evidence` 里把 `overview_context` 拼在逐事件文本前。
- 大盘：`_load_prediction_market_context` 里把 `overview_context` 拼在 `build_market_context` 前。

### 3.4 API

- `GET /api/v1/prediction-markets/overview?market=&limit=`，返回 §3.1。

## 4. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `src/services/prediction_market_service.py` | 新增 `build_overview()`、`build_overview_context()`、`_classify_stance()`、`_mean_probability()` |
| 2 | `src/agent/tools/prediction_market_tools.py` | 新增 `get_prediction_market_overview` 工具并加入 `ALL_PREDICTION_MARKET_TOOLS` |
| 3 | `src/agent/agents/intel_agent.py` / `risk_agent.py` / `portfolio_agent.py` | 白名单加入新工具 |
| 4 | `src/core/pipeline.py` | `_load_prediction_market_evidence` 拼接概览文本 |
| 5 | `src/market_analyzer.py` | `_load_prediction_market_context` 拼接概览文本 |
| 6 | `api/v1/schemas/prediction_markets.py` / `api/v1/endpoints/prediction_markets.py` | `/overview` 契约与端点 |
| 7 | `tests/test_prediction_market.py` | 聚合统计、态度分组、工具、注入拼接测试 |
| 8 | `docs/CHANGELOG.md` / `docs/ai/CODEMAP.md` / `DATA-FLOW` | 同步 |

## 5. 验收用例

1. 池中 5 个 macro + 3 个 crypto 时，`total_contracts=8`、`by_category` 正确。
2. 极端概率/低成交量事件被过滤，不进入概览。
3. `attitude` 分组与 `tilt` 计算正确；样本不足时 label 为 `样本不足`。
4. 个股/大盘 Prompt 文本包含「预测市场全景」与免责声明。
5. 问股工具返回 `enabled/total_contracts/attitude`，未启用时返回 `enabled=false`。
6. `/overview` 未启用返回 `enabled=false`，不报 500。
7. 不参与评分/权重；关闭时行为等于现状。

## 6. 兼容性与回滚

- 全部为追加：新方法、新工具、新端点、注入文本。
- 关闭 `PREDICTION_MARKET_ENABLED` 时概览返回空/`enabled=false`，不注入。
- 回滚：revert 本任务提交；sidecar/事件池不受影响。

## 7. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| 服务/工具 | `pytest tests/test_prediction_market.py -m "not network"` |
| 注入 | `pytest tests/test_agent_pipeline.py tests/test_market_analyzer_generate_text.py` |
| API | `pytest tests/test_api_schema_pydantic.py`；接口冒烟 |
| 静态 | `py_compile`、`./scripts/ci_gate.sh flake8`、`gen_ai_codemap.py --check` |
| Web 概览卡 | `npm run lint && npm run build && npx vitest run` |

## 8. 落地说明

- Web 报告新增 `PredictionMarketOverviewPanel` 摘要卡，读取 `GET /api/v1/prediction-markets/overview`；接口使用 `refresh=false`，查看历史报告时不会触发外部刷新。
- 卡片展示：活跃合约数 + 类别分布、关注集中度、Top 3 事件、risk-on/risk-off 倾向与免责声明；未启用或池为空时整卡不渲染。
- 问股：当本轮分析过程使用过 `search_prediction_markets` / `get_prediction_market_overview` 时，在回答下方复用同一摘要卡；未使用工具时不渲染。

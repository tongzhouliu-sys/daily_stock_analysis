# 需求文档：预测市场能力「可见 + 可用」补齐（P1.5）

> 状态：需求待评审
> 前置：P1 已合并（PR #36，`prediction-market-intelligence`）
> 关联任务书：`docs/tasks/prediction-market-visibility.md`
> 影响面：多 Agent 工具面 / Agent 模式分析 / 报告渲染 / Web 前端 / i18n / 部署文档 / 测试

---

## 1. 背景与问题

P1 已交付「预测市场采集 → 落库 → Prompt 注入 → 只读工具」，但实际部署后用户反馈：**问股界面和分析界面看不到预测市场数据**。排查后确认这不是单一配置问题，而是 P1 设计范围与运行时链路共同导致的四个缺口：

| # | 问题 | 现象 |
| --- | --- | --- |
| P1 | 多 Agent 专家工具白名单不含 `search_prediction_markets` | `AGENT_ARCH=multi` 时问股永远调不到该工具 |
| P2 | Agent 模式分析不注入 `prediction_market_context` | `AGENT_MODE=true` 时分析报告没有预测市场证据 |
| P3 | 报告/问股没有独立的「预测市场」展示 | 数据只在 LLM Prompt 里，模型不提就完全看不见；Web 无结构化卡片 |
| P4 | 部署文档未说明 env 透传 | Docker 需服务器 `.env`；GitHub Actions 默认 allowlist 不会注入新变量 |

其中 P1–P3 是代码能力缺口，P4 是运维可见性缺口。本需求只解决 P1–P3 与必要的部署说明，不改变 P1 已有的只读、默认关闭、fail-open 边界。

## 2. 目标与非目标

### 2.1 目标

- G1：多 Agent 模式下，至少 `intel`、`risk` 专家可调用 `search_prediction_markets`；单 Agent 与多 Agent 行为一致。
- G2：Agent 模式分析（`AGENT_MODE=true`）注入 `prediction_market_context`，与 legacy 分析路径一致。
- G3：报告与问股新增**独立、结构化**的「预测市场」展示：事件、隐含概率、24h 成交额、数据时间、来源链接、免责声明；无数据时不空占位。
- G4：结构化预测市场证据随分析结果/历史快照持久化，可被 API、Markdown、通知与 Web 复用，而不是只存在于 Prompt。
- G5：部署文档明确 Docker Compose 与 GitHub Actions 的 env 透传方式。
- G6：保持只读、fail-open、默认关闭、不参与 `decision_score` / `strategy_synthesis` / 仓位权重。

### 2.2 非目标

- 不做预测市场交易、下单、套利或任何写操作。
- 不把概率换算成买卖强度、仓位或评分权重（留待 P2 单独契约）。
- 不新增独立页面或导航入口；首版在报告详情与问股消息内嵌。
- 不改模型名、provider、`base URL`、默认模型策略、回退策略或运行时配置迁移语义。
- 不接入非授权门户直抓、反爬、模拟登录。

## 3. 用户故事与验收场景

1. **问股（单 Agent）**：用户问「美联储 12 月降息概率」「市场怎么看 NVDA 财报」，回答中包含至少一条带隐含概率与来源链接的预测市场证据。
2. **问股（多 Agent）**：`AGENT_ARCH=multi` 下，intel/risk 专家同样能检索并引用预测市场证据，行为与单 Agent 一致。
3. **个股分析（legacy）**：分析报告出现「预测市场」小节，列出相关事件、概率、24h 成交额、数据时间、来源链接与免责声明。
4. **个股分析（Agent 模式）**：`AGENT_MODE=true` 时同样出现上述小节，与 legacy 一致。
5. **无数据降级**：预测市场关闭、拉取失败或池为空时，不渲染空小节/空卡片，不报堆栈，不阻塞分析。
6. **可追溯**：每个证据项可点击跳转到 Kalshi/Polymarket 原始合约页。
7. **运维可见**：`GET /api/v1/prediction-markets/providers|events` 与日志可判断是否启用、是否拉到数据。
8. **多语言**：中文/英文/韩文下「预测市场」标签、免责声明、空态文案正确。

## 4. 功能范围

### WS-A：后端可见性与可用性

- 多 Agent 专家工具白名单补充 `search_prediction_markets`（`intel`、`risk`；`portfolio` 可选）。
- Agent 模式分析注入 `prediction_market_context` 与结构化 `prediction_markets`。
- `PredictionMarketService` 新增结构化收集方法，供 legacy / agent / 报告复用。
- 结构化证据随分析结果持久化（历史快照），供历史详情、通知、Web 读取。

### WS-B：展示层

- Markdown 报告新增「预测市场」小节（仅在存在证据时渲染）。
- 飞书/微信通知、历史详情保持一致（复用同一份结构化数据）。
- Web 报告详情新增 `PredictionMarketsPanel` 卡片。
- 问股消息新增预测市场证据块（工具调用结果可折叠展示）。
- i18n（zh/en/ko）补齐标签、免责声明、空态。

### WS-C：部署与排障文档

- Docker Compose：说明 `env_file: ../.env`，服务器需在仓库根 `.env` 配置。
- GitHub Actions：说明默认 env allowlist 不会自动透传，需在 `00-daily-analysis.yml` 显式映射。
- 提供 `/providers`、`/events`、`/fetch` 的排障命令。

## 5. 数据与展示契约

### 5.1 结构化证据项（统一低敏口径）

```json
{
  "source": "kalshi | polymarket",
  "market_id": "KXFED-26DEC",
  "question": "Will the Fed cut rates in December?",
  "implied_probability": 0.62,
  "volume_24h": 420000.0,
  "close_time": "2026-12-31T00:00:00Z",
  "category": "macro | equity | commodity | crypto",
  "symbol": "NVDA | null",
  "market": "us | global",
  "market_url": "https://kalshi.com/markets/..."
}
```

- 不包含 API key、token、webhook、raw diagnostics。
- `implied_probability` 归一为 `0.0–1.0`；展示时换算百分比。
- 无 `close_time` 时展示「未提供」。

### 5.2 报告小节（Markdown）

```markdown
### 预测市场现状
1. Will the Fed cut rates in December? —— 隐含概率 62%（24h成交额 420,000 / 2026-12-31）
   来源：https://kalshi.com/markets/...
2. ...

> 预测市场概率为群体定价，非投资建议，可能受流动性与合规限制影响。
```

- 仅在 `prediction_markets` 非空时渲染；空数据不出现该小节。
- 三语标签与免责声明由 `report_language.py` 统一维护。

### 5.3 Web 展示

- 报告详情：「预测市场」卡片，位于资讯区之后、运行诊断之前。
- 问股：工具 `search_prediction_markets` 的结果以可折叠证据块展示（复用现有证据/工具卡片样式）。
- 默认折叠或紧凑展示，避免长列表淹没报告。

## 6. 边界与合规

- 只读、fail-open、默认关闭（`PREDICTION_MARKET_ENABLED=false`）。
- 不参与评分/权重/仓位；不写入 `decision_score`、`strategy_synthesis`、`DecisionSignal` 的数值语义。
- 来源链接可追溯；展示免责声明。
- 不采集 politics（除非显式允许）；不泄露敏感信息。
- 报告/通知失败不影响主分析链路。

## 7. 分期与优先级

| 阶段 | 内容 | 优先级 |
| --- | --- | --- |
| P1.5a | 多 Agent 工具白名单 + Agent 模式注入 + 结构化事件收集 | 高 |
| P1.5b | 报告/通知/历史 Markdown 小节 | 高 |
| P1.5c | Web 报告卡片 + 问股证据块 + i18n | 高 |
| P1.5d | 部署与排障文档 | 中 |
| P2 | 概率纳入决策/权重（单独契约，需反例） | 低 |

## 8. 决策记录（已确认）

| # | 问题 | 决策 |
| --- | --- | --- |
| 1 | Web 报告卡片与问股证据块是否都做 | **都做**：报告卡片 + 问股证据块 |
| 2 | `portfolio` 专家是否加入工具 | **加入**：`intel` / `risk` / `portfolio` 三个专家 |
| 3 | 结构化证据存储位置 | **单独 sidecar**（不写入 `context_snapshot`） |
| 4 | 报告小节渲染方式 | **继续 LLM 自由发挥 + 结构化卡片兜底**（不强制确定性 Markdown 小节） |
| 5 | 是否过滤噪声 | **过滤**：排除 `0%/100%` 极端概率与低成交量事件（阈值可配置） |

### 8.1 决策带来的设计调整

- 结构化证据使用独立 sidecar 表 `analysis_prediction_markets`，按 `analysis_history_id` 关联；历史删除同步清理。
- 报告 Markdown 不新增强制小节，LLM 仍基于注入上下文自由发挥；Web 卡片/问股证据块是权威的结构化展示。
- 噪声过滤在证据收集与工具检索层生效，原始 `GET /prediction-markets/events` 保留全量用于诊断。
- 新增配置：`PREDICTION_MARKET_FILTER_NOISE`（默认 true）、`PREDICTION_MARKET_MIN_VOLUME_24H`（默认 1000，0 关闭）。

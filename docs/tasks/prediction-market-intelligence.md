# 开发任务书：问股与分析接入「预测市场现状」

> 状态：已实施（P1）
> 基线：待定（实施前锁定）
> 任务类型：`feat`
> 影响面：数据源适配层 / 存储层 / 业务服务层 / 问股 Agent 工具 / 个股分析 / 大盘复盘 / 报告渲染 / i18n / 配置 / 测试 / 文档
> 需求背景：见本任务书 §1（原需求文档已并入，不再单独维护）

---

## 1. 背景与现状

### 1.1 用户诉求

在「问股」和「个股分析」「大盘复盘」中，新增一类独立于新闻/技术面/基本面的前瞻性信号：**预测市场（Prediction Market）对事件概率的定价**。典型场景：

- 「美联储 12 月是否降息」的隐含概率（Kalshi / Polymarket）
- 「NVIDIA 下季度财报是否超预期」的隐含概率（Kalshi 已上线个股事件合约）
- 「BTC 年底是否到 $150k」「Brent 原油月底价格区间」等宏观/商品事件

诉求是把这类数据作为**独立分析维度**呈现，让报告/回答能引用"市场现在把某事件定价为 X% 概率"这类量化证据，并附带来源链接与免责声明。

### 1.2 现有链路（复用锚点）

| 能力 | 现有锚点 | 复用点 |
| --- | --- | --- |
| 资讯源采集/落库 | `intelligence_items` 表、`docs/intelligence-sources.md` | fail-open 拉取、去重、retention、`symbol/market` 作用域 |
| 个股情报注入 | `src/core/pipeline.py` `_load_persisted_intelligence_context()`（约 772）、`_build_legacy_analysis_artifacts()`（约 894）、`analyzer.analyze(news_context=...)`（约 933） | 预测市场上下文按同一条链路注入 |
| 上下文包 | `src/services/analysis_context_builder.py` `PipelineAnalysisArtifacts`（约 58） | 新增字段 |
| 大盘复盘 payload | `src/market_analyzer.py` `build_market_review_payload()`（约 1049） | 合并宏观事件 |
| 问股工具 | `src/agent/tools/market_tools.py`（`_MARKET_READ_POLICY` + `ToolDefinition` + `ALL_MARKET_TOOLS`）、`src/agent/factory.py` `_build_tool_registry()`（约 350） | 新增只读工具并注册 |
| 外部只读工具 | `src/agent/tools/fxmacrodata_tools.py` | 密钥只从环境读，不进 schema/参数/响应 |

### 1.3 需要解决的问题

1. 问股 Agent 无预测市场工具，无法回答事件概率类问题。
2. 个股/大盘报告对事件驱动风险只有新闻措辞，缺量化概率证据。
3. Polymarket / Kalshi 字段各异，需统一标准化，下游不得各自解析。
4. 外部第三方数据必须遵守 fail-open、超时、脱敏、去重、retention，且默认关闭。

---

## 2. 目标与非目标

### 2.1 目标

- G1：新增预测市场采集层，标准化 Kalshi / Polymarket 数据并落库，支持按 `symbol/market/category` 检索。
- G2：问股 Agent 新增只读工具 `search_prediction_markets`，事件驱动类提问可自主检索并引用概率证据。
- G3：个股分析 best-effort 注入 `prediction_market_context`；大盘复盘合并宏观事件到 `market_review_payload`。
- G4：报告新增可选「预测市场」小节（仅在存在证据时渲染），含概率、24h 成交额、数据时间、来源链接与免责声明。
- G5：单一平台失败、未配置、超时不阻塞任何主链路；全能力默认关闭。

### 2.2 非目标

- 不做预测市场交易/下单/套利，本能力只读。
- 不把概率换算成仓位、买卖强度或 `decision_score`/`strategy_synthesis` 数值权重。
- 不新增独立前端页面/导航入口（首版只做报告与问股内嵌展示）。
- 不改模型名、provider、`base URL`、默认模型策略、回退策略或运行时配置迁移语义。
- 不接入非授权门户直抓、反爬、模拟登录或 Cookie 抓取。

---

## 3. 数据源与字段契约

### 3.1 平台接入（P1 固定范围）

| 平台 | 端点（默认） | 覆盖 |
| --- | --- | --- |
| Kalshi | `https://api.elections.kalshi.com/trade-api/v2` | 宏观（Fed/CPI/失业率/GDP）、个股事件（财报/价格阈值）、商品 |
| Polymarket | `https://gamma-api.polymarket.com` | 宏观、加密、个股事件 |

- P1 直接对接官方 API，**不接**聚合中间层（CoinMetrics/Apify/Tatum），留作 P2 扩展/降级。
- 类别白名单硬编码：只采集 `macro` / `equity` / `commodity` / `crypto`，`politics` **默认排除**。

### 3.2 标准化字段（下游唯一消费口径）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source` | `str` | `polymarket` / `kalshi` |
| `event_id` | `str` | 平台原生事件标识 |
| `market_id` | `str` | 平台原生合约标识（**去重键**） |
| `question` | `str` | 事件描述 |
| `outcome` | `str` | `yes` / `no` 或多选项标签 |
| `implied_probability` | `float` | 隐含概率 `0.0–1.0` |
| `yes_bid` / `yes_ask` / `last_price` | `float \| null` | 买卖价与最新成交价 |
| `volume` / `volume_24h` | `float \| null` | 累计 / 24h 成交额 |
| `open_interest` | `float \| null` | 未平仓量 |
| `liquidity_usd` | `float \| null` | 流动性 |
| `status` | `str` | `open` / `closed` / `settled` |
| `open_time` / `close_time` | `str(ISO)` | 合约起止（UTC） |
| `resolution` | `str \| null` | 结算结果 |
| `category` | `str` | `macro` / `equity` / `commodity` / `crypto` |
| `tags` | `list[str]` | 平台原始标签（低敏） |
| `market_url` | `str` | 来源链接 |
| `symbol` / `market` | `str \| null` | 关联标的与市场（`cn/hk/us/...`） |

- **去重**：`(source, market_id)` 唯一；无 `market_id` 用 `no-market-id:<hash>` 兜底。
- 非法/非有限概率（`<0` 或 `>1`、`NaN`）直接丢弃，不 clamp 后入库。

---

## 4. 数据与接口契约

### 4.1 存储

新增表 `prediction_market_events`（`Base.metadata.create_all` 创建，无 `ALTER TABLE` 迁移）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `INTEGER PK` | 自增 |
| `source` | `VARCHAR(16)` | 平台 |
| `market_id` | `VARCHAR` | 去重键（唯一索引） |
| `event_id` | `VARCHAR` | 事件标识 |
| `question` | `TEXT` | 事件描述 |
| `outcome` | `VARCHAR` | 结算方向 |
| `implied_probability` | `REAL` | 隐含概率 |
| `yes_bid` / `yes_ask` / `last_price` | `REAL NULL` | 买卖价/最新价 |
| `volume_24h` / `open_interest` / `liquidity_usd` | `REAL NULL` | 数值 |
| `status` | `VARCHAR` | 状态 |
| `category` | `VARCHAR` | 类别 |
| `tags` | `TEXT`（JSON） | 标签 |
| `open_time` / `close_time` | `VARCHAR` | ISO 字符串 |
| `resolution` | `VARCHAR NULL` | 结算 |
| `symbol` / `market` | `VARCHAR NULL` | 关联标的/市场 |
| `market_url` | `TEXT` | 来源链接 |
| `fetched_at` | `DATETIME` | 采集时间（**UTC naive**） |
| `updated_at` | `DATETIME` | 平台数据时间（**UTC naive**） |

- 时间基准走 **UTC naive**（`utc_naive_now`），并在 `tests/test_time_basis_contract.py` 的 `UTC_TIME_TABLES` 登记（见 `docs/ai/DATA-FLOW.md` §6）。
- retention：`PREDICTION_MARKET_RETENTION_DAYS`（默认 30）清理已结算/超期记录。
- 数据访问走 `src/repositories/prediction_market_repo.py`，不在端点写 SQL。

### 4.2 问股工具契约

新增 `src/agent/tools/prediction_market_tools.py`：

```python
_PREDICTION_MARKET_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read"],
    permissions=["prediction_market:read"],
)

search_prediction_markets_tool = ToolDefinition(
    name="search_prediction_markets",
    description="Search prediction-market event probabilities (Kalshi/Polymarket) "
                "for macro, equity, commodity or crypto events, e.g. Fed rate decision, "
                "earnings beat, price thresholds. Returns implied probability, 24h volume, "
                "close time and source URL.",
    parameters=[
        ToolParameter(name="query", type="string", required=False,
                      description="Event keyword, e.g. 'fed rate', 'nvda earnings'"),
        ToolParameter(name="symbol", type="string", required=False,
                      description="Optional stock code, e.g. 'NVDA'"),
        ToolParameter(name="market", type="string", required=False,
                      default="us", enum=["cn", "hk", "us", "global"],
                      description="Market scope"),
        ToolParameter(name="category", type="string", required=False,
                      enum=["macro", "equity", "commodity", "crypto"],
                      description="Event category"),
        ToolParameter(name="status", type="string", required=False,
                      default="open", enum=["open", "closed", "settled"]),
        ToolParameter(name="limit", type="integer", required=False, default=5),
    ],
    handler=_handle_search_prediction_markets,
    category="market",
    policy=_PREDICTION_MARKET_READ_POLICY,
)

ALL_PREDICTION_MARKET_TOOLS = [search_prediction_markets_tool]
```

- handler 调用 `PredictionMarketService.query_local(...)` 读本地池；若 `PREDICTION_MARKET_AUTO_FETCH_ENABLED=true` 且冷却过期，先 fail-open `fetch()`。
- 未配置/不可用/异常统一返回 `{"status": "unavailable", "items": [], "reason": "<脱敏原因>"}`，不抛堆栈、不泄露凭据。
- 工具在 `src/agent/factory.py::_build_tool_registry()` 中 import 并加入注册元组（`ALL_PREDICTION_MARKET_TOOLS`）。

### 4.3 HTTP（首版最小集）

接口位于 `/api/v1/prediction-markets`，继承 `/api/v1/*` 管理员鉴权：

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/prediction-markets/events` | `?symbol=&market=&category=&status=&days=` | `{"items": [§3.2 字段...], "total": n}` |
| `POST` | `/api/v1/prediction-markets/fetch` | `{"dry_run": false}` | `{"fetched": n, "sources": {...}, "errors": [...]}` |
| `GET` | `/api/v1/prediction-markets/providers` | — | `{"providers": [{"name", "enabled", "available", "last_error"}]}` |

- 未启用/未配置时返回明确错误码，不伪造空成功。
- 响应只含 §3.2 低敏字段 + 来源链接；不含 API key、token、webhook 或 raw diagnostics。
- `POST /fetch` 对任意来源 `dry_run=true` 不落库，用于测试。

---

## 5. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `data_provider/prediction_market_fetcher.py`（新） | Kalshi/Polymarket HTTP 客户端；统一标准化到 §3.2；fail-open；超时/降级；地址校验与代理禁用 |
| 2 | `src/storage.py` | 新增 `PredictionMarketEvent` 模型与 `prediction_market_events` 表；`(source, market_id)` 去重 upsert、retention 清理、查询 |
| 3 | `src/repositories/prediction_market_repo.py`（新） | 数据访问封装 |
| 4 | `src/services/prediction_market_service.py`（新） | 采集编排、进程内冷却、`symbol/market/category` 过滤、证据文本格式化 |
| 5 | `src/agent/tools/prediction_market_tools.py`（新） | §4.2 工具 + `ALL_PREDICTION_MARKET_TOOLS` |
| 6 | `src/agent/factory.py` | `_build_tool_registry()` 导入并注册 `ALL_PREDICTION_MARKET_TOOLS` |
| 7 | `src/core/pipeline.py` | 个股分析：新增 `_load_prediction_market_context()`，把 `prediction_market_context` 传入 `_build_legacy_analysis_artifacts()`（约 894）与 `analyzer.analyze()`（约 933） |
| 8 | `src/services/analysis_context_builder.py` | `PipelineAnalysisArtifacts`（约 58）新增 `prediction_market_context: Optional[str] = None` |
| 9 | `src/analyzer.py` | `analyze()` 新增可选参数 `prediction_market_context: Optional[str] = None`；prompt 组装时作为独立数据块注入（含数据时间/来源/免责声明）。**最小、可解释、可回滚补丁，不做其它重构** |
| 10 | `src/market_analyzer.py` | `build_market_review_payload()`（约 1049）合并同市场 `category=macro` 事件 |
| 11 | `api/v1/endpoints/prediction_markets.py`（新） | §4.3 三个接口 |
| 12 | 配置注册 | 新增 §6 键，同步 `.env.example` 与配置注册表（三处声明同步，见 `docs/config-single-source.md`） |
| 13 | `src/report_language.py`、`templates/report_markdown.j2`、`templates/report_wechat.j2` | 「预测市场」小节文案与免责声明（zh/en/ko） |
| 14 | `tests/` | 见 §8 验证矩阵 |
| 15 | `docs/ai/DATA-FLOW.md`、`docs/CHANGELOG.md` | 新数据源记录与变更说明 |

---

## 6. 配置项

全部默认关闭；新增键同步 `.env.example`：

```env
PREDICTION_MARKET_ENABLED=false
PREDICTION_MARKET_AUTO_FETCH_ENABLED=false
PREDICTION_MARKET_REFRESH_COOLDOWN_MIN=60
PREDICTION_MARKET_FETCH_TIMEOUT_SEC=10
PREDICTION_MARKET_RETENTION_DAYS=30
PREDICTION_MARKET_MAX_ITEMS_PER_FETCH=200

KALSHI_API_BASE_URL=https://api.elections.kalshi.com/trade-api/v2
KALSHI_API_KEY=
POLYMARKET_API_BASE_URL=https://gamma-api.polymarket.com
POLYMARKET_API_KEY=
```

- `PREDICTION_MARKET_ENABLED=false` 时不发外部请求、不注入上下文。
- `KALSHI_API_BASE_URL` / `POLYMARKET_API_BASE_URL` 留默认官方端点，可覆盖；生产部署前核验可达性与返回契约。

---

## 7. 验收用例

1. 未配置 / `PREDICTION_MARKET_ENABLED=false`：问股与个股/大盘分析行为与现状完全一致，无外部请求、无「预测市场」小节。
2. 启用后，问股事件驱动类提问（如「市场怎么看 NVDA 财报」）返回带 `implied_probability`、`volume_24h`、`close_time`、`market_url` 的证据；数据源不可用时返回 `status=unavailable`，无堆栈、无凭据泄露。
3. 个股/大盘报告仅在存在证据时渲染「预测市场」小节，概率/数据时间/来源链接与落库一致；空数据不出现空占位。
4. 单平台失败不阻塞另一平台与分析主链路（fail-open）。
5. 去重/retention 生效：同一 `(source, market_id)` 不重复堆积；超期/已结算记录按 retention 清理。
6. 敏感信息不进入报告/通知/日志：无 API key、token、webhook、内网地址或 raw diagnostics。
7. `zh/en/ko` 文案与免责声明正确；空列表走 `labels.none_label`，不硬编码「无/None」。
8. `tests/test_time_basis_contract.py` 对新表时间基准登记通过；`pytest -m "not network"` 全绿。

---

## 8. 兼容性与回滚

- 全能力默认关闭，新增表/字段/工具均为**追加**，不修改既有表结构、字段契约、模型/路由语义。
- `prediction_market_context` 为 best-effort 追加，不改变 `news_context` / `market_review_payload` 既有结构；失败或关闭时等于现状。
- `analyzer.py` 仅新增一个可选参数与一个 prompt 数据块，遵循「最小、可解释、可回滚」补丁原则。
- 回滚：`revert` 本 PR；如需仅降级，运行时停用并清理 `prediction_market_events` 存量与相关配置，不影响原有分析链路。
- 不新增环境级回滚开关；能力启停由 `PREDICTION_MARKET_ENABLED` 决定。

---

## 9. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| Python 采集/存储/服务 | `python -m pytest tests/test_prediction_market*.py tests/test_time_basis_contract.py -m "not network"`、`python -m py_compile` 改动文件 |
| 问股工具 | 只读契约、`unavailable` 降级、凭据不进参数/响应、factory 注册不破坏既有工具面 |
| 报告/模板 | 有证据渲染 + 空数据不渲染 + 三语文案 + 免责声明 |
| 联网 smoke（观测） | `pytest -m network`（可选）：Kalshi/Polymarket 官方端点可达性与字段契约核验 |
| 文档 | 核对命令、接口路径、文件名、`.env.example` 键名与仓库一致 |

---

## 10. 分期

- **P1（本任务书范围）**：采集 + 落库 + 问股工具 + 个股/大盘注入 + 报告展示，全默认关闭，不参与评分权重。
- **P1.5（可选）**：问股预测市场证据计数（`src/agent/news_evidence.py` 的 ContextVar 模式）；`/prediction-markets/events` 的 Web 只读视图。
- **P2（后续单独评审）**：概率纳入决策/技能观点，需先建立「不把概率当确定性权重」的契约与反例，且不违反 `docs/multi-strategy-contract.md` Baseline 不变量。
- **P3（可选）**：聚合中间层降级、多平台扩展、概率历史序列（price history）与走势图。

---

## 11. P1 落地说明（与任务书的差异）

- **报告展示采用 Prompt 注入而非新增模板小节**：个股分析在 `analyzer._format_prompt` 追加独立 `🎲 预测市场现状` 数据块，大盘复盘在 `_build_review_prompt` 追加 `## 预测市场现状`；不修改四条 renderer 与 Jinja 模板，避免破坏 `multi-strategy-contract.md` 的 renderer 一致性契约。由 LLM 在既有报告正文中自然引用。
- **配置为 env-only**：新增键按 `NEWS_INTEL_*` 先例只写入 `src/config.py` 与 `.env.example`（注释态），不进入 `config_registry` / `system_config_service`，避免扩大三处声明同步面。
- **不新增 `prediction_markets` 结构化 payload 字段**：大盘复盘仅做 Prompt 级合并；结构化字段留待 P1.5。
- **未做 DNS 私网校验**：平台 base URL 来自运维环境变量（非用户输入），仅强制 https、禁止凭据、禁用环境代理；若后续开放自定义 URL 再补 DNS 二次校验。
- **已实现**：采集/标准化/去重/retention、`prediction_market_events` 表、repo、service、Agent 只读工具、个股+大盘 Prompt 注入、`AnalysisContextPack` 辅助块与 label、三个 API、配置与 `.env.example`、UTC 时间基准登记、测试与文档。

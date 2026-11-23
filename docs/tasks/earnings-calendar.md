# 开发任务书：分析 / 问股接入「财报日历（下一次财报披露日期）」

> 状态：已实施（P1）
> 基线：待实施前锁定
> 任务类型：`feat`
> 影响面：数据源适配层（A股 akshare / 美股 yfinance）/ 基本面聚合层 / 报告渲染 / 问股 Agent 工具返回 / i18n / 测试 / 文档
> 结论依据：`docs/ai/CODEMAP.md`、`docs/ai/DATA-FLOW.md`、`docs/analysis-context-pack.md`、`data_provider/fundamental_adapter.py`、`data_provider/yfinance_fundamental_adapter.py`、`data_provider/base.py`、`src/analyzer.py`、`src/agent/tools/data_tools.py`

---

## 1. 背景与现状

### 1.1 用户诉求

当前系统在「个股分析」和「问股」里能拿到**已披露**的财报数据（财务报表、业绩预告、业绩快报、分红），但**没有任何前瞻性的「下一次财报什么时候披露」数据**。缺失的典型场景：

- 「贵州茅台下次年报 / 一季报什么时候披露？」
- 「距 NVDA 下次财报还有几天，临近披露波动是否会放大？」
- 报告里那句「财报披露前波动可能放大」目前只是 LLM 的泛泛措辞，没有真实披露日期支撑（`tests/test_analyzer_news_prompt.py` 可证）。

诉求是把「财报日历」（预约披露日期 / 下一次财报日期）作为基本面 `earnings` 块的一个子数据，让分析报告和问股回答能引用**带来源、带计划/预估语义**的披露日期。

### 1.2 现有链路（复用锚点）

| 能力 | 现有锚点 | 复用点 |
| --- | --- | --- |
| A股基本面聚合 | `data_provider/fundamental_adapter.py` `AkshareFundamentalAdapter.get_fundamental_bundle()`（约 784） | 直接新增 calendar 取数，复用 `_call_df_candidates`（多候选 + fallback + 整表缓存 + 限时，约 721） |
| A股整表缓存 | `_DAILY_CACHEABLE_AKSHARE_INTERFACES`（约 132） | 新接口加入后按交易日缓存整表 |
| 美股/港股基本面 | `data_provider/yfinance_fundamental_adapter.py` `YfinanceFundamentalAdapter.get_fundamental_bundle()`（约 158） | 加 `ticker.calendar` / `ticker.earnings_dates`，无密钥 |
| 基本面聚合 | `data_provider/base.py` `get_fundamental_context()`、`_build_fundamental_block()`（约 3697）、`_earnings_block_has_values()`（约 3779） | calendar 作为 earnings 子块被自动包装进 `earnings.data` |
| 报告基本面渲染 | `src/analyzer.py` `_build_fundamental_rows()`（约 1422） | 业绩预告/快报已在此渲染，新增「下次财报披露」行 |
| 问股工具 | `src/agent/tools/data_tools.py` `_compact_fundamental_context()`（约 150）、`get_stock_info`（约 542） | `earnings.data` 已被完整透传，**无需新增工具** |
| 报告语言 | `src/report_language.py` `earnings_outlook_label` 等 | 新增 label（zh/en/ko） |

### 1.3 需要解决的问题

1. 分析 / 问股拿不到「下一次财报披露日期」，事件驱动维度缺失。
2. A股与美股财报日历数据源、字段各异，需统一标准化，下游不得各自解析。
3. 披露日期是**计划/预约**，会变更（延期），必须标注「计划/预估」语义，不能当确定事实。
4. 单一数据源失败、接口不稳定、字段变更不得阻塞分析主链路（fail-open）。

---

## 2. 目标与非目标

### 2.1 目标

- G1：A股经 akshare（巨潮预约披露 + 东财个股预约披露）取「下一次财报预约披露日期」；美股经 yfinance `calendar` / `earnings_dates` 取「下一次财报日期」。
- G2：标准化为 `earnings.calendar` 子块，进入 `fundamental_context`，普通分析与问股 `get_stock_info` 自动复用。
- G3：报告基本面业绩区块渲染「下次财报披露日期（计划）」，带 `days_to_next` 与来源语义；问股回答可引用。
- G4：任何数据源失败 / 未取到 / 不支持（如港股）都标 `not_supported` 或静默降级，不阻断主链路；默认开启但 fail-open。
- G5：披露日期始终带「计划/预估」语义，不当作确定性买卖信号强度。

### 2.2 非目标

- 不做港股财报日历（港股财报日期数据源弱，首期标 `not_supported`，留 P2）。
- 不把披露日期换算成 `decision_score` / `strategy_synthesis` 数值权重或买卖强度。
- 不新增独立前端页面 / 导航入口（首版只做报告与问股内嵌展示）。
- 不新增数据库表、不落库财报日历（随基本面上下文一次性取、一次性用，与现有 forecast/quick_report 一致）。
- 不接 Finnhub `/calendar/earnings` 到主分析链路（已有 `FinnhubEarningsAdapter` 仅供 signal_scanner，且限 8 标的、需 Key；留作美股降级候选）。
- 不改模型名、provider、`base URL`、默认模型策略、回退策略或运行时配置迁移语义。

---

## 3. 数据源与字段契约

### 3.1 数据源（P1 固定范围）

| 市场 | 数据源 | 接口 | 覆盖 | 密钥 |
| --- | --- | --- | --- | --- |
| A股 | akshare | `stock_report_disclosure`（巨潮资讯-预约披露，按 market+period 整表） | 全市场预约披露日期（含已披露） | 无 |
| A股 | akshare | `stock_yysj_em`（东方财富-个股预约披露时间，按 symbol+date） | 个股预约披露日期 | 无 |
| 美股 | yfinance | `ticker.calendar`（`Earnings Date` 字段） / `ticker.earnings_dates`（未来日期表） | 下一次财报日期 | 无 |

- 首期只走无密钥源；A股候选顺序 `stock_report_disclosure` → `stock_yysj_em`，用 `_call_df_candidates` 复用现有 fallback 机制。
- `stock_report_disclosure` 为整表拉取，需加入 `_DAILY_CACHEABLE_AKSHARE_INTERFACES` 按交易日缓存，避免每只股票重复拉全市场。
- 港股不在 P1 范围：`get_fundamental_context` 的 HK 路径沿用 yfinance，若 `ticker.calendar` 有值则顺带可用，否则标 `not_supported`。

### 3.2 标准化字段（下游唯一消费口径）

在 `earnings` 数据块内新增 `calendar` 子块（即 `_build_fundamental_block` 的 `data` payload 内）：

```jsonc
"earnings": {
  "financial_report": { ... },       // 已有
  "forecast_summary": "...",         // 已有
  "quick_report_summary": "...",     // 已有
  "dividend": { ... },               // 已有
  "calendar": {                      // 新增
    "events": [
      {
        "report_period": "2025-12-31",            // 报告期截止日（ISO 日期）
        "expected_disclosure_date": "2026-03-28", // 预约/计划披露日期（核心字段）
        "actual_disclosure_date": null,           // 实际披露日期（已披露才有）
        "disclosure_status": "scheduled"          // scheduled | disclosed
      }
    ],
    "next_earnings_date": "2026-03-28", // 最近一次未来披露日（派生，无未来事件则为 null）
    "days_to_next": 126,                 // 距今天数（派生，非负整数；无未来事件为 null）
    "coverage": "cn_reservation_disclosure", // akshare / yfinance_calendar
    "as_of": "2026-11-22"                // 取数日期
  }
}
```

- `events` 只保留「下一个未来披露日 + 最近 1 个已披露日」（最多 2 条），避免整表塞进上下文。
- `expected_disclosure_date` 只接受可解析 ISO 日期；解析失败丢弃该事件，不伪造。
- `days_to_next` 由实现基于 `as_of`（本地服务器日期）计算，不依赖上游时区；跨日边界不反向影响 `next_earnings_date` 本身。
- 披露日期是**计划值**：报告渲染与问股措辞必须带「预计/预约」限定，不得写成已确定事实。

---

## 4. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `data_provider/fundamental_adapter.py` | `get_fundamental_bundle()` 内新增 A股 `calendar` 取数：`_call_df_candidates([("stock_report_disclosure", {...}), ("stock_yysj_em", {...})])` → `_build_earnings_calendar()` 标准化为 §3.2；把 `calendar` 并入 `result["earnings"]`；把两个接口加入 `_DAILY_CACHEABLE_AKSHARE_INTERFACES`。新增解析函数 `_build_earnings_calendar(df, stock_code, now)`，用 `_pick_by_keywords` 探测字段（证券代码/证券简称/报告期/预约日期/实际披露日期等列名以实测为准） |
| 2 | `data_provider/yfinance_fundamental_adapter.py` | `get_fundamental_bundle()` 内新增美股 `calendar`：读 `ticker.calendar` 的 `Earnings Date`（优先）与 `ticker.earnings_dates`（补充未来日期），标准化为 §3.2；失败静默追加 `errors`，不影响其它块 |
| 3 | `data_provider/base.py` | `_earnings_block_has_values()`（约 3779）把 `calendar` 子块纳入「有值」判断，避免 calendar-only 的 earnings 块被判 shell 丢弃；确认 `_infer_block_status` 对 calendar 非空时判 `ok/partial` |
| 4 | `src/analyzer.py` | `_build_fundamental_rows()`（约 1422）业绩区块新增「下次财报披露日期」行（label 走 `report_language`），值形如 `2026-03-28（预计，距今 126 天）`；无 `next_earnings_date` 时**不渲染**该行 |
| 5 | `src/report_language.py` | 新增 `next_earnings_date_label` / `days_until_earnings_label` 等 label，补齐 `zh` / `en` / `ko` 三语 |
| 6 | `src/agent/tools/data_tools.py` | `get_stock_info` 的 description 与（如存在）`src/agent/executor.py` 工具说明里点明「财报日历在下一次财报披露日期」位于 `get_stock_info` 返回的 `earnings.calendar`，提示模型可引用；`_compact_fundamental_context()` 已透传 `earnings.data`，无需改动结构 |
| 7 | 配置 | 不新增配置键（复用 fundamental 取数、无密钥、fail-open，遵循「不配置也可运行」）；如需紧急关闭，走代码回滚。**若评审认为需要开关**，则新增 `EARNINGS_CALENDAR_ENABLED=true` 并同步 `.env.example` + 配置注册表三处声明（`docs/config/config-single-source.md`） |
| 8 | `docs/ai/DATA-FLOW.md` | 记录 earnings 新增 `calendar` 子块来源与字段；`docs/CHANGELOG.md` `[Unreleased]` 加一行 `- [新功能] 分析/问股接入财报日历（下一次财报披露日期）` |
| 9 | `tests/` | 见 §8 验证矩阵 |

---

## 5. 关键实现约束

- **fail-open**：calendar 取数失败只追加 `errors`，`earnings` 块整体仍可 `ok/partial`，不把基本面整块降级为 `failed`。
- **字段探测**：akshare 列名以实测为准，用 `_pick_by_keywords` 做别名匹配，禁止硬编码单列名（沿用 `_DIVIDEND_KEYWORD_MAP` 先例）。
- **整表缓存**：`stock_report_disclosure` 加入 `_DAILY_CACHEABLE_AKSHARE_INTERFACES`，`_call_df_candidates` 会自动按 A股交易日缓存整表并 `_filter_rows_by_code` 过滤单只股票，避免每只股票重复拉全市场。
- **计划/预估语义**：`expected_disclosure_date` 在报告/问股措辞里必须带「预计/预约」，不得写成确定日期。
- **不扩配置面**：首版不加开关，除非评审要求；加开关时按三处声明同步（见 §4 #7）。
- **分析师大文件红线**：`src/analyzer.py` 是「不要大改的上游文件」，本次只在 `_build_fundamental_rows` 追加一行渲染，最小可回滚补丁。

---

## 6. 验收用例

1. A股：分析 `600519`，报告基本面区块出现「下次财报披露日期（预计）」；数据源可用时日期与 akshare 预约披露一致，带 `预计` 措辞。
2. 美股：分析 `AAPL`，报告出现「下次财报日期（预计）」（yfinance `calendar` 有值时）；yfinance 无值时静默不渲染，不报错。
3. 港股 / ETF：`earnings.calendar` 标 `not_supported` 或为空，报告不出现财报日历行，分析链路正常。
4. 问股：`get_stock_info` 返回的 `earnings.calendar` 含 §3.2 字段，模型可据此回答「下次财报什么时候披露」。
5. fail-open：mock akshare/yfinance 抛异常，分析主链路不受影响，`earnings` 其它子块（financial_report/dividend）仍正常。
6. 计划语义：所有用户可见披露日期均带「预计/预约」限定，无把预约日期当事实的措辞。
7. 三语文案：`zh/en/ko` 的 label 正确，空数据不出现空占位。
8. `pytest -m "not network"` 全绿；新增/更新契约测试（见 §8）。

---

## 7. 兼容性与回滚

- 全部为**追加字段**：`earnings.calendar` 是新增子块，不修改既有 `financial_report` / `dividend` / `forecast_summary` / `quick_report_summary` 结构。
- 不新增数据库表、不迁移、不落库；`fundamental_context` 形状仅在 `earnings.data` 内多一个可选子块，旧记录 / 缺字段时下游正常。
- `src/analyzer.py` 只在基本面业绩区块追加一行渲染，遵循「最小、可解释、可回滚」补丁原则。
- 回滚：`revert` 本 PR 即可；`earnings.calendar` 消失后报告/问股退回现状，无残留状态。

---

## 8. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| Python 数据/聚合 | `python -m pytest tests/test_fundamental_adapter_contracts.py tests/test_analyzer_news_prompt.py -m "not network"`、`python -m py_compile` 改动文件 |
| 契约测试（新增） | A股 `_build_earnings_calendar` 的标准化 / 字段探测 / 未来事件选取 / 计划语义；`_earnings_block_has_values` 对 calendar 的判定 |
| 美股契约（新增/更新） | `yfinance` `calendar`/`earnings_dates` mock 返回 → §3.2 标准化；缺失/异常 fail-open |
| 报告渲染 | 有 `next_earnings_date` 渲染 + 空数据不渲染 + 三语文案 + 「预计」措辞 |
| 问股工具 | `get_stock_info` 返回含 `earnings.calendar`，`_compact_fundamental_context` 透传不丢 |
| 联网 smoke（观测，可选） | `pytest -m network`：akshare `stock_report_disclosure`/`stock_yysj_em`、yfinance `calendar` 实际字段契约核验 |
| 文档 | 核对接口名、字段名、文件名、`CHANGELOG` 条目与仓库一致 |

---

## 9. 分期

- **P1（本任务书范围）**：A股（akshare 预约披露）+ 美股（yfinance calendar）财报日历；`earnings.calendar` 标准化；报告与问股内嵌展示；fail-open、计划语义、三语文案、测试与文档。港股标 `not_supported`。
- **P2（后续单独评审）**：港股财报日期（futu OpenD 或降级为公告检索）；美股 Finnhub `/calendar/earnings` 降级链（需评估 Key 与 8 标的限制如何放宽）；披露日期纳入决策信号 / 技能观点的「事件驱动」维度（需先建立「不把日期当强度权重」的反例契约）。

---

## 10. P1 落地说明（与任务书的差异）

- **未新增 `report_language` label**：财报日历首期只进入分析 Prompt 的「业绩与筹码事件」小表，该表在 `src/analyzer.py::_build_earnings_and_chip_event_rows` 内自带 en/zh 标签（与「业绩预告 / 业绩快报」行同款），不经过通知 / 历史渲染，因此不需要 `report_language.py` 的 label 字典。若后续要进通知或历史报告，再补 label。
- **A股主备顺序调整为 `stock_report_disclosure` → `stock_yysj_em`**：巨潮接口单次 POST（pagesize=10000）取整表，东财接口是分页 GET（每期约 10 次请求）。优先巨潮、失败再降级东财，显著降低单次分析的请求量；两者都已加入 `_DAILY_CACHEABLE_AKSHARE_INTERFACES`，跨股票复用整表缓存。
- **不新增配置开关**（按任务书 §4 #7 的默认选择）：无密钥、fail-open、复用现有基本面取数，关闭方式为代码回滚。
- **聚合层补充**：`data_provider/base.py::_earnings_block_has_values` 将 `calendar.next_earnings_date` / 非空 `events` 计入「有值」，并在 Futu × yfinance 合并路径补 `calendar` 透传，避免港股有 Futu 数据时把 yfinance 的日历静默丢掉。
- **港股**：不走专门取数；未配置 Futu 时经 yfinance 顺带可用，配置 Futu 的合并路径会保留 yfinance 日历，但不对港股单独保证，仍以 `not_supported` / 缺失为主。
- **测试**：新增 `tests/test_earnings_calendar.py`（17 例）覆盖标准化、未来/已披露事件选取、最新变更优先、接口取数与 fail-open、聚合层判定、yfinance 解析、分析器渲染；`tests/test_fundamental_adapter_contracts.py::test_installed_akshare_receives_valid_parameters_at_http_boundary` 同步更新为包含财报日历的边界请求数（12 次）与参数断言。

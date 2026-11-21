# DATA-FLOW · 数据从抓取到页面

> 手写维护。新增数据源、字段或提示词输入时同步更新本文件；字段级契约以 `docs/` 专题文档与代码为准。

## 1. 总览

```
外部数据源 → Fetcher 标准化 → 落库(daily_bars / 新闻 / 情报)
          → 分析上下文组装 → Prompt → LLM → AnalysisResult
          → 报告(Markdown/图片) → 通知 / 历史记录 → Web API → 前端渲染
```

## 2. 抓取与标准化

| 环节 | 代码锚点 | 关键约束 |
| --- | --- | --- |
| 数据源选择与 fallback | `data_provider/base.py`（`DataFetcherManager`、`normalize_stock_code`） | 单源失败降级到下一源；`priority` 顺序决定首选 |
| A 股 / ETF | `data_provider/akshare_fetcher.py`、`tushare_fetcher.py`、`efinance_fetcher.py` 等 | 成交量统一到「手 / 股」口径，港股与美股分别归一 |
| 港美股 | 同上（`_is_hk_code`、`_is_us_code`） | 代码后缀规则见 `docs/market-support.md` |
| 复权与拆股 | 抓取层 + `data10/splits.csv`（回测专用） | 回测按拆股复权；实时分析使用数据源口径 |

**字段约定**：抓取层返回统一列名（`date/open/high/low/close/volume/amount`），下游不再判断数据源。

## 3. 落库

| 数据 | 存储 | 说明 |
| --- | --- | --- |
| 日线 | `daily_bars`（`src/storage.py:3656 save_daily_data()`） | 盘中未收盘的当日行不入库；回写只保留到最近已收盘交易日 |
| 分析历史 | `analysis_history`（`src/storage.py:3156 save_analysis_history()`） | 报告详情、历史页、追问上下文都读这条记录 |
| 新闻 / 情报 | `news_intel` 等（`src/storage.py`） | 带来源、时间、情感字段 |
| 会话 | Agent 会话表（`src/services/agent_chat_session_service.py`） | 消息、技能选择、标的上下文 |

数据库访问优先走 `src/repositories/`（`stock_repo`、`analysis_repo`、`portfolio_repo` 等），新增查询不要直接在端点里写 SQL。

## 4. 分析上下文与 Prompt

| 环节 | 代码锚点 | 说明 |
| --- | --- | --- |
| 上下文组装 | `src/analyzer.py:4196 analyze()` | 行情、历史 K 线、技术指标、新闻、基本面、持仓 |
| 上下文包 | `docs/analysis-context-pack.md`（契约文档） | Web 追问复用同一份上下文包 |
| 提示词 | `src/analyzer.py` 内各 prompt 构造函数 | 数据块降级会写明「落后 N 个交易日」并扣减数据质量分 |
| 报告语言 | `src/report_language.py` | `REPORT_LANGUAGE` 控制输出语言与固定文案 |
| 问股 Agent | `src/agent/orchestrator.py` + `src/agent/tools/` | 工具调用结果以 `tool_done` 事件回传，不写进系统提示词以外的全局状态 |

**判断标准**：某个字段有没有进提示词，看 `src/analyzer.py` 里对应 prompt 构造是否引用了它；只入库但未引用即为「已采集、未使用」，可在 `STATUS.md` 记录。

## 5. 输出与消费

| 消费方 | 路径 | 说明 |
| --- | --- | --- |
| 通知 | `src/notification.py` → 各渠道 sender | 内容按渠道能力裁剪；微信走仪表盘 / 简报格式 |
| 图片海报 | `src/share_image.py` | Markdown 转图片，失败不影响文本推送 |
| 历史 API | `api/v1/endpoints/history.py` → `api/v1/report_builder.build_analysis_report()` | 三处报告对象由同一构造函数生成 |
| 分析 API | `api/v1/endpoints/analysis.py`（同步分析 / 任务状态） | 任务状态回退同样走 `build_analysis_report()` |
| Web 前端 | `apps/dsa-web/src/pages/`、`src/components/` | 首页、历史、持仓、筛选、问股 |

## 6. 时间字段约定

数据库里有两套 naive 时间，**不要混用**：

| 约定 | 默认值 | 使用表 | 序列化 |
| --- | --- | --- | --- |
| 服务器本地时间 | `datetime.now` | 行情、分析历史、持仓、告警、会话、用量等老表 | `HistoryService._serialize_created_at` → `value.astimezone().isoformat()` |
| UTC 时间 | `utc_naive_now` | `screening_runs`、决策信号、技能观点、`login_audit_log` | `storage.to_utc_isoformat()`（假定 naive=UTC） |

新增 `DateTime` 字段时必须选择其一，并在 `tests/test_time_basis_contract.py` 的 `LOCAL_TIME_TABLES` / `UTC_TIME_TABLES` 登记；跨表比较前先确认两边基准一致（例如决策信号会把历史时间显式转成 UTC-naive）。

## 7. 缓存与降级

- 历史 K 线缓存：`data_source='cache'` 在进度事件里显式标注，前端显示「历史缓存」。
- 实时行情失败：降级到最近收盘数据，并在报告里标注数据时间。
- 通知渠道失败：逐渠道记录诊断（`record_notification_run`），主流程继续。
- LLM 失败：按渠道配置重试/降级，最终失败时报告写明失败原因，不伪造分析结论。

## 需求与解决方案：Tushare `trade_cal` 限频替代

### 一、背景与问题界定

当前 Tushare 账号调用 `trade_cal` 时返回“频率超限（1 次/小时）”。根因是该接口在 Tushare 的积分体系下设有权限门槛，免费或低积分账号不具备稳定调用资格。`trade_cal` 的访问通常需要至少 2000 积分，而新注册账号默认仅约 100 积分，因此在权限不足或积分档位较低时，调用会被严格限频，甚至直接拒绝。社区反馈中，“每小时最多访问该接口 1 次”是低积分账号的常见表现。

核心需求是：**在不依赖 Tushare 积分体系、不产生不可控远程调用频率的前提下，获得一套准确、可维护的 A 股交易日历，用于回测、数据对齐和调度逻辑。**


### 二、需求细化

| 需求维度 | 具体要求 |
|---|---|
| **数据准确性** | 必须覆盖 A 股实际交易日，正确剔除周末、法定节假日、调休及临时休市，不能简单用“工作日”替代 |
| **频率可控** | 调用不触发类似“1 次/小时”的硬限频；最好能做到“一次获取，长期使用” |
| **覆盖范围** | 历史回测通常需要 1990 年代至今的全量序列；部分场景需要展望至未来 1–2 年 |
| **返回格式** | 优先直接返回 `trade_date` 列表，便于在 Pandas 中做日期筛选、区间截取和成员判断 |
| **工程可行性** | 方案应可集成进现有 Python 数据管线，不引入过重的运维负担 |


### 三、候选方案逐一分析

#### 方案 A：AkShare `tool_trade_date_hist_sina`

AkShare 提供的新浪财经交易日历接口，直接返回一列 `trade_date`，用法与 `trade_cal` 最为接近：

```python
import akshare as ak

trade_dates = ak.tool_trade_date_hist_sina()
```

**优势**：单次返回全量序列，无“每小时 1 次”级别的限频；结果列名就是 `trade_date`，下游代码改动量最小。

**需要特别核实的边界**：AkShare 官方文档当前标注的覆盖上限为 **2024-12-31**，示例数据实际展示到 2025 年末。部分社区整理显示数据可延伸至 2026 年末，但这取决于上游新浪源的实际更新状态，不能默认全量覆盖未来日期。因此该方案适合**历史全量获取**，如果涉及 2025 年之后的日期，需要配合其他来源或本地缓存更新机制。

**限频**：不存在 Tushare 式的积分限频，但作为网页接口抓取，仍可能因上游站点策略出现偶发限流，建议获取后立即持久化，避免重复调用。


#### 方案 B：Baostock `query_trade_dates`

Baostock 是完全免费、无需注册的 A 股数据源，交易日查询接口返回 `calendar_date` 和 `is_trading_day` 两列，结构化程度更高：

```python
import baostock as bs
import pandas as pd

lg = bs.login()
rs = bs.query_trade_dates(start_date="2024-01-01", end_date="2026-12-31")

data = []
while (rs.error_code == "0") & rs.next():
    data.append(rs.get_row_data())

df = pd.DataFrame(data, columns=rs.fields)
# columns: calendar_date, is_trading_day (0/1)
bs.logout()
```

**优势**：免费免注册，不存在积分门槛和“1 次/小时”的硬限频；返回结果直接包含 `is_trading_day`，无需额外判断。Baostock 官方说明提供上交所 1990 年至“今年”的数据，适合历史回测与当期覆盖。

**局限**：Baostock 的覆盖边界以服务端可提供的范围为限，对**未来年份**的展望能力有限，不宜将其作为未来日期的可靠来源。


#### 方案 C：pandas_market_calendars / exchange_calendars（本地规则计算）

这类库基于交易所规则和已公布的节假日安排在**本地计算**交易日历，不发起远程请求，因此从机制上不存在“频率超限”问题：

```python
import pandas_market_calendars as mcal

sse = mcal.get_calendar("SSE")  # 或 XSHG
schedule = sse.schedule(start_date="2020-01-01", end_date="2026-12-31")
trading_days = schedule.index
```

**关键边界必须正视**：pandas_market_calendars 对中国 A 股（XSHG/XSHE）的**可靠上限当前为 2026-12-31**。中国调休和长假安排由官方按年度发布，在交易所发布下一年度休市安排之前，PMC 无法可靠推导未来日期的交易日，**不能把普通工作日外推当作未来交易日使用**。这意味着该方案对**历史年份和已被官方安排覆盖的年份**非常可靠，但对 2027 年及之后的日期，需要等待库更新或人工补充官方安排。


#### 方案 D：Qlib 离线日历文件（一次性下载，零接口依赖）

Qlib 的数据包中包含 `calendars/day.txt`，即为 A 股交易日历文件。可从 `chenditc/investment_data` 的 Releases 页面下载完整数据包并提取：

```bash
wget https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=2
# 交易日历位于 calendars/day.txt
```

**优势**：彻底摆脱接口调用——下载一次后，所有回测和管线直接读本地文件，调用次数为 0，不存在任何限频问题。

**局限**：`day.txt` 是静态快照，更新需要重新下载或自行增量维护；作为“日期骨架”使用完全够用，但不应期望它自动覆盖下载时间点之后的未来日期。


### 四、方案对比与选型建议

| 方案 | 限频风险 | 历史覆盖 | 未来日期能力 | 返回格式友好度 | 适用定位 |
|---|---|---|---|---|---|
| **Baostock** | 极低 | 1990 年至今 | 有限 | 高（日期 + 是否交易日） | **首选：全量历史获取与落盘** |
| **AkShare** | 低（网页抓取偶发） | 官方标注至 2024 年末，实际视上游 | 有限 | 最高（直接 `trade_date` 列） | 快速替换、小体量场景 |
| **pandas_market_calendars** | 无（本地计算） | 历史年份可靠 | **可靠至 2026-12-31** | 中（返回 DatetimeIndex） | 历史回测的规则校验与补充 |
| **Qlib `day.txt`** | 无 | 视数据包版本 | 无（静态快照） | 中（文本文件需解析） | **生产环境的零接口骨架** |

**推荐组合策略**：

1. **以 Baostock 或 AkShare 做一次全量拉取，保存为本地 Parquet/CSV**。这是绕开 Tushare 限频最直接、改动最小的路径；后续回测直接读本地文件，不再触发任何交易日历接口调用。
2. **若需要在历史年份上做交叉校验**，可用 pandas_market_calendars 对本地日历进行规则比对，确认节假日剔除一致。
3. **对于 2026-12-31 之后的未来日期**，不要依赖 PMC 自动推导。应在每年交易所发布下一年度休市安排后，由本地缓存做一次增量追加，或等待 pandas_market_calendars / tradecal 等库发布对应的年度更新。

这一组合的实质是：**用“一次性获取 + 本地持久化”把接口调用从日常管线中彻底移除**，未来日期则回到“官方安排驱动”的维护节奏，而非试图让任何单一库无限外推。


### 五、实施步骤（以 Baostock + 本地缓存为例）

**步骤 1：拉取全量历史交易日**

```python
import baostock as bs
import pandas as pd

lg = bs.login()
rs = bs.query_trade_dates(start_date="1990-01-01", end_date="2026-12-31")
data = []
while (rs.error_code == "0") & rs.next():
    data.append(rs.get_row_data())
df = pd.DataFrame(data, columns=rs.fields)
bs.logout()

trading_days = df.loc[df["is_trading_day"] == "1", "calendar_date"]
trading_days = pd.to_datetime(trading_days)
trading_days.to_series().to_frame("trade_date").to_parquet("trade_cal.parquet")
```

**步骤 2：下游统一读本地文件**

将原有 `pro.trade_cal(...)` 的调用位置替换为：

```python
trade_cal = pd.read_parquet("trade_cal.parquet")["trade_date"]
```

后续所有日期筛选、区间截取、`isin` 判断均基于该本地 Series 完成。

**步骤 3：年度增量维护**

每年交易所公布下一年度休市安排后，用 Baostock 的 `query_trade_dates` 对新增年份拉取一次，追加到 Parquet 文件；若 Baostock 当期未覆盖，则以官方公告手工补充，确保未来日期不出现空洞。


### 六、风险与注意事项

- **AkShare 的覆盖标注存在版本差异**：官方文档显示到 2024-12-31，示例数据展示到 2025 年末，部分社区版本声称到 2026 年末。使用前应实际调用并检查 `max(trade_date)`，不要仅依据文档承诺。
- **pandas_market_calendars 不能“猜测”未来**：2026-12-31 之后的日期在官方安排发布前不可靠，任何试图用工作日规则外推的做法在长假和调休场景下会产生错误。
- **Baostock 的未来展望能力有限**：它适合“拉到今天/今年”，不应被当作未来日期的权威来源。
- **本地缓存是降频的根本手段**：即使选用 AkShare 或 Baostock，也应在首次成功后立即持久化；交易日历是低频变化数据，重复远程调用没有工程上的必要性。
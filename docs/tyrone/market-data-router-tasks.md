# 行情数据源多源降级 - 开发任务分解

**需求文档**：`docs/tyrone/hangqingyuan.md`  
**目标**：实现 `MarketDataRouter` 统一路由层，支持多源自动降级、频率控制、北交所边界处理

---

## 任务概览

| 阶段 | 任务数 | 预估工时 | 依赖关系 |
|------|--------|----------|----------|
| T1: 基础设施 | 3 | 2-3h | 无 |
| T2: 数据源适配器 | 6 | 8-10h | T1 |
| T3: 路由核心 | 4 | 6-8h | T2 |
| T4: 降级链实现 | 3 | 4-5h | T3 |
| T5: 可观测性 | 2 | 2-3h | T3 |
| T6: 测试验证 | 8 | 4-5h | T4, T5 |
| **总计** | **26** | **26-34h** | |

---

## T1: 基础设施层

### T1.1 安装新依赖

**目标**：安装需求文档指定的依赖包

**操作**：
```bash
# 必须安装
pip install tj-calendar
pip install "ashareapi[pandas]"
pip install zzshare
pip install baostock  # 如未安装

# 可选（注册后安装）
# pip install tushare  # 如已有 Token
```

**验证**：
- [ ] `python -c "from tj_calendar import trade_days_between; print(trade_days_between('2026-11-01', '2026-11-30'))"`
- [ ] `python -c "import ashareapi; print(ashareapi.__version__)"`
- [ ] `python -c "import zzshare; print('ok')"`

**文件**：
- `requirements.txt` - 添加新依赖

---

### T1.2 创建模块目录结构

**目标**：建立 `data_provider/market_router/` 模块骨架

**目录结构**：
```
data_provider/
├── market_router/
│   ├── __init__.py
│   ├── router.py              # MarketDataRouter 主类
│   ├── calendar.py            # 交易日历（离线）
│   ├── rate_limiter.py        # 频率控制器
│   ├── observability.py       # 日志与指标
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py            # Provider 基类
│   │   ├── tencent_realtime.py    # S1
│   │   ├── tencent_kline.py       # S2
│   │   ├── sina_realtime.py       # S3
│   │   ├── ashareapi_provider.py  # S4
│   │   ├── zzshare_provider.py    # S5
│   │   ├── apocdata_provider.py   # S6
│   │   ├── tushare_provider.py    # S7 (可选)
│   │   ├── itick_provider.py      # S8 (可选)
│   │   └── baostock_provider.py   # 历史数据兜底
│   └── chains/
│       ├── __init__.py
│       ├── realtime_chain.py      # 实时快照降级链
│       ├── daily_chain.py         # 日K线降级链
│       └── minute_chain.py        # 分钟K线降级链
└── ... (现有文件保持不变)
```

**验证**：
- [ ] 目录结构创建完成
- [ ] 所有 `__init__.py` 文件存在

---

### T1.3 实现交易日历（离线）

**目标**：使用 `tj-calendar` 实现零网络依赖的交易日历

**文件**：`data_provider/market_router/calendar.py`

**接口**：
```python
def get_calendar(start: str, end: str) -> list[str]:
    """返回区间内交易日列表。离线实现，零网络。"""
    
def is_trade_day(date: str) -> bool:
    """判断是否为交易日"""
    
def next_trade_day(date: str) -> str:
    """返回下一个交易日"""
```

**约束**：
- 覆盖范围：1990-12-19 至 2035-12-31
- 支持市场：SSE/SZSE（沪深）、BSE（北交所，2021-11-15 起）
- 超出范围时抛出 `CalendarRangeError`，不得静默返回

**验证**：
- [ ] 断网环境下 `get_calendar("2026-11-01", "2026-11-30")` 正常返回
- [ ] `is_trade_day("2025-10-01")` 返回 `False`（国庆节）
- [ ] 北交所 2021-11-15 之前的日期抛出 `CalendarRangeError`

---

## T2: 数据源适配器层

### T2.1 Provider 基类与通用工具

**目标**：定义 Provider 统一接口和工具函数

**文件**：`data_provider/market_router/providers/base.py`

**接口**：
```python
class BaseProvider(ABC):
    """数据源提供者基类"""
    
    provider_id: str  # 如 "S1_tencent", "S2_tencent_kline"
    supports_bse: bool = False  # 是否支持北交所
    
    @abstractmethod
    def get_realtime(self, codes: list[str]) -> dict[str, dict | None]:
        """批量实时快照"""
        pass
    
    @abstractmethod
    def get_daily(self, code: str, start: str | None, end: str | None, count: int | None) -> pd.DataFrame | None:
        """日K线"""
        pass
    
    @abstractmethod
    def get_minute(self, code: str, freq: str, count: int) -> pd.DataFrame | None:
        """分钟K线。freq ∈ {'1min','5min','15min','30min','60min'}"""
        pass
```

**工具函数**：
```python
def is_bse_code(code: str) -> bool:
    """识别北交所代码：8xxxxx, 4xxxxx, 920xxx"""
    
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一返回列：date, open, high, low, close, volume, amount"""
```

**验证**：
- [ ] 基类定义完整
- [ ] `is_bse_code("830799")` 返回 `True`
- [ ] `is_bse_code("600519")` 返回 `False`

---

### T2.2 S1: 腾讯实时快照适配器

**目标**：实现腾讯实时快照接口

**文件**：`data_provider/market_router/providers/tencent_realtime.py`

**接口**：`https://qt.gtimg.cn/q={code}`

**频率硬限制**：
- 请求间隔 ≥ 100ms
- 批量查询用逗号拼接，减少请求数

**北交所**：支持（老代码数据较全）

**返回字段映射**：
```python
{
    "open": float,
    "high": float,
    "low": float,
    "close": float,  # 最新价
    "volume": int,   # 成交量（手）
    "amount": float, # 成交额（元）
    "ts": str        # 时间戳
}
```

**验证**：
- [ ] 单只沪深标的返回有效数据
- [ ] 批量查询（10只）正常返回
- [ ] 北交所标的返回有效数据

---

### T2.3 S2: 腾讯K线适配器

**目标**：实现腾讯日K线和分钟K线接口

**文件**：`data_provider/market_router/providers/tencent_kline.py`

**接口**：
- 日线：`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`
- 分钟线：`https://web.ifzq.gtimg.cn/appstock/app/kline/mkline`

**频率硬限制**：
- 与 S1 共享腾讯域，建议 ≥ 200ms
- 单次返回上限 500 根
- 连续 5000+ 次后返回空，降速可恢复

**北交所**：**不支持**，遇 `bj`/`920` 开头代码会报错

**数据口径**：
- 日线及以上：前复权
- 分钟线：不复权

**验证**：
- [ ] 沪深标的日线返回有效数据（前复权）
- [ ] 沪深标的分钟线返回有效数据（不复权）
- [ ] 北交所标的触发时抛出明确异常或返回 `None`

---

### T2.4 S3: 新浪实时快照适配器

**目标**：实现新浪实时快照接口

**文件**：`data_provider/market_router/providers/sina_realtime.py`

**接口**：`https://hq.sinajs.cn/list={code}`

**频率硬限制**：
- 单 IP 日请求 ≤ 1050 次（硬编码，预留 30% 余量）
- 并发 ≤ 5 QPS
- 必须带 `Referer` 和 `User-Agent`

**北交所**：需实测确认

**验证**：
- [ ] 单只沪深标的返回有效数据
- [ ] 日请求计数器正常工作
- [ ] 超过 1050 次/日时主动拒绝请求

---

### T2.5 S4: ashareapi 适配器

**目标**：实现 ashareapi 实时快照和K线接口

**文件**：`data_provider/market_router/providers/ashareapi_provider.py`

**接口**：
```python
from ashareapi import AShareAPI
api = AShareAPI()
api.quote(code)  # 实时快照
api.kline(code, period, count)  # K线
```

**频率硬限制**：
- 遇 429 退避 ≥ 2s
- 连续 3 次 429 则本批次跳过该源

**北交所**：需实测

**数据口径**：
- 日线及以上：前复权（服务端固定）
- 分钟线：不复权（付费端点）

**验证**：
- [ ] 实时快照返回有效数据
- [ ] 日K线返回有效数据（前复权）
- [ ] 429 退避逻辑正常工作

---

### T2.6 S5: zzshare 适配器

**目标**：实现 zzshare 日K线、分钟K线、实时快照接口

**文件**：`data_provider/market_router/providers/zzshare_provider.py`

**接口**：
```python
from zzshare import DataApi
api = DataApi(token=optional)
api.daily()  # 日线
api.stk_mins()  # 分钟线
api.rt_k()  # 实时快照
```

**频率硬限制**：
- 匿名 ≥ 1s
- 配置 Token 后可适当降低间隔
- 单只单次最多 1000 条，需用 `offset` 分页

**北交所**：`stock_basic` 含 `BJ` 交易所；K线接口需实测

**数据口径**：日线、分钟线均支持

**验证**：
- [ ] 日线返回有效数据
- [ ] 分钟线返回有效数据
- [ ] 分页逻辑正常工作
- [ ] 北交所标的返回有效数据（或明确不支持）

---

### T2.7 S6: ApocData 适配器（可选）

**目标**：实现 ApocData 实时快照接口

**文件**：`data_provider/market_router/providers/apocdata_provider.py`

**接口**：
```python
BASE = "https://www.apocdata.com/api/blade-dataplatform/open/data"
GET /quote?symbol=X
```

**频率硬限制**：
- 免费体验额度匿名可用
- 注册提高额度

**北交所**：需实测

**验证**：
- [ ] 实时快照返回有效数据
- [ ] 匿名额度耗尽时优雅降级

---

### T2.8 S7: Tushare 适配器（可选，仅注册用户）

**目标**：实现 Tushare 日K线备份接口

**文件**：`data_provider/market_router/providers/tushare_provider.py`

**前置条件**：
- 用户已注册 Tushare 并获取 120 积分
- 环境变量 `TUSHARE_TOKEN` 已配置

**频率硬限制**：
- ≤ 50 次/分钟（即 ≥ 1200ms/次）
- ≤ 8000 次/日

**数据口径**：**仅不复权日线**

**约束**：
- 如果策略依赖前复权日线，不得将 S7 作为主日线源
- 仅可在不复权口径可接受的场景下作为校验或备份

**验证**：
- [ ] 未配置 Token 时自动跳过
- [ ] 日线返回有效数据（不复权）
- [ ] 频率超限进入黑名单

---

### T2.9 S8: iTick 适配器（可选，仅注册用户）

**目标**：实现 iTick 实时快照和分钟线接口

**文件**：`data_provider/market_router/providers/itick_provider.py`

**前置条件**：
- 用户已注册 iTick 并获取免费 Token
- 环境变量 `ITICK_TOKEN` 已配置

**频率硬限制**：
- REST ≤ 5 次/分钟（即 ≥ 12s/次）
- WebSocket 1 连接 / 3 订阅

**适用场景**：仅用于少量标的兜底

**验证**：
- [ ] 未配置 Token 时自动跳过
- [ ] 实时快照返回有效数据
- [ ] 频率超限进入黑名单

---

### T2.10 Baostock 历史数据适配器

**目标**：实现 Baostock 历史日线兜底接口

**文件**：`data_provider/market_router/providers/baostock_provider.py`

**使用模式**：
- 仅用于历史日线/分钟线的本地缓存填充
- 以"一次拉取、本地复用"的模式使用
- 不进入实时降级链

**频率硬限制**：
- 每日 ≤ 50,000 次
- 单连接串行，不支持并发

**北交所**：不支持

**验证**：
- [ ] 日线返回有效数据
- [ ] 并发请求时被拒绝

---

## T3: 路由核心层

### T3.1 频率控制器

**目标**：实现各源的硬性频率控制

**文件**：`data_provider/market_router/rate_limiter.py`

**接口**：
```python
class RateLimiter:
    def acquire(self, provider_id: str) -> bool:
        """尝试获取请求许可。返回 False 表示应跳过该源。"""
        
    def record_success(self, provider_id: str) -> None:
        """记录成功请求"""
        
    def record_failure(self, provider_id: str, reason: str) -> None:
        """记录失败请求"""
```

**硬约束常量**：
```python
RATE_LIMITS = {
    "S1_tencent": {"min_interval_ms": 100},
    "S2_tencent_kline": {"min_interval_ms": 200},
    "S3_sina": {"max_daily": 1050, "max_qps": 5},
    "S4_ashareapi": {"backoff_on_429_s": 2, "skip_after_429_count": 3},
    "S5_zzshare": {"min_interval_s": 1},
    "S7_tushare": {"max_per_minute": 50, "max_daily": 8000},
    "S8_itick": {"max_per_minute": 5},
}
```

**验证**：
- [ ] S1 连续请求间隔 ≥ 100ms
- [ ] S3 日请求计数达到 1050 时拒绝后续请求
- [ ] S4 连续 3 次 429 后本批次跳过

---

### T3.2 可观测性模块

**目标**：实现结构化日志和指标记录

**文件**：`data_provider/market_router/observability.py`

**日志格式**（JSON 行）：
```json
{
    "timestamp": "2026-11-09T13:30:00Z",
    "code": "600519",
    "data_type": "realtime|daily|minute",
    "provider_hit": "S1_tencent",
    "success": true,
    "latency_ms": 120,
    "attempts": [
        {"provider": "S1_tencent", "success": false, "latency_ms": 120, "reason": "timeout"},
        {"provider": "S3_sina", "success": true, "latency_ms": 340, "reason": null}
    ]
}
```

**日志文件**：`market_data_router.log`

**监控规则**：
- 主源（S1/S2/S5）占比持续低于 50% 时告警
- `ALL_FAILED` 时记录完整 `attempts`

**验证**：
- [ ] 每次调用都输出结构化日志
- [ ] 日志文件可被 `jq` 解析
- [ ] `ALL_FAILED` 时包含完整尝试记录

---

### T3.3 MarketDataRouter 主类

**目标**：实现统一路由入口

**文件**：`data_provider/market_router/router.py`

**接口**：
```python
class MarketDataRouter:
    def get_calendar(self, start: str, end: str) -> list[str]:
        """返回区间内交易日列表。离线实现，零网络。"""
        
    def get_realtime(self, codes: list[str]) -> dict[str, dict | None]:
        """批量实时快照。按 §5.1 降级链逐源尝试。"""
        
    def get_daily(self, code: str, start: str | None, end: str | None, count: int | None) -> pd.DataFrame | None:
        """单只标的日线。按 §5.2 降级链。"""
        
    def get_minute(self, code: str, freq: str, count: int) -> pd.DataFrame | None:
        """分钟K线。按 §5.3 降级链。"""
        
    def get_last_call_metadata(self) -> dict:
        """返回最近一次调用的元信息（provider_hit, attempts 等）"""
```

**统一返回列**：
```
date (str, YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS), open, high, low, close, volume, amount(可选)
```

**单位换算**：
- `volume` 统一为"手"
- `amount` 统一为"元"（若源返回万元，须换算）

**验证**：
- [ ] 接口定义完整
- [ ] 返回列统一
- [ ] 元信息可查询

---

### T3.4 北交所边界处理

**目标**：在路由层入口识别北交所代码并标记

**文件**：`data_provider/market_router/router.py`

**逻辑**：
```python
def _is_bse_code(code: str) -> bool:
    """识别北交所代码：8xxxxx, 4xxxxx, 920xxx"""
    code = code.strip()
    if code.startswith(("8", "4", "920")):
        return True
    return False
```

**各链路行为**：
- **实时快照**：S1 支持北交所，正常按降级链尝试
- **日K线**：跳过 S2，从 S4 或 S5 开始尝试
- **分钟K线**：跳过 S2，从 S5 开始尝试

**验证**：
- [ ] 北交所代码被正确识别
- [ ] 日K线链路中 S2 不被调用
- [ ] 批次中混合北交所和沪深代码时不中断

---

## T4: 降级链实现

### T4.1 实时快照降级链

**目标**：实现实时快照的多源降级逻辑

**文件**：`data_provider/market_router/chains/realtime_chain.py`

**降级顺序**：
```
S1 腾讯实时快照 (qt.gtimg.cn)
   ↓ 失败 / 频率受限
S3 新浪实时快照 (hq.sinajs.cn)
   ↓ 失败
S4 ashareapi quote
   ↓ 失败
S5 zzshare rt_k
   ↓ 失败
S6 ApocData quote
   ↓ 失败
S8 iTick (仅在已注册且 Token 已配置时尝试)
   ↓ 全部失败
该标的本次实时快照返回 None，记录 ALL_FAILED
```

**频率硬约束**：
- S1 请求间隔 ≥ 100ms
- S3 单 IP 日请求硬上限 1050 次，并发 ≤ 5 QPS
- S8 ≤ 5 次/分钟
- **禁止在实时链路上使用 S7（Tushare）**

**验证**：
- [ ] 手动禁用 S1，确认 S3 被自动触发
- [ ] 日志中 `provider_hit` 为 S3
- [ ] 全部失败时返回 `None` 并记录 `ALL_FAILED`

---

### T4.2 日K线降级链

**目标**：实现日K线的多源降级逻辑

**文件**：`data_provider/market_router/chains/daily_chain.py`

**降级顺序**：
```
S2 腾讯K线 (web.ifzq.gtimg.cn)
   ↓ 失败 / 返回空（北交所跳过）
S4 ashareapi kline
   ↓ 失败
S5 zzshare daily
   ↓ 失败
S7 Tushare daily (仅在已注册且 Token 已配置、且不复权口径可接受时尝试)
   ↓ 失败
S6 ApocData (若其接口覆盖日线)
   ↓ 失败
Baostock (仅作为历史日线兜底)
   ↓ 全部失败
该标的本次日线返回 None，记录 ALL_FAILED
```

**北交所边界**：
- S2 不支持北交所，遇 `bj`/`920` 开头代码直接跳过
- 从 S4 或 S5 开始尝试

**验证**：
- [ ] 手动禁用 S2，确认 S4 或 S5 被自动触发
- [ ] 北交所代码不触发 S2
- [ ] 全部失败时返回 `None` 并记录 `ALL_FAILED`

---

### T4.3 分钟K线降级链

**目标**：实现分钟K线的多源降级逻辑

**文件**：`data_provider/market_router/chains/minute_chain.py`

**降级顺序**：
```
S5 zzshare stk_mins
   ↓ 失败
S2 腾讯分钟K线 (kline/mkline)
   ↓ 失败（北交所跳过 S2）
S4 ashareapi kline(period='m1'...'m60') (付费端点，仅在已配置 Key 时尝试)
   ↓ 失败
S8 iTick 分钟线 (仅在已注册且 Token 已配置时尝试)
   ↓ 全部失败
该标的本次分钟线返回 None，记录 ALL_FAILED
```

**北交所边界**：
- 跳过 S2，从 S5 开始尝试

**验收事实**：
- 分钟线是免费源覆盖最薄弱的部分
- 不要求分钟线链路在所有场景下都有免注册兜底

**验证**：
- [ ] 各频率（1min/5min/15min/30min/60min）返回有效数据
- [ ] 北交所代码不触发 S2
- [ ] 全部失败时返回 `None` 并记录 `ALL_FAILED`

---

## T5: 可观测性与监控

### T5.1 结构化日志实现

**目标**：每次调用输出 JSON 行格式日志

**文件**：`data_provider/market_router/observability.py`

**日志字段**：
```json
{
    "timestamp": "2026-11-09T13:30:00Z",
    "code": "600519",
    "data_type": "realtime|daily|minute",
    "provider_hit": "S1_tencent|S2_tencent_kline|...|ALL_FAILED",
    "success": true,
    "latency_ms": 120,
    "attempts": [
        {"provider": "S1_tencent", "success": false, "latency_ms": 120, "reason": "timeout"},
        {"provider": "S3_sina", "success": true, "latency_ms": 340, "reason": null}
    ]
}
```

**日志文件**：`market_data_router.log`

**验证**：
- [ ] 日志文件存在且可写
- [ ] 每行都是合法 JSON
- [ ] 包含所有必需字段

---

### T5.2 监控告警规则

**目标**：实现主源占比监控和 ALL_FAILED 告警

**文件**：`data_provider/market_router/observability.py`

**监控规则**：
- 主源（S1/S2/S5）占比持续低于 50% 时记录告警
- 出现 `ALL_FAILED` 时记录完整 `attempts`

**实现建议**：
- 使用滑动窗口统计最近 N 次调用的主源占比
- 告警写入日志，级别为 `WARNING`

**验证**：
- [ ] 主源占比低于 50% 时触发告警
- [ ] `ALL_FAILED` 时日志包含完整尝试记录

---

## T6: 测试与验证

### T6.1 单源连通性测试

**目标**：验证每个数据源的最小请求

**文件**：`tests/test_market_router/test_provider_connectivity.py`

**验证项**：
- [ ] S1 腾讯实时：1 只沪深标的返回有效数据
- [ ] S2 腾讯K线：1 只沪深标的日线返回有效数据
- [ ] S3 新浪实时：1 只沪深标的返回有效数据
- [ ] S4 ashareapi：1 只沪深标的实时快照返回有效数据
- [ ] S5 zzshare：1 只沪深标的日线返回有效数据
- [ ] S6 ApocData：1 只沪深标的实时快照返回有效数据（若可用）
- [ ] 至少 4 个源返回有效数据
- [ ] 记录每个源的延迟

---

### T6.2 实时降级链模拟失效测试

**目标**：验证实时快照降级链的自动切换

**文件**：`tests/test_market_router/test_realtime_chain.py`

**测试场景**：
- [ ] 手动禁用 S1（如将 URL 指向不可达地址）
- [ ] 确认 S3 被自动触发并返回数据
- [ ] 日志中 `provider_hit` 为 S3

---

### T6.3 日线降级链模拟失效测试

**目标**：验证日K线降级链的自动切换

**文件**：`tests/test_market_router/test_daily_chain.py`

**测试场景**：
- [ ] 手动禁用 S2
- [ ] 确认 S4 或 S5 被自动触发并返回日线数据
- [ ] 日志中 `provider_hit` 为对应备源

---

### T6.4 频率合规测试

**目标**：验证频率控制逻辑

**文件**：`tests/test_market_router/test_rate_limiter.py`

**测试场景**：
- [ ] 连续请求 10 只标的的日线，不触发 HTTP 403/429
- [ ] S3 的日请求计数在 1050 以内（本地计数器验证）
- [ ] S1 请求间隔 ≥ 100ms
- [ ] S4 连续 3 次 429 后跳过该源

---

### T6.5 北交所边界测试

**目标**：验证北交所代码的特殊处理

**文件**：`tests/test_market_router/test_bse_boundary.py`

**测试场景**：
- [ ] 构造批次：1 只北交所（如 `830799`）+ 2 只沪深（如 `600519`, `000001`）
- [ ] 沪深标的从 S2 正常返回
- [ ] 北交所标的不触发 S2（日志中不得出现北交所代码请求 S2 的记录）
- [ ] 批次完整结束
- [ ] 北交所标的若全部源失败，记录 `ALL_FAILED` 告警，不影响沪深标的

---

### T6.6 批次容错测试

**目标**：验证无效代码不中断批次

**文件**：`tests/test_market_router/test_batch_fault_tolerance.py`

**测试场景**：
- [ ] 构造批次：1 只无效代码（如 `999999`）+ 2 只有效代码
- [ ] 执行日线获取
- [ ] 无效代码的 `ALL_FAILED` 被记录
- [ ] 有效代码正常返回

---

### T6.7 断网日历测试

**目标**：验证交易日历的离线可用性

**文件**：`tests/test_market_router/test_calendar_offline.py`

**测试场景**：
- [ ] 断开网络
- [ ] 执行 `get_calendar("2026-11-01", "2026-11-30")`
- [ ] 正常返回交易日列表

---

### T6.8 可观测性验证

**目标**：验证日志完整性

**文件**：`tests/test_market_router/test_observability.py`

**测试场景**：
- [ ] 检查日志文件
- [ ] 确认每次调用都有 `provider_hit`、`success`、`latency_ms`、`attempts` 字段
- [ ] `ALL_FAILED` 时 `attempts` 包含完整记录

---

## 验收清单

**V1-V8 全部通过，方可视为满足需求**：

| 编号 | 验证项 | 通过标准 | 状态 |
|------|--------|----------|------|
| V1 | 单源逐项连通性 | 至少 4 个源返回有效数据 | ⬜ |
| V2 | 实时降级链模拟失效 | S1 禁用后 S3 自动触发 | ⬜ |
| V3 | 日线降级链模拟失效 | S2 禁用后 S4/S5 自动触发 | ⬜ |
| V4 | 频率合规 | 连续请求不触发 403/429 | ⬜ |
| V5 | 北交所边界 | 北交所代码不进入 S2 | ⬜ |
| V6 | 批次容错 | 无效代码不中断批次 | ⬜ |
| V7 | 断网日历 | 断网环境下日历正常返回 | ⬜ |
| V8 | 可观测性 | 每次调用都有完整日志 | ⬜ |

---

## 依赖关系图

```
T1.1 (安装依赖)
   ↓
T1.2 (目录结构) ──→ T1.3 (交易日历)
   ↓
T2.1 (Provider 基类)
   ↓
T2.2-T2.10 (各源适配器，可并行)
   ↓
T3.1 (频率控制器) ──→ T3.2 (可观测性)
   ↓                        ↓
T3.3 (Router 主类) ←───────┘
   ↓
T3.4 (北交所边界)
   ↓
T4.1-T4.3 (降级链，可并行)
   ↓
T5.1-T5.2 (监控告警)
   ↓
T6.1-T6.8 (测试验证，可并行)
```

---

## 风险与注意事项

1. **mootdx 已退役**：现有代码中如有 `mootdx` 引用，应标记为废弃并从自动链路移除
2. **S7 Tushare 仅不复权**：如果策略依赖前复权日线，不得将 S7 作为主日线源
3. **分钟线免费源薄弱**：不要求分钟线链路在所有场景下都有免注册兜底
4. **北交所 S2 不支持**：必须在路由层入口识别并跳过
5. **S3 新浪日请求上限**：硬编码 1050 次/日，超出后主动拒绝

---

## 文档更新

完成后需更新：
- [ ] `requirements.txt` - 添加新依赖
- [ ] `.env.example` - 添加 `ITICK_TOKEN` 等可选配置
- [ ] `docs/CHANGELOG.md` - 记录新功能
- [ ] `README.md` - 如有必要，添加 MarketDataRouter 使用说明

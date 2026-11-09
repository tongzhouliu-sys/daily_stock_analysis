# 开发任务：消除 Tushare `trade_cal` API 依赖

## 任务概述

**目标**：消除 `data_provider/tushare_fetcher.py` 对 Tushare `trade_cal` API 的直接调用，改用现有的 `src/core/trading_calendar.py` 模块，彻底解决低积分账号"1 次/小时"的限频问题。

**优先级**：P1（影响市场统计、筹码分布、行业板块排名等功能的可用性）

**预估工作量**：2-3 天（含测试和验证）

---

## 背景与问题

### 当前实现

`data_provider/tushare_fetcher.py` 的 `_get_trade_dates()` 方法（第 301-330 行）：

```python
def _get_trade_dates(self, end_date: Optional[str] = None) -> List[str]:
    """按自然日刷新交易日历缓存，避免服务跨日后继续复用旧日历。"""
    if self._api is None:
        return []

    china_now = self._get_china_now()
    requested_end_date = end_date or china_now.strftime("%Y%m%d")

    if self.date_list is not None and self._date_list_end == requested_end_date:
        return self.date_list

    start_date = (china_now - timedelta(days=20)).strftime("%Y%m%d")
    df_cal = self._call_api_with_rate_limit(
        "trade_cal",  # <-- 这里触发限频
        exchange="SSE",
        start_date=start_date,
        end_date=requested_end_date,
    )
    # ... 处理返回结果
```

该方法被以下功能调用：
- `get_market_stats()`（第 897 行）：获取市场涨跌统计
- `get_trade_time()`（第 1065 行）：确定数据获取的时间窗口
- 间接影响：`get_sector_rankings()`（行业板块排名）、`get_chip_distribution()`（筹码分布）

### 问题

1. **限频风险**：Tushare 免费账号（<2000 积分）调用 `trade_cal` 会触发"1 次/小时"限频
2. **重复实现**：仓库已有 `src/core/trading_calendar.py` 模块，基于 `exchange-calendars` 库实现本地交易日历计算，无需 API 调用
3. **未复用现有资产**：`trading_calendar.py` 已被 20+ 个文件使用（`main.py`、`src/core/pipeline.py`、`src/services/screening/daily.py` 等），但 `tushare_fetcher.py` 完全没有使用

### 现有资产

`src/core/trading_calendar.py` 提供以下能力：
- `is_market_open(market, check_date)`：判断指定市场在指定日期是否开市
- `get_trading_dates(market, start_date, end_date)`：获取交易日期序列
- `get_next_trading_date(market, check_date)`：获取下一个交易日
- `get_effective_trading_date(market, current_time)`：获取有效交易日（用于 checkpoint/resume）
- `get_open_markets_today()`：获取今日开市的市场集合

该模块基于 `exchange-calendars` 库（已在 `requirements.txt` 中声明），完全本地计算，无任何 API 调用。

---

## 任务目标

### 主要目标

1. **消除 `trade_cal` API 调用**：将 `tushare_fetcher.py` 的 `_get_trade_dates()` 方法改为使用 `src/core/trading_calendar.py`
2. **保持功能一致性**：确保市场统计、筹码分布、行业板块排名等功能的行为不变
3. **添加本地缓存**：按照文档建议，实现"一次获取，长期使用"的缓存策略
4. **完善 fallback 链**：当 `exchange-calendars` 不可用时，降级到 Baostock 或 AkShare

### 次要目标

1. **统一交易日历入口**：确保所有交易日历相关逻辑都通过 `src/core/trading_calendar.py`
2. **补充测试覆盖**：为新的实现添加单元测试和集成测试
3. **更新文档**：在 `docs/CHANGELOG.md` 中记录此次改动

---

## 实现方案

### 方案 A：直接复用 `trading_calendar.py`（推荐）

**优点**：
- 改动最小，复用现有资产
- `exchange-calendars` 已在 `requirements.txt` 中声明
- 已有 20+ 个文件使用，稳定性已验证
- 完全本地计算，无 API 调用，无限频风险

**缺点**：
- `exchange-calendars` 对中国 A 股的可靠上限为 2026-12-31（文档已指出）
- 对 2027 年及之后的日期需要等待库更新或人工补充

**实现步骤**：

1. **修改 `_get_trade_dates()` 方法**：

```python
def _get_trade_dates(self, end_date: Optional[str] = None) -> List[str]:
    """使用 trading_calendar 模块获取交易日期，避免调用 trade_cal API。"""
    from src.core.trading_calendar import get_trading_dates, get_next_trading_date
    
    china_now = self._get_china_now()
    requested_end_date = end_date or china_now.strftime("%Y%m%d")
    
    # 缓存命中
    if self.date_list is not None and self._date_list_end == requested_end_date:
        return self.date_list
    
    # 使用 trading_calendar 获取交易日期
    end_dt = datetime.strptime(requested_end_date, "%Y%m%d").date()
    start_dt = end_dt - timedelta(days=30)  # 获取最近 30 天的交易日
    
    try:
        trading_dates = get_trading_dates("cn", start_dt, end_dt)
        if trading_dates is not None and len(trading_dates) > 0:
            # 转换为 YYYYMMDD 格式，倒序排列
            trade_dates = [dt.strftime("%Y%m%d") for dt in trading_dates]
            trade_dates = sorted(trade_dates, reverse=True)
            self.date_list = trade_dates
            self._date_list_end = requested_end_date
            return trade_dates
    except Exception as e:
        logger.warning(f"[Tushare] trading_calendar 获取交易日期失败: {e}，尝试 fallback")
    
    # Fallback: 使用 Baostock
    trade_dates = self._get_trade_dates_from_baostock(requested_end_date)
    if trade_dates:
        self.date_list = trade_dates
        self._date_list_end = requested_end_date
        return trade_dates
    
    logger.error("[Tushare] 所有交易日历来源均失败")
    self.date_list = []
    self._date_list_end = requested_end_date
    return self.date_list

def _get_trade_dates_from_baostock(self, end_date: str) -> List[str]:
    """从 Baostock 获取交易日期作为 fallback。"""
    try:
        from data_provider.baostock_fetcher import BaostockFetcher
        import baostock as bs
        
        end_dt = datetime.strptime(end_date, "%Y%m%d").date()
        start_dt = end_dt - timedelta(days=30)
        
        fetcher = BaostockFetcher()
        with fetcher._baostock_session() as bs_module:
            rs = bs_module.query_trade_dates(
                start_date=start_dt.strftime("%Y-%m-%d"),
                end_date=end_dt.strftime("%Y-%m-%d")
            )
            
            data = []
            while (rs.error_code == "0") & rs_module.next():
                data.append(rs.get_row_data())
            
            if not data:
                return []
            
            df = pd.DataFrame(data, columns=rs.fields)
            trading_days = df.loc[df["is_trading_day"] == "1", "calendar_date"]
            trading_days = [dt.replace("-", "") for dt in trading_days]
            return sorted(trading_days, reverse=True)
    except Exception as e:
        logger.warning(f"[Tushare] Baostock fallback 失败: {e}")
        return []
```

2. **移除 `trade_cal` API 调用**：删除第 314-320 行的 `_call_api_with_rate_limit("trade_cal", ...)` 调用

3. **更新测试**：
   - 修改 `tests/test_tushare_fetcher_followups.py` 中与 `_get_trade_dates()` 相关的测试
   - 添加新的测试用例，验证 fallback 链的行为

### 方案 B：添加本地持久化缓存（增强方案）

在方案 A 的基础上，添加本地 Parquet/CSV 缓存，实现"一次获取，长期使用"：

**实现步骤**：

1. **添加缓存文件路径配置**：

```python
# src/config.py
class Config:
    # ... 现有配置
    
    @property
    def trade_calendar_cache_path(self) -> str:
        """交易日历缓存文件路径"""
        return os.getenv("TRADE_CALENDAR_CACHE_PATH", "data/trade_cal.parquet")
```

2. **实现缓存读写逻辑**：

```python
def _get_trade_dates(self, end_date: Optional[str] = None) -> List[str]:
    """使用本地缓存 + trading_calendar + fallback 链获取交易日期。"""
    from src.core.trading_calendar import get_trading_dates
    from src.config import get_config
    
    config = get_config()
    cache_path = config.trade_calendar_cache_path
    
    china_now = self._get_china_now()
    requested_end_date = end_date or china_now.strftime("%Y%m%d")
    
    # 缓存命中
    if self.date_list is not None and self._date_list_end == requested_end_date:
        return self.date_list
    
    # 尝试从本地缓存读取
    if os.path.exists(cache_path):
        try:
            df = pd.read_parquet(cache_path)
            trade_dates = df["trade_date"].astype(str).tolist()
            # 检查缓存是否包含请求的日期范围
            if trade_dates and max(trade_dates) >= requested_end_date:
                # 过滤出请求范围内的日期
                trade_dates = [dt for dt in trade_dates if dt <= requested_end_date]
                trade_dates = sorted(trade_dates, reverse=True)
                self.date_list = trade_dates[:30]  # 只返回最近 30 个
                self._date_list_end = requested_end_date
                return self.date_list
        except Exception as e:
            logger.warning(f"[Tushare] 读取本地缓存失败: {e}")
    
    # 缓存未命中或过期，使用 trading_calendar 获取
    end_dt = datetime.strptime(requested_end_date, "%Y%m%d").date()
    start_dt = end_dt - timedelta(days=365)  # 获取最近 1 年的交易日
    
    try:
        trading_dates = get_trading_dates("cn", start_dt, end_dt)
        if trading_dates is not None and len(trading_dates) > 0:
            trade_dates = [dt.strftime("%Y%m%d") for dt in trading_dates]
            trade_dates = sorted(trade_dates, reverse=True)
            
            # 写入本地缓存
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                cache_df = pd.DataFrame({"trade_date": pd.to_datetime(trade_dates)})
                cache_df.to_parquet(cache_path)
                logger.info(f"[Tushare] 交易日历已缓存到 {cache_path}")
            except Exception as e:
                logger.warning(f"[Tushare] 写入本地缓存失败: {e}")
            
            self.date_list = trade_dates
            self._date_list_end = requested_end_date
            return trade_dates
    except Exception as e:
        logger.warning(f"[Tushare] trading_calendar 获取交易日期失败: {e}")
    
    # Fallback: Baostock
    trade_dates = self._get_trade_dates_from_baostock(requested_end_date)
    if trade_dates:
        self.date_list = trade_dates
        self._date_list_end = requested_end_date
        return trade_dates
    
    logger.error("[Tushare] 所有交易日历来源均失败")
    self.date_list = []
    self._date_list_end = requested_end_date
    return self.date_list
```

3. **添加年度增量维护脚本**：

创建 `scripts/update_trade_calendar.py`：

```python
#!/usr/bin/env python3
"""
年度交易日历维护脚本

用途：每年交易所公布下一年度休市安排后，运行此脚本更新本地缓存。

用法：
    python scripts/update_trade_calendar.py
"""

import os
import sys
from datetime import datetime, timedelta

import pandas as pd

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_config


def update_trade_calendar():
    """更新交易日历缓存。"""
    config = get_config()
    cache_path = config.trade_calendar_cache_path
    
    # 获取当前日期和未来 2 年
    today = datetime.now()
    end_date = today.replace(year=today.year + 2)
    
    # 使用 Baostock 获取全量历史交易日
    try:
        import baostock as bs
        
        lg = bs.login()
        if lg.error_code != '0':
            print(f"Baostock 登录失败: {lg.error_msg}")
            return False
        
        rs = bs.query_trade_dates(
            start_date="1990-01-01",
            end_date=end_date.strftime("%Y-%m-%d")
        )
        
        data = []
        while (rs.error_code == "0") & rs.next():
            data.append(rs.get_row_data())
        
        bs.logout()
        
        if not data:
            print("未获取到交易日历数据")
            return False
        
        df = pd.DataFrame(data, columns=rs.fields)
        trading_days = df.loc[df["is_trading_day"] == "1", "calendar_date"]
        trading_days = pd.to_datetime(trading_days)
        
        # 写入缓存
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        cache_df = pd.DataFrame({"trade_date": trading_days})
        cache_df.to_parquet(cache_path)
        
        print(f"交易日历已更新到 {cache_path}")
        print(f"覆盖范围: {trading_days.min()} 至 {trading_days.max()}")
        print(f"总计 {len(trading_days)} 个交易日")
        
        return True
    except Exception as e:
        print(f"更新交易日历失败: {e}")
        return False


if __name__ == "__main__":
    success = update_trade_calendar()
    sys.exit(0 if success else 1)
```

---

## 推荐方案

**优先实施方案 A**，原因：
1. 改动最小，风险最低
2. 复用现有资产，无需引入新的依赖
3. `exchange-calendars` 已验证稳定，覆盖到 2026-12-31
4. 可以立即解决限频问题

**后续可选增强**：如果未来需要覆盖 2027 年及之后的日期，再实施方案 B 的本地持久化缓存部分。

---

## 验证计划

### 单元测试

1. **修改现有测试**：
   - `tests/test_tushare_fetcher_followups.py`：更新与 `_get_trade_dates()` 相关的测试用例
   - 确保 mock 对象从 Tushare API 改为 `trading_calendar` 模块

2. **添加新测试**：
   - 测试 `trading_calendar` 正常返回的场景
   - 测试 `trading_calendar` 失败、Baostock fallback 成功的场景
   - 测试所有来源均失败的场景
   - 测试缓存命中和缓存过期的场景

### 集成测试

1. **本地验证**：
   ```bash
   # 运行 Tushare Fetcher 相关测试
   python -m pytest tests/test_tushare_fetcher_followups.py -v
   
   # 运行市场统计相关测试
   python -m pytest tests/test_market_structure_service.py -v
   
   # 运行筹码分布相关测试
   python -m pytest tests/ -k "chip" -v
   ```

2. **端到端验证**：
   ```bash
   # 使用低积分 Tushare 账号测试
   export TUSHARE_TOKEN=<low_credit_token>
   
   # 测试市场统计
   python main.py --market-review
   
   # 测试筹码分布
   python main.py --stocks 600519 --chip
   
   # 测试行业板块排名
   python main.py --stocks 600519 --sector
   ```

3. **CI 验证**：
   - 提交 PR 后，观察 CI 是否通过
   - 重点关注 `backend-gate` 和 `network-smoke` 检查

### 回归测试

1. **对比验证**：
   - 在改动前后，使用相同的股票和时间窗口，对比市场统计结果
   - 确保涨跌家数、涨停跌停数、成交额等关键指标一致

2. **边界场景**：
   - 测试跨日场景（例如周五收盘后、节假日前后）
   - 测试非交易日场景（周末、法定节假日）
   - 测试交易日不同时段（盘前、盘中、盘后）

---

## 风险与缓解

### 风险 1：`exchange-calendars` 对中国 A 股的覆盖上限

**风险描述**：`exchange-calendars` 对中国 A 股（XSHG）的可靠上限为 2026-12-31。对 2027 年及之后的日期，可能无法正确判断交易日。

**缓解措施**：
1. 当前日期为 2026-11-09，距离上限还有 14 个月，短期内无风险
2. 在代码中添加警告日志，当请求日期接近上限时提醒用户
3. 后续可通过本地缓存 + 年度维护脚本解决（方案 B）

### 风险 2：Baostock fallback 的稳定性

**风险描述**：Baostock 作为 fallback 来源，其稳定性和可用性未经验证。

**缓解措施**：
1. 在测试环境中验证 Baostock fallback 的行为
2. 添加详细的日志记录，便于排查问题
3. 后续可添加 AkShare 作为第二 fallback

### 风险 3：功能行为变化

**风险描述**：替换交易日历来源后，可能导致市场统计、筹码分布等功能的行为发生微小变化。

**缓解措施**：
1. 在测试环境中进行充分的对比验证
2. 重点关注跨日、节假日等边界场景
3. 在 PR 描述中详细说明验证结果

---

## 交付物

1. **代码改动**：
   - 修改 `data_provider/tushare_fetcher.py` 的 `_get_trade_dates()` 方法
   - 添加 `_get_trade_dates_from_baostock()` fallback 方法（方案 A）
   - 可选：添加本地缓存逻辑（方案 B）

2. **测试改动**：
   - 修改 `tests/test_tushare_fetcher_followups.py`
   - 添加新的测试用例

3. **文档改动**：
   - 更新 `docs/CHANGELOG.md`，记录此次改动
   - 可选：创建 `scripts/update_trade_calendar.py` 维护脚本（方案 B）

4. **验证证据**：
   - 本地测试截图或日志
   - CI 通过截图
   - 对比验证结果（改动前后）

---

## 时间估算

| 任务 | 预估时间 |
|------|----------|
| 方案 A 实现 | 0.5 天 |
| 单元测试编写 | 0.5 天 |
| 集成测试验证 | 0.5 天 |
| 文档更新 | 0.25 天 |
| PR 创建与 review | 0.25 天 |
| **总计（方案 A）** | **2 天** |
| 方案 B 增强（可选） | +1 天 |

---

## 验收标准

1. ✅ `tushare_fetcher.py` 不再调用 `trade_cal` API
2. ✅ 市场统计、筹码分布、行业板块排名等功能正常工作
3. ✅ 单元测试和集成测试全部通过
4. ✅ CI 通过（`backend-gate`、`network-smoke`）
5. ✅ 在低积分 Tushare 账号下验证无"频率超限"错误
6. ✅ 文档已更新（`docs/CHANGELOG.md`）

---

## 参考资料

- 问题描述文档：`docs/tyrone/Tushare.md`
- 现有交易日历模块：`src/core/trading_calendar.py`
- Tushare Fetcher 实现：`data_provider/tushare_fetcher.py`
- Baostock Fetcher 实现：`data_provider/baostock_fetcher.py`
- 相关测试：`tests/test_tushare_fetcher_followups.py`、`tests/test_trading_calendar.py`

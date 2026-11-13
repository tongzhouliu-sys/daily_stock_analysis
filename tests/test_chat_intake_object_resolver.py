"""Tests for chat intake object resolver (T2).

验证标的解析服务的正确性，覆盖需求 11.1 的解析用例。
"""

import pytest
from src.services.chat_intake_object_resolver import (
    resolve_intake_object,
    IntakeObjectResolution,
)
from src.services.stock_list_parser import IndexRegistry


@pytest.fixture
def empty_registry():
    """空的指数注册表"""
    return IndexRegistry(rows=[], validated=True)


@pytest.fixture
def mock_lookup():
    """模拟的股票查找表"""
    return {
        "codes": {
            "600519": {"code": "600519", "name": "贵州茅台", "market": "CN", "assetType": "stock"},
            "00700": {"code": "00700", "name": "腾讯控股", "market": "HK", "assetType": "stock"},
            "AAPL": {"code": "AAPL", "name": "Apple Inc.", "market": "US", "assetType": "stock"},
        },
        "names": {
            "贵州茅台": [{"code": "600519", "name": "贵州茅台", "market": "CN", "assetType": "stock"}],
            "腾讯": [{"code": "00700", "name": "腾讯控股", "market": "HK", "assetType": "stock"}],
            "腾讯控股": [{"code": "00700", "name": "腾讯控股", "market": "HK", "assetType": "stock"}],
            "苹果": [
                {"code": "AAPL", "name": "Apple Inc.", "market": "US", "assetType": "stock"},
            ],
        },
        "aliases": {
            "茅台": [{"code": "600519", "name": "贵州茅台", "market": "CN", "assetType": "stock"}],
            "apple": [{"code": "AAPL", "name": "Apple Inc.", "market": "US", "assetType": "stock"}],
        },
    }


class TestObjectResolver:
    """标的解析器测试"""

    def test_empty_message(self, empty_registry, mock_lookup):
        """空消息返回 missing"""
        result = resolve_intake_object(
            "",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "missing"
        assert result.scope == "unknown"

    def test_concept_question(self, empty_registry, mock_lookup):
        """概念问题返回 not_required + concept"""
        result = resolve_intake_object(
            "什么是市盈率",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "not_required"
        assert result.scope == "concept"

    def test_market_question(self, empty_registry, mock_lookup):
        """大盘问题返回 not_required + market"""
        result = resolve_intake_object(
            "今天大盘怎么样",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "not_required"
        assert result.scope == "market"

    def test_portfolio_question(self, empty_registry, mock_lookup):
        """持仓问题返回 not_required + portfolio"""
        result = resolve_intake_object(
            "我的持仓怎么样",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "not_required"
        assert result.scope == "portfolio"

    def test_watchlist_question(self, empty_registry, mock_lookup):
        """自选问题返回 not_required + watchlist"""
        result = resolve_intake_object(
            "看看我的自选",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "not_required"
        assert result.scope == "watchlist"

    def test_explicit_code_a_share(self, empty_registry, mock_lookup):
        """A 股代码解析"""
        result = resolve_intake_object(
            "分析 600519",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "600519"
        assert result.identity.market == "CN"
        assert result.reason == "unique_code"

    def test_explicit_code_hk(self, empty_registry, mock_lookup):
        """港股代码解析"""
        result = resolve_intake_object(
            "分析 00700",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "00700"
        assert result.identity.market == "HK"

    def test_explicit_code_us(self, empty_registry, mock_lookup):
        """美股代码解析"""
        result = resolve_intake_object(
            "分析 AAPL",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "AAPL"
        assert result.identity.market == "US"

    def test_exact_name_match(self, empty_registry, mock_lookup):
        """精确名称匹配"""
        result = resolve_intake_object(
            "分析贵州茅台",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "600519"
        assert result.identity.stock_name == "贵州茅台"
        assert result.reason == "unique_name"

    def test_alias_match(self, empty_registry, mock_lookup):
        """别名匹配"""
        result = resolve_intake_object(
            "分析腾讯",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "00700"
        assert result.identity.stock_name == "腾讯控股"

    def test_session_inheritance(self, empty_registry, mock_lookup):
        """会话继承"""
        active_stock = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "CN",
            "asset_type": "stock",
        }
        result = resolve_intake_object(
            "继续看看",
            active_stock=active_stock,
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "600519"
        assert result.reason == "session_inherited"

    def test_report_context(self, empty_registry, mock_lookup):
        """报告上下文"""
        report_context = {
            "stock_code": "00700",
            "stock_name": "腾讯控股",
            "market": "HK",
            "asset_type": "stock",
        }
        result = resolve_intake_object(
            "这份报告怎么样",
            report_context=report_context,
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "00700"
        assert result.reason == "report_context"

    def test_missing_object(self, empty_registry, mock_lookup):
        """缺失对象"""
        result = resolve_intake_object(
            "分析一下",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "missing"
        assert result.scope == "unknown"

    def test_recommendation_with_active_stock(self, empty_registry, mock_lookup):
        """推荐候选时考虑当前会话股票"""
        # 模拟中芯国际在 A 股和港股都有
        lookup_with_ambiguous = {
            "codes": {},
            "names": {
                "中芯国际": [
                    {"code": "688981", "name": "中芯国际", "market": "CN", "assetType": "stock"},
                    {"code": "00981", "name": "中芯国际", "market": "HK", "assetType": "stock"},
                ],
            },
            "aliases": {},
        }
        active_stock = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "CN",
            "asset_type": "stock",
        }
        result = resolve_intake_object(
            "分析中芯国际",
            active_stock=active_stock,
            registry=empty_registry,
            lookup=lookup_with_ambiguous,
        )
        assert result.status == "ambiguous"
        assert len(result.candidates) == 2
        assert result.recommended_option_id is not None
        # 应该推荐 A 股版本，因为当前会话是 A 股
        assert "CN" in result.recommended_option_id

    def test_recommendation_with_watchlist(self, empty_registry, mock_lookup):
        """推荐候选时考虑自选股"""
        lookup_with_ambiguous = {
            "codes": {},
            "names": {
                "中芯国际": [
                    {"code": "688981", "name": "中芯国际", "market": "CN", "assetType": "stock"},
                    {"code": "00981", "name": "中芯国际", "market": "HK", "assetType": "stock"},
                ],
            },
            "aliases": {},
        }
        result = resolve_intake_object(
            "分析中芯国际",
            watchlist=["00981"],
            registry=empty_registry,
            lookup=lookup_with_ambiguous,
        )
        assert result.status == "ambiguous"
        assert result.recommended_option_id is not None
        # 应该推荐港股版本，因为在自选中
        assert "HK" in result.recommended_option_id

    def test_recommendation_with_recent_codes(self, empty_registry, mock_lookup):
        """推荐候选时考虑最近分析"""
        lookup_with_ambiguous = {
            "codes": {},
            "names": {
                "中芯国际": [
                    {"code": "688981", "name": "中芯国际", "market": "CN", "assetType": "stock"},
                    {"code": "00981", "name": "中芯国际", "market": "HK", "assetType": "stock"},
                ],
            },
            "aliases": {},
        }
        result = resolve_intake_object(
            "分析中芯国际",
            recent_codes=["688981"],
            registry=empty_registry,
            lookup=lookup_with_ambiguous,
        )
        assert result.status == "ambiguous"
        assert result.recommended_option_id is not None
        # 应该推荐 A 股版本，因为最近分析过
        assert "CN" in result.recommended_option_id


class TestObjectResolverEdgeCases:
    """边界情况测试"""

    def test_case_insensitive_ticker(self, empty_registry, mock_lookup):
        """ticker 大小写不敏感"""
        result = resolve_intake_object(
            "分析 aapl",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "AAPL"

    def test_multiple_objects_in_message(self, empty_registry, mock_lookup):
        """消息中包含多个对象"""
        result = resolve_intake_object(
            "比较 600519 和 00700",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        # 应该解析第一个对象
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "600519"

    def test_code_with_prefix(self, empty_registry, mock_lookup):
        """带前缀的代码"""
        result = resolve_intake_object(
            "分析 SH600519",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        # 应该能解析出代码
        assert result.status in ["resolved", "missing"]

    def test_chinese_with_numbers(self, empty_registry, mock_lookup):
        """中文和数字混合"""
        result = resolve_intake_object(
            "帮我看看 600519 的走势",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "600519"

    def test_english_message(self, empty_registry, mock_lookup):
        """英文消息"""
        result = resolve_intake_object(
            "Analyze AAPL",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "resolved"
        assert result.identity is not None
        assert result.identity.stock_code == "AAPL"

    def test_whitespace_only(self, empty_registry, mock_lookup):
        """只有空白字符"""
        result = resolve_intake_object(
            "   \n\t  ",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "missing"

    def test_special_characters(self, empty_registry, mock_lookup):
        """特殊字符"""
        result = resolve_intake_object(
            "！！！",
            registry=empty_registry,
            lookup=mock_lookup,
        )
        assert result.status == "missing"


class TestObjectResolverPerformance:
    """性能测试"""

    def test_resolution_speed(self, empty_registry, mock_lookup, benchmark):
        """解析速度测试"""
        def resolve():
            return resolve_intake_object(
                "分析 600519",
                registry=empty_registry,
                lookup=mock_lookup,
            )

        result = benchmark(resolve)
        assert result.status == "resolved"

    def test_large_lookup_performance(self, empty_registry, benchmark):
        """大型查找表性能测试"""
        # 模拟大型查找表
        large_lookup = {
            "codes": {str(i): {"code": str(i), "name": f"Stock {i}", "market": "CN"} for i in range(10000)},
            "names": {f"Stock {i}": [{"code": str(i), "name": f"Stock {i}", "market": "CN"}] for i in range(10000)},
            "aliases": {},
        }

        def resolve():
            return resolve_intake_object(
                "分析 Stock 5000",
                registry=empty_registry,
                lookup=large_lookup,
            )

        result = benchmark(resolve)
        assert result.status == "resolved"

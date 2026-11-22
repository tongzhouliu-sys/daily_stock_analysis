# -*- coding: utf-8 -*-
"""
Regression tests for SerpAPI organic content fetch throttling (Issue #882).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import SerpAPISearchProvider


class _FakeGoogleSearch:
    response_payload = {}
    init_params = []

    def __init__(self, params):
        type(self).init_params.append(params)

    def get_dict(self):
        return type(self).response_payload

    @classmethod
    def reset(cls) -> None:
        cls.response_payload = {}
        cls.init_params = []


def _fake_serpapi_module() -> ModuleType:
    module = ModuleType("serpapi")
    module.GoogleSearch = _FakeGoogleSearch
    return module


class _KeyedFakeGoogleSearch:
    """Fake GoogleSearch returning a payload per api_key."""

    responses_by_key = {}
    init_params = []

    def __init__(self, params):
        self.params = params
        type(self).init_params.append(params)

    def get_dict(self):
        return type(self).responses_by_key.get(self.params.get("api_key"), {})

    @classmethod
    def reset(cls, responses_by_key=None) -> None:
        cls.responses_by_key = dict(responses_by_key or {})
        cls.init_params = []


class TestSerpAPISearchProvider(unittest.TestCase):
    """Tests for provider-specific organic content fetch behavior."""

    def setUp(self) -> None:
        # 用量计数写到临时目录，避免测试污染 data/cache 下的真实计数
        self._usage_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._usage_dir.cleanup)
        self._usage_store_path = Path(self._usage_dir.name) / "serpapi_key_usage.json"

    def _make_provider(self, api_keys, **kwargs):
        return SerpAPISearchProvider(
            api_keys,
            key_usage_store_path=self._usage_store_path,
            **kwargs,
        )

    def _patch_serpapi(self, payload):
        _FakeGoogleSearch.reset()
        _FakeGoogleSearch.response_payload = payload
        return patch.dict(sys.modules, {"serpapi": _fake_serpapi_module()})

    def _patch_keyed_serpapi(self, responses_by_key):
        _KeyedFakeGoogleSearch.reset(responses_by_key)
        module = ModuleType("serpapi")
        module.GoogleSearch = _KeyedFakeGoogleSearch
        return patch.dict(sys.modules, {"serpapi": module})

    def test_provider_skips_body_fetch_when_snippet_is_sufficient(self) -> None:
        provider = self._make_provider(["dummy_key"])
        long_snippet = "这是一段已经足够长的摘要。 " * 12

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Long summary result",
                        "link": "https://example.com/long-summary",
                        "snippet": long_snippet,
                        "source": "Example",
                        "date": "2026-03-20",
                    }
                ]
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].snippet, long_snippet.strip())
        self.assertEqual(resp.results[0].published_date, "2026-03-20")
        mock_fetch.assert_not_called()
        self.assertEqual(_FakeGoogleSearch.init_params[0]["num"], 3)

    def test_provider_uses_rich_snippet_extensions_without_fetching(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Structured summary result",
                        "link": "https://example.com/structured-summary",
                        "source": "Example",
                        "rich_snippet": {
                            "top": {
                                "extensions": [
                                    "Q4 revenue grows 22% year over year and margin keeps improving",
                                    "Management raises full-year guidance after demand stays strong",
                                ]
                            },
                            "bottom": {
                                "extensions": [
                                    "Brokerages lift target prices and keep overweight ratings",
                                ]
                            },
                        },
                    }
                ]
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertIn("Q4 revenue grows 22%", resp.results[0].snippet)
        self.assertIn("Brokerages lift target prices", resp.results[0].snippet)
        mock_fetch.assert_not_called()

    def test_provider_uses_detected_rich_snippet_fields_without_fetching(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Detected extensions result",
                        "link": "https://example.com/detected-extensions",
                        "source": "Example",
                        "rich_snippet": {
                            "top": {
                                "detected_extensions": {
                                    "price": "$125.30",
                                    "updated_at": "1 hour ago",
                                }
                            },
                            "bottom": {
                                "detected_extensions": {
                                    "rating": 4.5,
                                    "votes": 1200,
                                }
                            },
                        },
                    }
                ]
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertIn("price: $125.30", resp.results[0].snippet)
        self.assertIn("updated at: 1 hour ago", resp.results[0].snippet)
        self.assertIn("rating: 4.5", resp.results[0].snippet)
        self.assertIn("votes: 1200", resp.results[0].snippet)
        mock_fetch.assert_not_called()

    def test_provider_preserves_falsy_detected_extensions_without_fetching(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Zero-like extensions result",
                        "link": "https://example.com/zero-like-extensions",
                        "source": "Example",
                        "rich_snippet": {
                            "top": {
                                "detected_extensions": {
                                    "price": 0,
                                    "market_open": False,
                                }
                            },
                            "bottom": {
                                "detected_extensions": {
                                    "votes": 0,
                                }
                            },
                        },
                    }
                ]
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertIn("price: 0", resp.results[0].snippet)
        self.assertIn("market open: False", resp.results[0].snippet)
        self.assertIn("votes: 0", resp.results[0].snippet)
        mock_fetch.assert_not_called()

    def test_provider_ignores_scalar_detected_extensions_and_still_fetches(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Malformed detected extensions result",
                        "link": "https://example.com/malformed-detected-extensions",
                        "snippet": "摘要过短",
                        "source": "Example",
                        "rich_snippet": {
                            "top": {
                                "detected_extensions": True,
                            },
                        },
                    }
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            return_value="网页正文补充信息 " * 40,
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        mock_fetch.assert_called_once_with(
            "https://example.com/malformed-detected-extensions",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertNotIn("True", resp.results[0].snippet)
        self.assertIn("【网页详情】", resp.results[0].snippet)

    def test_provider_ignores_malformed_rich_snippet_sections(self) -> None:
        provider = self._make_provider(["dummy_key"])
        long_enough_snippet = "已有摘要，足够避免补抓。 " * 16

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Malformed rich snippet",
                        "link": "https://example.com/malformed-rich-snippet",
                        "snippet": long_enough_snippet,
                        "source": "Example",
                        "rich_snippet": {
                            "top": "unexpected string payload",
                            "bottom": ["unexpected", "list"],
                        },
                    }
                ]
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].snippet, long_enough_snippet.strip())
        mock_fetch.assert_not_called()

    def test_provider_ignores_non_list_rich_snippet_extensions(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Malformed extensions payload",
                        "link": "https://example.com/malformed-extensions",
                        "source": "Example",
                        "rich_snippet": {
                            "top": {
                                "extensions": True,
                            },
                            "bottom": {
                                "extensions": 1,
                                "detected_extensions": {
                                    "rating": 4.5,
                                },
                            },
                        },
                    }
                ]
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertIn("rating: 4.5", resp.results[0].snippet)
        mock_fetch.assert_not_called()

    def test_extract_rich_snippet_extensions_handles_non_dict_payloads(self) -> None:
        self.assertEqual(
            SerpAPISearchProvider._extract_rich_snippet_extensions(
                {"rich_snippet": "unexpected string payload"}
            ),
            [],
        )
        self.assertEqual(
            SerpAPISearchProvider._extract_rich_snippet_extensions(
                {
                    "rich_snippet": {
                        "top": "unexpected string payload",
                        "bottom": ["unexpected", "list"],
                    }
                }
            ),
            [],
        )

    def test_merge_organic_snippet_uses_normalized_length_for_ellipsis(self) -> None:
        merged = SerpAPISearchProvider._merge_organic_snippet_with_content(
            "原始摘要",
            "A" + ("\n" * (SerpAPISearchProvider._ORGANIC_FETCHED_PREVIEW_LENGTH + 20)),
        )

        self.assertFalse(merged.endswith("..."))

    def test_provider_fetches_only_one_top_short_snippet_candidate(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Need extra context",
                        "link": "https://example.com/need-extra-context",
                        "snippet": "摘要过短",
                        "source": "Example",
                    },
                    {
                        "title": "Second short result",
                        "link": "https://example.com/second-short",
                        "snippet": "也很短",
                        "source": "Example",
                    },
                    {
                        "title": "Third short result",
                        "link": "https://example.com/third-short",
                        "snippet": "还是很短",
                        "source": "Example",
                    },
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            return_value="网页正文补充信息 " * 40,
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 3)
        mock_fetch.assert_called_once_with(
            "https://example.com/need-extra-context",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertIn("【网页详情】", resp.results[0].snippet)
        self.assertEqual(resp.results[1].snippet, "也很短")
        self.assertEqual(resp.results[2].snippet, "还是很短")

    def test_provider_skips_asset_link_and_fetches_next_eligible_result(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "PDF attachment",
                        "link": "https://example.com/report.PDF?download=1",
                        "snippet": "附件摘要很短",
                        "source": "Example",
                    },
                    {
                        "title": "HTML article",
                        "link": "https://example.com/article",
                        "snippet": "正文摘要也短",
                        "source": "Example",
                    },
                    {
                        "title": "Third short result",
                        "link": "https://example.com/third-short",
                        "snippet": "还是很短",
                        "source": "Example",
                    },
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            return_value="网页正文补充信息 " * 40,
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 3)
        mock_fetch.assert_called_once_with(
            "https://example.com/article",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertEqual(resp.results[0].snippet, "附件摘要很短")
        self.assertIn("【网页详情】", resp.results[1].snippet)
        self.assertEqual(resp.results[2].snippet, "还是很短")

    def test_provider_skips_query_encoded_attachment_and_fetches_next_result(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Attachment behind download endpoint",
                        "link": "https://example.com/download?file=report.pdf",
                        "snippet": "附件摘要很短",
                        "source": "Example",
                    },
                    {
                        "title": "HTML article",
                        "link": "https://example.com/article",
                        "snippet": "正文摘要也短",
                        "source": "Example",
                    },
                    {
                        "title": "Third short result",
                        "link": "https://example.com/third-short",
                        "snippet": "还是很短",
                        "source": "Example",
                    },
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            return_value="网页正文补充信息 " * 40,
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 3)
        mock_fetch.assert_called_once_with(
            "https://example.com/article",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertEqual(resp.results[0].snippet, "附件摘要很短")
        self.assertIn("【网页详情】", resp.results[1].snippet)
        self.assertEqual(resp.results[2].snippet, "还是很短")

    def test_provider_keeps_html_fetch_for_asset_like_query_param(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "HTML article with media param",
                        "link": "https://example.com/article?thumbnail=cover.jpg",
                        "snippet": "摘要过短",
                        "source": "Example",
                    },
                    {
                        "title": "Second short result",
                        "link": "https://example.com/second-short",
                        "snippet": "也很短",
                        "source": "Example",
                    },
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            return_value="网页正文补充信息 " * 40,
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=2)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 2)
        mock_fetch.assert_called_once_with(
            "https://example.com/article?thumbnail=cover.jpg",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertIn("【网页详情】", resp.results[0].snippet)
        self.assertEqual(resp.results[1].snippet, "也很短")

    def test_provider_skips_non_string_link_and_keeps_fetch_budget(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Malformed link result",
                        "link": {"href": "https://example.com/broken"},
                        "snippet": "摘要过短",
                        "source": "Example",
                    },
                    {
                        "title": "HTML article",
                        "link": "https://example.com/article",
                        "snippet": "正文摘要也短",
                        "source": "Example",
                    },
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            return_value="网页正文补充信息 " * 40,
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=2)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 2)
        mock_fetch.assert_called_once_with(
            "https://example.com/article",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertEqual(resp.results[0].snippet, "摘要过短")
        self.assertIn("【网页详情】", resp.results[1].snippet)

    def test_provider_fetch_failure_stays_fail_open_and_stops_after_budget(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {
                "organic_results": [
                    {
                        "title": "Slow result",
                        "link": "https://example.com/slow",
                        "snippet": "摘要过短",
                        "source": "Example",
                    },
                    {
                        "title": "Another short result",
                        "link": "https://example.com/another-short",
                        "snippet": "仍然很短",
                        "source": "Example",
                    },
                ]
            }
        ), patch(
            "src.search_service.fetch_url_content",
            side_effect=TimeoutError("slow site"),
        ) as mock_fetch:
            resp = provider.search("阿里巴巴 财报", max_results=2)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 2)
        mock_fetch.assert_called_once_with(
            "https://example.com/slow",
            timeout=SerpAPISearchProvider._ORGANIC_CONTENT_FETCH_TIMEOUT,
        )
        self.assertEqual(resp.results[0].snippet, "摘要过短")
        self.assertEqual(resp.results[1].snippet, "仍然很短")

    def test_response_error_is_reported_as_failure(self) -> None:
        provider = self._make_provider(["dummy_key"])

        with self._patch_serpapi(
            {"error": "Your account has run out of searches."}
        ):
            resp = provider.search("贵州茅台 最新消息", max_results=3)

        self.assertFalse(resp.success)
        self.assertEqual(resp.results, [])
        self.assertIn("run out of searches", resp.error_message or "")

    def test_exhausted_key_fails_over_to_next_key(self) -> None:
        provider = self._make_provider(["exhausted_key", "healthy_key"])

        with self._patch_keyed_serpapi(
            {
                "exhausted_key": {"error": "Your account has run out of searches."},
                "healthy_key": {
                    "organic_results": [
                        {
                            "title": "茅台发布季度报告",
                            "link": "https://example.com/moutai",
                            "snippet": "这是一段足够长的摘要。 " * 12,
                            "source": "Example",
                        }
                    ]
                },
            }
        ), patch("src.search_service.fetch_url_content") as mock_fetch:
            resp = provider.search("贵州茅台 最新消息", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].url, "https://example.com/moutai")
        self.assertEqual(
            [params["api_key"] for params in _KeyedFakeGoogleSearch.init_params],
            ["exhausted_key", "healthy_key"],
        )
        mock_fetch.assert_not_called()


class TestSerpAPIKeyUsageLimit(unittest.TestCase):
    """单 Key 用量达到软上限后应主动切换到下一个 Key，并跨进程累计。"""

    _SUCCESS_PAYLOAD = {
        "organic_results": [
            {
                "title": "茅台发布季度报告",
                "link": "https://example.com/moutai",
                "snippet": "这是一段足够长的摘要。 " * 12,
                "source": "Example",
            }
        ]
    }

    def setUp(self) -> None:
        self._usage_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._usage_dir.cleanup)
        self._usage_store_path = Path(self._usage_dir.name) / "serpapi_key_usage.json"

    def _make_provider(self, api_keys, **kwargs):
        return SerpAPISearchProvider(
            api_keys,
            key_usage_store_path=self._usage_store_path,
            **kwargs,
        )

    def _seed_usage(self, usage_by_key, month=None) -> None:
        payload = {
            "month": month or SerpAPISearchProvider._current_usage_month(),
            "usage": {
                SerpAPISearchProvider._usage_key_id(key): value
                for key, value in usage_by_key.items()
            },
        }
        self._usage_store_path.parent.mkdir(parents=True, exist_ok=True)
        self._usage_store_path.write_text(json.dumps(payload), encoding="utf-8")

    def _used_keys(self):
        return [params["api_key"] for params in _KeyedFakeGoogleSearch.init_params]

    def test_skips_key_that_reached_limit_in_previous_run(self) -> None:
        provider = self._make_provider(["key_1", "key_2"], key_usage_limit=2)

        with self._patch_keyed_serpapi(
            {"key_1": self._SUCCESS_PAYLOAD, "key_2": self._SUCCESS_PAYLOAD}
        ), patch("src.search_service.fetch_url_content"):
            for _ in range(3):
                self.assertTrue(provider.search("贵州茅台 最新消息", max_results=1).success)

        self.assertEqual(provider._key_usage["key_1"], 2)
        self.assertEqual(self._used_keys(), ["key_1", "key_2", "key_1"])

        # 模拟下一次运行（新进程）：用量应从本地文件恢复，并跳过已满的 key_1
        restarted = self._make_provider(["key_1", "key_2"], key_usage_limit=2)
        self.assertEqual(restarted._key_usage["key_1"], 2)

        with self._patch_keyed_serpapi(
            {"key_1": self._SUCCESS_PAYLOAD, "key_2": self._SUCCESS_PAYLOAD}
        ), patch("src.search_service.fetch_url_content"):
            self.assertTrue(restarted.search("贵州茅台 最新消息", max_results=1).success)

        self.assertEqual(self._used_keys(), ["key_2"])

    def test_ignores_usage_from_previous_month(self) -> None:
        self._seed_usage({"key_1": 500}, month="2000-01")

        provider = self._make_provider(["key_1", "key_2"], key_usage_limit=200)

        self.assertEqual(provider._key_usage["key_1"], 0)

    def test_keeps_searching_when_all_keys_reach_limit(self) -> None:
        self._seed_usage({"key_1": 200, "key_2": 250})
        provider = self._make_provider(["key_1", "key_2"], key_usage_limit=200)

        with self._patch_keyed_serpapi(
            {"key_1": self._SUCCESS_PAYLOAD, "key_2": self._SUCCESS_PAYLOAD}
        ), patch("src.search_service.fetch_url_content"):
            resp = provider.search("贵州茅台 最新消息", max_results=1)

        self.assertTrue(resp.success)
        # 所有 Key 都触顶时退回消耗最少的 Key，避免整条搜索链路直接失效
        self.assertEqual(self._used_keys(), ["key_1"])
        self.assertEqual(provider._key_usage["key_1"], 201)

    def test_zero_limit_disables_persistence(self) -> None:
        provider = self._make_provider(["key_1"], key_usage_limit=0)

        with self._patch_keyed_serpapi(
            {"key_1": self._SUCCESS_PAYLOAD}
        ), patch("src.search_service.fetch_url_content"):
            self.assertTrue(provider.search("贵州茅台 最新消息", max_results=1).success)

        self.assertFalse(self._usage_store_path.exists())

    def _patch_keyed_serpapi(self, responses_by_key):
        _KeyedFakeGoogleSearch.reset(responses_by_key)
        module = ModuleType("serpapi")
        module.GoogleSearch = _KeyedFakeGoogleSearch
        return patch.dict(sys.modules, {"serpapi": module})


if __name__ == "__main__":
    unittest.main()

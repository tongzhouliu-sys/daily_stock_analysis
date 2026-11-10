from src.utils.chat_title import EMPTY_TITLE, MAX_TITLE_LENGTH, summarize_chat_title


def test_summarizes_skill_and_target_into_compact_title():
    assert summarize_chat_title("用缠论分析贵州茅台最近走势，现在适合买入吗？") == "贵州茅台 · 缠论"
    assert summarize_chat_title("请帮我用波浪理论分析一下宁德时代未来三个月的走势") == "宁德时代 · 波浪理论"
    assert summarize_chat_title("分析比亚迪趋势") == "比亚迪 · 趋势"


def test_keeps_short_questions_readable_and_strips_polite_noise():
    assert summarize_chat_title("请简要分析 600519") == "简要分析 600519"
    assert summarize_chat_title("茅台现在适合买入吗？") == "茅台现在适合买入"
    assert summarize_chat_title("宁德时代现在适合建仓吗？") == "宁德时代现在适合建仓"


def test_condenses_long_questions_into_topic_titles():
    assert summarize_chat_title("麻烦你帮我分析一下贵州茅台现在的走势，还能不能买入？") == "贵州茅台 · 走势"
    assert summarize_chat_title("我想问一下宁德时代最近适合建仓吗，顺便看看风险") == "宁德时代 · 建仓"
    assert summarize_chat_title("帮我看看美股英伟达未来三个月的趋势和估值水平，最好能给出操作建议") == "美股英伟达 · 趋势"


def test_keeps_non_stock_requests_readable():
    assert summarize_chat_title("帮我写一份去哪儿网误机投诉信") == "写一份去哪儿网误机投诉信"


def test_falls_back_to_empty_title_and_caps_length():
    assert summarize_chat_title(None) == EMPTY_TITLE
    assert summarize_chat_title("   ") == EMPTY_TITLE
    assert len(summarize_chat_title("x" * 120)) <= MAX_TITLE_LENGTH
    long_question = "这是很长的问题" * 20
    assert len(summarize_chat_title(long_question)) <= MAX_TITLE_LENGTH


def test_does_not_cut_english_words_in_half():
    title = summarize_chat_title(
        "This is a very long question about the market that should be compacted somehow because it keeps going"
    )
    assert title.endswith("…")
    assert not title[:-1].endswith(" ")
    assert "que…" not in title

"""
Tests for backend.api.deepseek: real ThinkTagParser, _partial_suffix_len,
_merge_extra_params, and chat_with_tools_stream against a mocked httpx
streaming layer.

Unlike test_deepseek_think.py (which tests a simulated copy of the logic),
these tests exercise the actual production classes.
Usage: cd test && python -m pytest test_deepseek_parser.py -v
"""
import asyncio
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.api.deepseek import (
    DeepSeekClient,
    ThinkTagParser,
    ToolCall,
    _THINK_PARTIAL_PREFIXES,
    _merge_extra_params,
    _partial_suffix_len,
    STREAM_EVENT_THINKING_DELTA,
    STREAM_EVENT_CONTENT_DELTA,
    STREAM_EVENT_TOOL_CALLS,
    STREAM_EVENT_DONE,
)


def run_parser(chunks):
    """Feed chunks through the real ThinkTagParser and collect fragments."""
    p = ThinkTagParser()
    out = []
    for c in chunks:
        out.extend(p.feed(c))
    out.extend(p.flush())
    return out


def joined(fragments, kind):
    return "".join(t for k, t in fragments if k == kind)


def feed_one(chunk):
    """Feed a single chunk into a fresh parser and return its fragments.

    No flush() on purpose: this shows exactly what the parser considers safe to
    hand downstream *before* the stream ends, i.e. whether a tag prefix that was
    split across chunks got buffered instead of being streamed out as content.
    """
    p = ThinkTagParser()
    return list(p.feed(chunk))


# 标签残片黑名单：既有完整标签，也有被拆开的半截前缀。
# 半截前缀必须单独列出来——旧实现泄漏出来的恰恰是 "答案<THI" 这种被截断的残片，
# 只查完整的 "<think" 是抓不到的。
TAG_FRAGMENTS = ("<think", "<thi", "</think", "</thi")


def assert_no_tag_leak(text, where=""):
    """断言正文/思考文本里没有混进 <think> 标签或它的半截前缀。"""
    lowered = text.lower()
    for frag in TAG_FRAGMENTS:
        assert frag not in lowered, f"{where} 泄漏了标签残片 {frag!r}: {text!r}"


# ============================================================
# _partial_suffix_len
# ============================================================

class TestPartialSuffixLen:
    """T28 把签名从「单个前缀字符串」改成「前缀元组」，本类按新签名覆盖原有语义。"""

    def test_full_prefix_match(self):
        assert _partial_suffix_len("abc<think", _THINK_PARTIAL_PREFIXES, 7) == 6

    def test_partial_match(self):
        assert _partial_suffix_len("hello<thi", _THINK_PARTIAL_PREFIXES, 7) == 4

    def test_no_match(self):
        assert _partial_suffix_len("hello", _THINK_PARTIAL_PREFIXES, 7) == 0

    def test_empty_string(self):
        assert _partial_suffix_len("", _THINK_PARTIAL_PREFIXES, 7) == 0

    def test_shorter_than_prefix(self):
        assert _partial_suffix_len("<t", _THINK_PARTIAL_PREFIXES, 7) == 2

    def test_single_string_prefix_still_accepted(self):
        """向后兼容：in-tag 分支仍以单个字符串 '</think' 调用。"""
        assert _partial_suffix_len("abc</think", "</think", 7) == 7
        assert _partial_suffix_len("abc</thi", "</think", 7) == 5
        assert _partial_suffix_len("abc</thi", ("</think",), 7) == 5
        assert _partial_suffix_len("abc</thi", "</think", 7) == _partial_suffix_len(
            "abc</thi", ("</think",), 7
        )

    def test_max_len_caps_result(self):
        """max_len 决定能缓冲多长的残片：旧的 6 撑不住 '<think/'，必须用 7。"""
        assert _partial_suffix_len("x<think", _THINK_PARTIAL_PREFIXES, 6) == 6
        # max_len=6 时 '<think/' 的 '/' 匹配不上（旧实现的漏洞）
        assert _partial_suffix_len("x<think/", _THINK_PARTIAL_PREFIXES, 6) == 0
        assert _partial_suffix_len("x<think/", _THINK_PARTIAL_PREFIXES, 7) == 7


class TestPartialSuffixLenCaseInsensitive:
    """大小写不敏感：模型可能吐 <THINK>，正则带 IGNORECASE，前缀缓冲也必须跟上。

    防的回归：分片正好切在 "<THI" / "</THI" 处时，若比较区分大小写就会返回 0，
    标签前半截被当成正文下发，前端看到 "<THI" 这种残片。
    """

    @pytest.mark.parametrize("suffix,expected", [
        ("<THI", 4),
        ("<Thi", 4),
        ("<THINK", 6),
        ("<THINK/", 7),
        ("<THINK ", 7),
        ("</THI", 5),
        ("</THINK", 7),
    ])
    def test_uppercase_suffix_is_detected(self, suffix, expected):
        prefixes = _THINK_PARTIAL_PREFIXES if not suffix.startswith("</") else "</think"
        assert _partial_suffix_len("答案" + suffix, prefixes, 7) == expected

    def test_case_insensitive_mixed_text(self):
        assert _partial_suffix_len("Some Answer<ThInK", _THINK_PARTIAL_PREFIXES, 7) == 6
        assert _partial_suffix_len("<th", _THINK_PARTIAL_PREFIXES, 7) == 3

    def test_longest_prefix_wins_for_self_closing_split(self):
        """'<think/' 必须返回 7 而不是 6，否则 '/' 会被当作正文下发。"""
        assert _partial_suffix_len("你好<think/", _THINK_PARTIAL_PREFIXES, 7) == 7
        assert _partial_suffix_len("a<think ", _THINK_PARTIAL_PREFIXES, 7) == 7


# ============================================================
# ThinkTagParser (real implementation)
# ============================================================

class TestThinkTagParserReal:
    def test_plain_content(self):
        frags = run_parser(["hello world"])
        assert joined(frags, "content") == "hello world"
        assert joined(frags, "think") == ""

    def test_complete_think_block_single_chunk(self):
        frags = run_parser(["<think>reasoning</think>answer"])
        assert joined(frags, "think") == "reasoning"
        assert joined(frags, "content") == "answer"

    def test_open_tag_split_across_chunks(self):
        frags = run_parser(["<thi", "nk>thinking text</think>real answer"])
        assert joined(frags, "think") == "thinking text"
        assert joined(frags, "content") == "real answer"

    def test_close_tag_split_across_chunks(self):
        frags = run_parser(["<think>reasoning</thi", "nk>answer"])
        assert joined(frags, "think") == "reasoning"
        assert joined(frags, "content") == "answer"

    def test_partial_close_tag_suffix_held_back(self):
        # "</th" at buffer end must not be emitted as thinking yet
        p = ThinkTagParser()
        out1 = list(p.feed("<think>abc</th"))
        assert joined(out1, "think") == "abc"
        out2 = list(p.feed("ink>done"))
        assert joined(out2, "content") == "done"

    def test_partial_open_tag_suffix_held_back(self):
        p = ThinkTagParser()
        out1 = list(p.feed("text<th"))
        assert joined(out1, "content") == "text"
        out2 = list(p.feed("ink>thinking</think>rest"))
        assert joined(out2, "think") == "thinking"
        assert joined(out2, "content") == "rest"

    def test_self_closing_tag_skipped(self):
        frags = run_parser(["before <think/> after"])
        assert joined(frags, "content") == "before  after"
        assert joined(frags, "think") == ""

    def test_think_tag_with_attributes(self):
        frags = run_parser(['<think process="reasoning">inner</think>outer'])
        assert joined(frags, "think") == "inner"
        assert joined(frags, "content") == "outer"

    def test_multiple_think_blocks(self):
        frags = run_parser(["<think>one</think>mid<think>two</think>final"])
        assert joined(frags, "think") == "onetwo"
        assert joined(frags, "content") == "midfinal"

    def test_unclosed_think_flushed_as_thinking(self):
        frags = run_parser(["<think>unfinished"])
        assert joined(frags, "think") == "unfinished"
        assert joined(frags, "content") == ""

    def test_unflushed_plain_content_flushed_as_content(self):
        p = ThinkTagParser()
        out = list(p.feed("abc<th"))  # partial tag held
        out += list(p.flush())  # stream ends without completing tag
        # The held-back "<th" is emitted as content on flush
        assert joined(out, "content") == "abc<th"

    def test_chinese_content(self):
        frags = run_parser(["<think>我在思考</think>这是答案"])
        assert joined(frags, "think") == "我在思考"
        assert joined(frags, "content") == "这是答案"

    def test_empty_feed(self):
        frags = run_parser([])
        assert frags == []


class TestThinkTagPrefixBuffered:
    """T28 回归：以 '<think/'、'<think '、'<THI...' 结尾的分片必须被缓冲，不能当正文下发。

    打标签的模型会把 '<think/>' 这类自闭合标签和正文挤在同一个 content delta 里，
    网络分片又可能恰好切在 '/' 或空格之后。旧实现只按 '<think'（6 字符、区分大小写）
    做部分匹配，于是 '<think/' 的 '/' 、'<THI' 的大小写变体都会提前漏进正文。
    """

    def test_slash_split_after_think_buffered(self):
        """防回归：分片切在 '<think/' 之后时，'<' 不能被当正文吐出去。"""
        out = feed_one("你好<think/")
        assert joined(out, "content") == "你好"
        assert joined(out, "think") == ""
        assert_no_tag_leak(joined(out, "content"), "slash 分片")

    def test_space_split_after_think_buffered(self):
        """防回归：分片切在 '<think ' 之后（自带空格的属性/自闭合写法）。"""
        out = feed_one("前文<think ")
        assert joined(out, "content") == "前文"
        assert_no_tag_leak(joined(out, "content"), "space 分片")

    def test_uppercase_split_buffered(self):
        """防回归：'<THI' 这类大写残片必须缓冲（正则带 IGNORECASE，缓冲也必须一致）。"""
        out = feed_one("答案<THI")
        assert joined(out, "content") == "答案"
        assert_no_tag_leak(joined(out, "content"), "大写分片")

    def test_uppercase_close_split_buffered_inside_tag(self):
        """防回归：think 块内以 '</THI' 结尾的增量不能被当思考文本提前发出。"""
        p = ThinkTagParser()
        out = list(p.feed("<THINK>推理中</THI"))
        assert joined(out, "think") == "推理中"
        out += list(p.feed("NK>正文"))
        assert joined(out, "content") == "正文"

    def test_bare_angle_bracket_held_then_released(self):
        """普通文本以 '<' 结尾时被暂存，但 flush 时必须原样吐回，不能丢字。"""
        p = ThinkTagParser()
        out = list(p.feed("价格 <"))
        assert joined(out, "content") == "价格 "
        out += list(p.flush())
        assert joined(out, "content") == "价格 <"


class TestThinkTagRealStreamingScenarios:
    """真实流式分片序列：标签文本绝不能泄漏进 content。"""

    def test_self_closing_tag_split_across_deltas(self):
        """分片 ['你好<think/', '>世界'] → content 恰好是 '你好世界'，无 '<think/>' 残留。"""
        frags = run_parser(["你好<think/", ">世界"])
        content = joined(frags, "content")
        think = joined(frags, "think")
        assert content == "你好世界"
        assert think == ""
        assert_no_tag_leak(content, "自闭合标签跨分片")

    def test_uppercase_think_block_split_across_deltas(self):
        """分片 ['答案<THI', 'NK>思考</THINK>正文'] → 思考归 think，正文只剩 '答案正文'。"""
        frags = run_parser(["答案<THI", "NK>思考</THINK>正文"])
        content = joined(frags, "content")
        think = joined(frags, "think")
        assert content == "答案正文"
        assert think == "思考"
        assert_no_tag_leak(content, "大写 think 块")
        assert_no_tag_leak(think, "大写 think 块")

    def test_self_closing_with_space_split_across_deltas(self):
        """分片 ['前文<think ', '/>后文'] → '<think />' 整体被吃掉。"""
        frags = run_parser(["前文<think ", "/>后文"])
        assert joined(frags, "content") == "前文后文"
        assert joined(frags, "think") == ""

    def test_open_tag_split_at_angle_bracket(self):
        """分片 ['文字<think', '>想</think>答'] 是原有能力，顺带一起锁住。"""
        frags = run_parser(["文字<think", ">想</think>答"])
        assert joined(frags, "content") == "文字答"
        assert joined(frags, "think") == "想"

    def test_partial_tag_prefix_split_at_every_offset(self):
        """把 '你好<THINK>思考</THINK>正文' 在所有可能位置切成两半，结果都必须一致。

        覆盖 1..len-1 全部分片点，任何一处提前下发标签残片都会让断言失败。
        """
        full = "你好<THINK>思考</THINK>正文"
        for cut in range(1, len(full)):
            frags = run_parser([full[:cut], full[cut:]])
            content = joined(frags, "content")
            think = joined(frags, "think")
            assert content == "你好正文", f"cut={cut} content={content!r}"
            assert think == "思考", f"cut={cut} think={think!r}"
            assert_no_tag_leak(content, f"cut={cut}")

    def test_normal_text_with_angle_brackets_not_harmed(self):
        """正常含 '<' '>' 的句子不能被误伤（既不丢字也不吞字符）。"""
        for text in [
            "价格 < 100 元，且 3 > 2",
            "a<b 且 c>d",
            "不等式 x<y 成立",
            "HTML 片段 <div> 与 <span>",
            "数学符号 ≤ ≥ ≠ 也照常输出",
        ]:
            frags = run_parser([text])
            assert joined(frags, "content") == text, f"正文被改动: {text!r}"
            assert joined(frags, "think") == ""

    def test_normal_text_with_angle_brackets_split_across_deltas(self):
        """含 '<' 的普通句子被任意切分后，拼接结果仍与原句完全一致。"""
        text = "价格 < 100 元，a<b 且 <div> 照常"
        for cut in range(1, len(text)):
            frags = run_parser([text[:cut], text[cut:]])
            assert joined(frags, "content") == text, f"cut={cut} 丢了字"
            assert joined(frags, "think") == ""


# ============================================================
# chat_with_tools_stream (mocked httpx)
# ============================================================

class FakeStreamResponse:
    def __init__(self, lines, status_code=200, body=b""):
        self._lines = lines
        self.status_code = status_code
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._body


class FakeHttpxClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None):
        return self._response


def sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False)


def stream_events(lines, status_code=200, body=b"", messages=None):
    resp = FakeStreamResponse(lines, status_code=status_code, body=body)
    client = DeepSeekClient(api_key="test-key", base_url="http://api.test", model="test-model")
    with patch("httpx.AsyncClient", lambda **kw: FakeHttpxClient(resp)):
        async def collect():
            return [e async for e in client.chat_with_tools_stream(
                messages or [{"role": "user", "content": "hi"}]
            )]
        return asyncio.run(collect())


class TestChatWithToolsStream:
    def test_content_streaming_and_done(self):
        events = stream_events([
            sse({"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"content": "世界"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ])
        deltas = [e for e in events if e["type"] == STREAM_EVENT_CONTENT_DELTA]
        assert "".join(d["delta"] for d in deltas) == "你好世界"
        done = [e for e in events if e["type"] == STREAM_EVENT_DONE]
        assert len(done) == 1
        assert done[0]["content"] == "你好世界"
        assert done[0]["finish_reason"] == "stop"

    def test_reasoning_content_streamed(self):
        events = stream_events([
            sse({"choices": [{"delta": {"reasoning_content": "首先分析问题"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"content": "答案"}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ])
        thinking = [e for e in events if e["type"] == STREAM_EVENT_THINKING_DELTA]
        assert "".join(t["content"] for t in thinking) == "首先分析问题"
        done = [e for e in events if e["type"] == STREAM_EVENT_DONE][0]
        assert done["reasoning_content"] == "首先分析问题"

    def test_think_tags_in_content_rerouted(self):
        """<think> tags embedded in content field become thinking deltas."""
        events = stream_events([
            sse({"choices": [{"delta": {"content": "<think>内部推理</think>外部回答"}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ])
        thinking = [e for e in events if e["type"] == STREAM_EVENT_THINKING_DELTA]
        content = [e for e in events if e["type"] == STREAM_EVENT_CONTENT_DELTA]
        assert "".join(t["content"] for t in thinking) == "内部推理"
        assert "".join(c["delta"] for c in content) == "外部回答"
        done = [e for e in events if e["type"] == STREAM_EVENT_DONE][0]
        assert done["content"] == "外部回答"
        assert done["reasoning_content"] == "内部推理"

    def test_tool_calls_accumulated_across_deltas(self):
        events = stream_events([
            sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "web_search", "arguments": '{"que'}}
            ]}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'ry":"银灰"}'}}
            ]}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]",
        ])
        tc_events = [e for e in events if e["type"] == STREAM_EVENT_TOOL_CALLS]
        assert len(tc_events) == 1
        tool_calls = tc_events[0]["tool_calls"]
        assert len(tool_calls) == 1
        assert isinstance(tool_calls[0], ToolCall)
        assert tool_calls[0].id == "call_1"
        assert tool_calls[0].name == "web_search"
        assert tool_calls[0].arguments == '{"query":"银灰"}'

    def test_multiple_parallel_tool_calls_ordered_by_index(self):
        events = stream_events([
            sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c0", "function": {"name": "rag", "arguments": "{}"}},
                {"index": 1, "id": "c1", "function": {"name": "web", "arguments": "{}"}},
            ]}, "finish_reason": None}]}),
            "data: [DONE]",
        ])
        tool_calls = [e for e in events if e["type"] == STREAM_EVENT_TOOL_CALLS][0]["tool_calls"]
        assert [tc.id for tc in tool_calls] == ["c0", "c1"]

    def test_http_error_raises_with_message(self):
        resp = FakeStreamResponse([], status_code=401,
                                  body=b'{"error": {"message": "invalid api key"}}')
        client = DeepSeekClient(api_key="bad", base_url="http://api.test", model="m")

        async def collect():
            with patch("httpx.AsyncClient", lambda **kw: FakeHttpxClient(resp)):
                return [e async for e in client.chat_with_tools_stream([{"role": "user", "content": "hi"}])]

        with pytest.raises(Exception, match="401 Error: invalid api key"):
            asyncio.run(collect())

    def test_http_error_non_json_body(self):
        resp = FakeStreamResponse([], status_code=500, body=b"internal server error")
        client = DeepSeekClient(api_key="k", base_url="http://api.test", model="m")

        async def collect():
            with patch("httpx.AsyncClient", lambda **kw: FakeHttpxClient(resp)):
                return [e async for e in client.chat_with_tools_stream([{"role": "user", "content": "hi"}])]

        with pytest.raises(Exception, match="500 Error"):
            asyncio.run(collect())

    def test_malformed_sse_lines_skipped(self):
        events = stream_events([
            "data: {not valid json",
            "",
            ": comment line",
            sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ])
        done = [e for e in events if e["type"] == STREAM_EVENT_DONE][0]
        assert done["content"] == "ok"

    def test_chunk_without_choices_skipped(self):
        events = stream_events([
            sse({"usage": {"total_tokens": 10}}),
            sse({"choices": []}),
            sse({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ])
        done = [e for e in events if e["type"] == STREAM_EVENT_DONE][0]
        assert done["content"] == "x"

    def test_large_content_split_into_stream_chunks(self):
        """Content longer than STREAM_CHUNK_SIZE must be split for smooth rendering."""
        long_text = "a" * 100
        events = stream_events([
            sse({"choices": [{"delta": {"content": long_text}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ])
        deltas = [e for e in events if e["type"] == STREAM_EVENT_CONTENT_DELTA]
        assert len(deltas) > 1
        assert all(len(d["delta"]) <= 8 for d in deltas)
        assert "".join(d["delta"] for d in deltas) == long_text


class TestDeepSeekClientInit:
    def test_missing_api_key_raises(self):
        with patch("backend.config.DEEPSEEK_API_KEY", ""):
            with pytest.raises(ValueError, match="API key"):
                DeepSeekClient(api_key=None)

    def test_explicit_params(self):
        client = DeepSeekClient(api_key="k", base_url="http://x", model="m")
        assert client.api_key == "k"
        assert client.base_url == "http://x"
        assert client.model == "m"
        assert client.disable_thinking is False


# ============================================================
# SSE 端到端：标签残片不得进入 content_delta / done.content
# ============================================================

def stream_content_deltas(chunks):
    """把若干 content 分片包装成 SSE 行，跑完整 chat_with_tools_stream。

    返回 (content_deltas, thinking_deltas, done_event)，其中分片顺序与真实服务端
    逐 delta 下发一致。
    """
    lines = [
        sse({"choices": [{"delta": {"content": c}, "finish_reason": None}]})
        for c in chunks
    ]
    lines.append(sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    lines.append("data: [DONE]")
    events = stream_events(lines)
    content = [e["delta"] for e in events if e["type"] == STREAM_EVENT_CONTENT_DELTA]
    thinking = [e["content"] for e in events if e["type"] == STREAM_EVENT_THINKING_DELTA]
    done = [e for e in events if e["type"] == STREAM_EVENT_DONE][0]
    return content, thinking, done


class TestStreamNoTagLeakEndToEnd:
    """走完整 SSE 解析链路（delta → ThinkTagParser → 事件 → done 汇总）验证无泄漏。"""

    def test_self_closing_tag_split_between_deltas(self):
        """分片 ['你好<think/', '>世界']：content_delta 必须拼成 '你好世界'。"""
        content, thinking, done = stream_content_deltas(["你好<think/", ">世界"])
        assert "".join(content) == "你好世界"
        assert done["content"] == "你好世界"
        assert thinking == []
        assert done["reasoning_content"] == ""
        assert_no_tag_leak(done["content"], "done.content")

    def test_uppercase_think_block_split_between_deltas(self):
        """分片 ['答案<THI', 'NK>思考</THINK>正文']：正文只剩 '答案正文'，思考归 reasoning。"""
        content, thinking, done = stream_content_deltas(["答案<THI", "NK>思考</THINK>正文"])
        assert "".join(content) == "答案正文"
        assert done["content"] == "答案正文"
        assert "".join(thinking) == "思考"
        assert done["reasoning_content"] == "思考"
        assert_no_tag_leak(done["content"], "done.content")
        assert_no_tag_leak(done["reasoning_content"], "done.reasoning_content")

    def test_self_closing_tag_with_space_split_between_deltas(self):
        """分片 ['前文<think ', '/>后文']：'<think />' 被整体吞掉，不留残片。"""
        content, thinking, done = stream_content_deltas(["前文<think ", "/>后文"])
        assert done["content"] == "前文后文"
        assert thinking == []
        assert_no_tag_leak(done["content"], "done.content")

    def test_normal_text_not_harmed_by_tag_buffering(self):
        """含 '<' 的普通句子经 SSE 全链路后必须一字不差，也不进 reasoning。"""
        text = "价格 < 100 元，a<b"
        content, thinking, done = stream_content_deltas(["价格 < 100 元", "，a<b"])
        assert "".join(content) == text
        assert done["content"] == text
        assert thinking == []
        assert done["reasoning_content"] == ""

    def test_tool_call_round_does_not_leak_tags(self):
        """带 tool_calls 的轮次同样不能把标签写进 content / reasoning。"""
        events = stream_events([
            sse({"choices": [{"delta": {"content": "答案<THI"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"content": "NK>思考</THINK>正文"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c0", "function": {"name": "rag", "arguments": "{}"}}
            ]}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]",
        ])
        evt = [e for e in events if e["type"] == STREAM_EVENT_TOOL_CALLS][0]
        assert evt["content"] == "答案正文"
        assert evt["reasoning_content"] == "思考"
        assert len(evt["tool_calls"]) == 1
        assert_no_tag_leak(evt["content"], "tool_calls.content")


# ============================================================
# _merge_extra_params + 真实请求 payload
# ============================================================

class TestMergeExtraParams:
    """防回归：调用方 kwargs 不得覆盖请求协议字段。

    修复前是 `payload.update(kwargs)`：调用方传 stream=False 就会让
    chat_with_tools_stream 用「非流式」flag 走 client.stream()，aiter_lines()
    看不到任何 'data: ' 行，于是静默返回空内容（无异常、无日志）。
    """

    def test_conflicting_stream_not_applied(self):
        payload = {"model": "m", "stream": True, "messages": []}
        _merge_extra_params(payload, {"stream": False})
        assert payload["stream"] is True

    def test_conflicting_protocol_fields_not_applied(self):
        payload = {"model": "m", "messages": [1], "temperature": 0.3, "stream": True}
        _merge_extra_params(payload, {
            "model": "hacked",
            "messages": [],
            "temperature": 9.9,
            "stream": False,
        })
        assert payload["model"] == "m"
        assert payload["messages"] == [1]
        assert payload["temperature"] == 0.3
        assert payload["stream"] is True

    def test_conflicting_thinking_not_applied(self):
        """disable_thinking 显式写入的 thinking 字段不能被 kwargs 顶掉。"""
        payload = {"stream": True, "thinking": {"type": "disabled"}}
        _merge_extra_params(payload, {"thinking": {"type": "enabled"}})
        assert payload["thinking"] == {"type": "disabled"}

    def test_non_conflicting_kwargs_passthrough(self):
        """非冲突键（如 max_tokens / top_p / extra 自定义字段）仍然透传。"""
        payload = {"model": "m", "stream": True}
        _merge_extra_params(payload, {"max_tokens": 2048, "top_p": 0.9, "custom_flag": True})
        assert payload["max_tokens"] == 2048
        assert payload["top_p"] == 0.9
        assert payload["custom_flag"] is True
        assert payload["model"] == "m"
        assert payload["stream"] is True

    def test_conflicting_tools_not_applied(self):
        """工具列表属于协议字段：kwargs 里的 tools 不能把已注册的工具清空。"""
        tools = [{"type": "function", "function": {"name": "rag", "parameters": {}}}]
        payload = {"stream": True, "tools": tools}
        _merge_extra_params(payload, {"tools": []})
        assert payload["tools"] == tools

    def test_mixed_conflict_and_passthrough(self):
        """冲突键被丢弃的同时，同一次调用里的非冲突键必须保留。"""
        payload = {"stream": True, "temperature": 0.3}
        _merge_extra_params(payload, {"stream": False, "max_tokens": 8192})
        assert payload["stream"] is True
        assert payload["temperature"] == 0.3
        assert payload["max_tokens"] == 8192

    def test_no_kwargs_is_noop(self):
        payload = {"stream": True}
        _merge_extra_params(payload, {})
        assert payload == {"stream": True}

    def test_mutates_payload_in_place(self):
        """函数返回 None，调用方依赖原地修改，不能改成返回新 dict。"""
        payload = {"stream": True}
        assert _merge_extra_params(payload, {"max_tokens": 1}) is None
        assert payload["max_tokens"] == 1

    def test_conflict_is_logged(self, caplog):
        """冲突参数不能被静默吞掉，必须有 warning 便于排查；透传键不应出现在告警里。"""
        import logging as _logging
        payload = {"stream": True}
        with caplog.at_level(_logging.WARNING, logger="backend.api.deepseek"):
            _merge_extra_params(payload, {"stream": False, "max_tokens": 5})
        messages = " | ".join(rec.getMessage() for rec in caplog.records)
        assert "stream" in messages
        assert "max_tokens" not in messages


class FakeCompletionResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    @property
    def text(self):
        return json.dumps(self._body)

    def json(self):
        return self._body


class RecordingStreamCtx:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class RecordingHttpxClient:
    """记录真实下发 payload 的 httpx.AsyncClient 替身（不替换被测逻辑）。

    既能当工厂（``httpx.AsyncClient(**kw)``）又能当异步上下文管理器，
    并把每次请求的 json body 收进 ``self.payloads``。
    """

    def __init__(self, lines=None):
        self.payloads = []
        self._lines = lines if lines is not None else [
            sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ]

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None):
        self.payloads.append(json)
        return RecordingStreamCtx(self._lines)

    async def post(self, url, headers=None, json=None):
        self.payloads.append(json)
        return FakeCompletionResponse({"choices": [{"message": {"content": "摘要"}}]})

    @property
    def last_payload(self):
        assert self.payloads, "没有任何请求 payload 被记录"
        return self.payloads[-1]


def capture_stream_payload(call_kwargs=None, disable_thinking=False, tools=None):
    """跑 chat_with_tools_stream（httpx 被打桩），返回 (events, payload)。"""
    fake = RecordingHttpxClient()
    client = DeepSeekClient(api_key="k", base_url="http://api.test", model="m")
    client.disable_thinking = disable_thinking
    with patch("httpx.AsyncClient", fake):
        async def collect():
            return [e async for e in client.chat_with_tools_stream(
                [{"role": "user", "content": "hi"}],
                tools=tools,
                **(call_kwargs or {}),
            )]
        events = asyncio.run(collect())
    return events, fake.last_payload


def capture_completion_payload(call_kwargs=None, disable_thinking=False):
    """跑 chat_completion（httpx 被打桩），返回 (text, payload)。"""
    fake = RecordingHttpxClient()
    client = DeepSeekClient(api_key="k", base_url="http://api.test", model="m")
    client.disable_thinking = disable_thinking
    with patch("httpx.AsyncClient", fake):
        text = asyncio.run(client.chat_completion(
            [{"role": "user", "content": "hi"}],
            **(call_kwargs or {}),
        ))
    return text, fake.last_payload


class TestStreamRequestPayload:
    """真实 payload 断言：协议字段永远是我们写的那份。"""

    def test_default_payload_streams(self):
        _, payload = capture_stream_payload()
        assert payload["stream"] is True
        assert payload["model"] == "m"
        assert payload["messages"] == [{"role": "user", "content": "hi"}]

    def test_stream_false_kwarg_cannot_disable_streaming(self):
        """核心回归：调用方传 stream=False 时 payload['stream'] 必须仍为 True。

        旧实现 payload.update(kwargs) 会让 stream 变成 False，而请求仍走
        client.stream()，导致 aiter_lines() 收不到 data 行、静默返回空内容。
        """
        events, payload = capture_stream_payload({"stream": False})
        assert payload["stream"] is True
        done = [e for e in events if e["type"] == STREAM_EVENT_DONE][0]
        assert done["content"] == "ok"  # 没有静默空内容

    def test_thinking_kwarg_cannot_override_disabled_thinking(self):
        _, payload = capture_stream_payload({"thinking": {"type": "enabled"}}, disable_thinking=True)
        assert payload["thinking"] == {"type": "disabled"}

    def test_thinking_kwarg_passes_when_not_set_by_client(self):
        """客户端没设 disable_thinking 时，thinking 不是协议字段，应当透传。"""
        _, payload = capture_stream_payload({"thinking": {"type": "enabled"}})
        assert payload["thinking"] == {"type": "enabled"}

    def test_max_tokens_kwarg_reaches_payload(self):
        """非冲突键必须真的发到服务端（否则限流配置形同虚设）。"""
        _, payload = capture_stream_payload({"max_tokens": 1234})
        assert payload["max_tokens"] == 1234
        assert payload["stream"] is True

    def test_tools_and_conflicting_kwargs_together(self):
        """带工具的真实调用：stream 不可被顶掉，工具列表与 max_tokens 都要在 payload 里。"""
        tools = [{"type": "function", "function": {"name": "rag", "parameters": {}}}]
        _, payload = capture_stream_payload({"stream": False, "max_tokens": 64}, tools=tools)
        assert payload["stream"] is True
        assert payload["tools"] == tools
        assert payload["max_tokens"] == 64

    def test_completion_payload_never_streams(self):
        """chat_completion 是非流式接口：payload['stream'] 必须为 False。"""
        text, payload = capture_completion_payload()
        assert text == "摘要"
        assert payload["stream"] is False

    def test_completion_stream_true_kwarg_cannot_flip(self):
        _, payload = capture_completion_payload({"stream": True})
        assert payload["stream"] is False

    def test_completion_non_conflicting_kwargs_passthrough(self):
        _, payload = capture_completion_payload({"max_tokens": 256})
        assert payload["max_tokens"] == 256
        assert payload["stream"] is False

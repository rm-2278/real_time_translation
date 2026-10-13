"""Tests for modules split out of pipeline.py / llm_translator.py / experiments."""

import asyncio

import pytest

from real_time_translation.experiments.media import parse_time
from real_time_translation.experiments.results_io import (
    ensure_csv_header,
    last_cumulative_text,
    slugify,
)
from real_time_translation.experiments.stats import mean, median, pearson_r, stdev
from real_time_translation.fragment_queue import FragmentQueue, QueuedTranscription
from real_time_translation.translation.prompts import (
    MONOTONIC_STYLE_BLOCK,
    language_name,
    length_budget_block,
    prior_translation_block,
)


def _item(text: str) -> QueuedTranscription:
    return QueuedTranscription(original=None, text_for_translation=text, context=[])  # type: ignore[arg-type]


def test_fragment_queue_drops_oldest_when_full():
    q = FragmentQueue(maxsize=2)
    for t in "abc":
        q.put(_item(t))
    assert len(q) == 2
    assert q.peek().text_for_translation == "b"


def test_fragment_queue_peek_pop_keeps_order():
    q = FragmentQueue(maxsize=5)
    q.put(_item("a"))
    q.put(_item("b"))
    assert q.peek().text_for_translation == "a"
    assert q.pop_peeked().text_for_translation == "a"
    assert asyncio.run(q.get()).text_for_translation == "b"


def test_prompt_blocks():
    assert "FIXED" in prior_translation_block("x", delta_only=True)
    assert "FIXED" not in prior_translation_block("x", delta_only=False)
    assert "40 characters" in length_budget_block(40)
    assert MONOTONIC_STYLE_BLOCK.startswith("This is an IN-PROGRESS")
    assert language_name("ja") == "Japanese"
    assert language_name("xx") == "xx"


def test_stats_empty_is_none():
    assert mean([]) is None and median([]) is None and stdev([1.0]) is None
    assert median([1.0, 3.0, 2.0, 10.0]) == 2.5
    assert pearson_r([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson_r([1, 1, 1], [1, 2, 3]) is None


def test_parse_time_and_slugify():
    assert parse_time("10:00") == 600
    assert parse_time("1:10:00") == 4200
    assert parse_time("600.5") == 600.5
    with pytest.raises(ValueError):
        parse_time("a:b")
    assert slugify(" Hello, World! ") == "hello_world"
    assert slugify("!!!") == "experiment"
    assert last_cumulative_text(["a", "ab"]) == "ab"
    assert last_cumulative_text([]) == ""


def test_ensure_csv_header_migrates_in_place(tmp_path):
    p = tmp_path / "results.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    ensure_csv_header(p, ["a", "b", "c"])
    assert p.read_text(encoding="utf-8").splitlines() == ["a,b,c", "1,2,"]

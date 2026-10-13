"""Regression tests for the 2026-09-24 human review of the LLM2024_8_part1 captions.

See experiments/20260924_llm2024_8_part1_human_review.md for the issue list.
"""

from __future__ import annotations

from types import SimpleNamespace

from real_time_translation.captions.subtitle_formatter import (
    _merge_tiny_utterances,
    _normalize_spaces,
    _RawUtterance,
    _split_chunks,
    _wrap_lines,
)
from real_time_translation.pipeline import TranslationPipeline
from real_time_translation.transcription.dangling import split_dangling_tail
from real_time_translation.transcription.deepgram_client import (
    DeepgramTranscriber,
    TranscriptionResult,
)
from real_time_translation.translation.dictionary import TermDictionary

# --- Deepgram re-emission ("重複した表示") ---------------------------------


def _results_msg(start, words, *, is_final):
    """A fake Deepgram `Results` message; `words` is [(text, start, end)]."""
    end = words[-1][2]
    return SimpleNamespace(
        type="Results",
        start=start,
        duration=end - start,
        is_final=is_final,
        channel=SimpleNamespace(
            alternatives=[
                SimpleNamespace(
                    transcript=" ".join(w for w, _s, _e in words),
                    confidence=0.95,
                    words=[
                        SimpleNamespace(word=w, punctuated_word=w, start=s, end=e)
                        for w, s, e in words
                    ],
                )
            ]
        ),
    )


def _drain(transcriber) -> list[TranscriptionResult]:
    out = []
    while not transcriber._result_queue.empty():
        out.append(transcriber._result_queue.get_nowait())
    return [r for r in out if r.is_final]


GEMMA = [
    ("Gemma", 649.8, 650.2),
    ("3D,", 650.3, 650.8),
    ("Gemma", 651.0, 651.4),
    ("four", 651.5, 651.8),
    ("stuffs.", 651.9, 652.4),
]


def test_utterance_end_then_late_final_is_not_re_emitted():
    # Exact shape of the 10:50 duplicate in the 2026-09-18 run: UtteranceEnd
    # flushed the pending interim, then Deepgram's own is_final for the
    # same audio arrived and was emitted again as a new utterance.
    t = DeepgramTranscriber(api_key="x")
    okay = [("Okay.", 653.5, 654.0)]
    t._handle_message(_results_msg(649.8, GEMMA + okay, is_final=False))
    t._handle_message(SimpleNamespace(type="UtteranceEnd"))
    t._handle_message(_results_msg(649.8, GEMMA, is_final=True))
    finals = _drain(t)
    assert [r.text for r in finals] == ["Gemma 3D, Gemma four stuffs. Okay."]


def test_resegmented_trailing_word_is_trimmed():
    t = DeepgramTranscriber(api_key="x")
    t._handle_message(
        _results_msg(649.8, GEMMA + [("Okay.", 653.5, 654.0)], is_final=False)
    )
    t._handle_message(SimpleNamespace(type="UtteranceEnd"))
    # Deepgram moves "Okay." into the next segment.
    t._handle_message(
        _results_msg(
            652.9,
            [("Okay.", 653.5, 654.0), ("That's", 654.2, 654.4), ("my", 654.5, 654.8)],
            is_final=True,
        )
    )
    finals = _drain(t)
    assert [r.text for r in finals][-1] == "That's my"
    assert finals[-1].start_time == 654.2


def test_non_overlapping_finals_unchanged():
    t = DeepgramTranscriber(api_key="x")
    t._handle_message(_results_msg(649.8, GEMMA, is_final=True))
    t._handle_message(_results_msg(652.9, [("Okay.", 653.5, 654.0)], is_final=True))
    assert [r.text for r in _drain(t)] == [
        "Gemma 3D, Gemma four stuffs.",
        "Okay.",
    ]


# --- dangling connectives ("…。そして、") ------------------------------------


def testsplit_dangling_tail():
    assert split_dangling_tail("the language model and also") == (
        "the language model",
        "and also",
    )
    assert split_dangling_tail("I would like to introduce my") == (
        "I would like to introduce",
        "my",
    )
    assert split_dangling_tail("Gemma four stuffs. Okay.") == (
        "Gemma four stuffs. Okay.",
        "",
    )
    assert split_dangling_tail("And") == ("", "And")


def _final(text, utterance_id, *, end=True):
    return TranscriptionResult(
        text=text,
        is_final=True,
        confidence=0.9,
        start_time=0.0,
        end_time=1.0,
        utterance_id=utterance_id,
        is_utterance_end=end,
    )


def test_dangling_carry_moves_tail_to_next_chunk():
    state = SimpleNamespace(_dangling_carry="", _open_utterance_id=None)
    carry = TranslationPipeline._apply_dangling_carry
    assert carry(state, _final("x", 1), "introduced, I already. And") == (
        "introduced, I already."
    )
    assert carry(state, _final("x", 2), "And") == ""
    assert carry(state, _final("x", 3), "also we did research.") == (
        "And And also we did research."
    )


def test_dangling_carry_never_swallows_closing_chunk_of_open_utterance():
    state = SimpleNamespace(_dangling_carry="", _open_utterance_id=None)
    carry = TranslationPipeline._apply_dangling_carry
    assert carry(state, _final("x", 5, end=False), "We build this") == "We build"
    # The utterance's closing chunk is only "and": it must still be queued
    # so the worker sees is_utterance_end=True for utterance 5.
    assert carry(state, _final("x", 5, end=True), "and") == "this and"


# --- dictionary misses on misheard names (JoshAnguo, Muscle Lab, ...) ------


def _dictionary(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text(
        "source_term,target_term,notes,aliases\n"
        "Jiaxian Guo,Jiaxian Guo,Speaker name,Josh Angou|Josh Anguo\n"
        "Matsuo-Iwasawa Lab,松尾・岩澤研究室,,Muscle Lab|Muscle Labs\n"
        "Suspicion-Agent,Suspicion-Agent,Specific AI agent name\n"
        "Gemma,Gemma,\n",
        encoding="utf-8",
    )
    d = TermDictionary()
    d.load_csv(path)
    return d


def test_alias_retrieval(tmp_path):
    d = _dictionary(tmp_path)
    hits = d.relevant_entries("My name is Josh Angou.", limit=5)
    assert [e.source_term for e in hits] == ["Jiaxian Guo"]
    hits = d.relevant_entries("thank you for Muscle Labs invitation", limit=5)
    assert [e.source_term for e in hits] == ["Matsuo-Iwasawa Lab"]


def test_fuzzy_retrieval_for_long_multiword_terms_only(tmp_path):
    d = _dictionary(tmp_path)
    hits = d.relevant_entries("I did a suspension agent in muscle lab", limit=5)
    assert {e.source_term for e in hits} == {"Suspicion-Agent", "Matsuo-Iwasawa Lab"}
    # Short single-word terms never fuzzy-match (Gemini vs Gemma).
    assert d.relevant_entries("contributed to Gemini", limit=5) == []


def test_aliases_shown_in_prompt(tmp_path):
    d = _dictionary(tmp_path)
    text = d.format_for_prompt(d.relevant_entries("Josh Angou", limit=5))
    assert "ASR may mishear as: Josh Angou, Josh Anguo" in text


# --- line breaks ("変なところでの改行") -------------------------------------


def test_latin_spaces_kept_and_words_not_split():
    text = _normalize_spaces("所属先の Google Research Australia です。")
    assert text == "所属先のGoogle Research Australiaです。"
    lines = _wrap_lines(text, 13)
    assert lines == ["所属先のGoogle Research", "Australiaです。"]
    for chunk in _split_chunks(
        _normalize_spaces("主にGeminiとGemmaに貢献し、そして" * 2), 26
    ):
        for a, b in zip(
            _wrap_lines(chunk, 13), _wrap_lines(chunk, 13)[1:], strict=False
        ):
            assert not (a[-1].isascii() and a[-1].isalnum() and b[0].isascii())
            assert b[0] not in "、。"


def test_tiny_utterances_fold_into_next():
    merged = _merge_tiny_utterances(
        [
            _RawUtterance(10.0, "そして"),
            _RawUtterance(11.0, "2024年にGoogleへ移りました。"),
            _RawUtterance(30.0, "はい。"),
        ]
    )
    assert [u.text for u in merged] == ["そして2024年にGoogleへ移りました。", "はい。"]
    assert merged[0].ready_at == 11.0


# --- live pacing ("ちょくちょく早いところがある") ------------------------------


def _drive(presenter, until, step=0.05):
    from real_time_translation.captions.live_presenter import LiveCard

    cards: list[LiveCard] = []
    t = 0.0
    while t <= until:
        card = presenter.poll(t)
        if card is not None:
            cards.append(card)
        t = round(t + step, 4)
    return cards


def test_live_burst_is_paced_not_flashed():
    from real_time_translation.captions.live_presenter import LiveCaptionPresenter

    p = LiveCaptionPresenter()
    for i in range(5):  # a backlog flush: five translations at once
        p.push(f"これは{i}番目の十分に長い字幕の文です。", 0.0, utterance_id=i)
    cards = _drive(p, 30.0)
    assert len(cards) == 5
    for a, b in zip(cards, cards[1:], strict=False):
        assert b.shown_at - a.shown_at >= 5 / 6 - 1e-9


def test_live_tiny_fragment_folds_into_next():
    from real_time_translation.captions.live_presenter import LiveCaptionPresenter

    p = LiveCaptionPresenter()
    p.push("そして", 0.0, utterance_id=1)
    assert p.poll(0.5) is None
    p.push("2024年にGoogleへ移りました。", 1.0, utterance_id=2)
    card = p.poll(1.0)
    assert card is not None and card.text.replace("\n", "").startswith("そして2024")


def test_live_continuation_shows_only_new_tail():
    from real_time_translation.captions.live_presenter import LiveCaptionPresenter

    p = LiveCaptionPresenter()
    p.push("主にGeminiとGemmaに貢献し、", 0.0, utterance_id=7, utterance_end=False)
    first = p.poll(0.0)
    p.push(
        "主にGeminiとGemmaに貢献し、Gemma 3nも担当しました。",
        1.0,
        utterance_id=7,
        utterance_end=True,
    )
    second = _drive(p, 10.0)
    assert first is not None and "Gemma" in first.text
    assert [c.text.replace("\n", "") for c in second] == ["Gemma 3nも担当しました。"]


def test_live_partial_only_when_idle():
    from real_time_translation.captions.live_presenter import LiveCaptionPresenter

    p = LiveCaptionPresenter()
    assert p.is_idle(0.0)
    p.push("これは十分に長い字幕の文です。", 0.0, utterance_id=1)
    card = p.poll(0.0)
    assert card is not None and not p.is_idle(0.1)
    assert p.is_idle(card.shown_at + card.duration)


def test_shifted_word_timings_still_trimmed():
    # 2026-09-24 live run: "Hello, everyone." emitted ending 8.8, then the
    # final re-segmented "everyone." as 8.41-8.9 at the start of the next.
    t = DeepgramTranscriber(api_key="x")
    t._handle_message(
        _results_msg(
            5.48, [("Hello,", 7.6, 8.0), ("everyone.", 8.1, 8.8)], is_final=True
        )
    )
    t._handle_message(
        _results_msg(
            8.41,
            [("everyone.", 8.41, 8.9), ("My", 9.0, 9.2), ("name", 9.3, 9.5)],
            is_final=True,
        )
    )
    assert [r.text for r in _drain(t)] == ["Hello, everyone.", "My name"]


def test_utterance_end_before_final_of_same_segment():
    # Real message order captured from Deepgram on the 07:30 audio: the
    # UtteranceEnd flush of the interim comes BEFORE the segment's is_final,
    # whose word timings are wider than the interim's.
    t = DeepgramTranscriber(api_key="x")
    t._handle_message(
        _results_msg(
            7.21, [("Hello,", 7.85, 8.25), ("everyone.", 8.25, 8.57)], is_final=False
        )
    )
    t._handle_message(SimpleNamespace(type="UtteranceEnd"))
    t._handle_message(
        _results_msg(
            7.21,
            [
                ("Hello,", 7.85, 8.41),
                ("everyone.", 8.41, 8.97),
                ("Yeah.", 8.97, 9.37),
                ("My", 9.37, 9.61),
                ("name", 9.61, 9.77),
            ],
            is_final=True,
        )
    )
    assert [r.text for r in _drain(t)] == ["Hello, everyone.", "Yeah. My name"]

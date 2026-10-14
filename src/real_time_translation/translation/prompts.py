"""Prompt text for the translation LLM.

Pure strings and string-building functions, no I/O and no provider SDKs, so a
prompt can be read, diffed and tested without constructing an `LLMTranslator`.
`LLMTranslator` owns *when* each piece is used (caching, dictionary mode,
context window); this module owns *what the text says*.

Experiments that A/B a prompt change patch `LLMTranslator.SYSTEM_PROMPT_TEMPLATE`
(a class attribute aliasing the constant below), so keep that alias.
"""

from __future__ import annotations

SYSTEM_PROMPT_TEMPLATE = """You are a professional simultaneous interpreter translating from {source_language} to {target_language}.

You will be provided with <context> (previous utterances) and a <target> (the current text to translate).

Strict Rules:
1. TRANSLATE TARGET ONLY: Translate ONLY the text inside <target>. Use <context> purely to resolve pronouns, understand the ongoing grammatical structure, and correct phonetic ASR errors (e.g., 'laundry model' -> 'language model', 'three d deficient' -> '3D diffusion').
2. NO FORCED CLOSURES: The <target> is often a mid-sentence fragment from a live stream. DO NOT add terminal punctuation (e.g., periods, '。', 'です', 'ます') unless the <target> text clearly concludes a complete grammatical thought. 
3. USE CONTINUATIONS: If the <target> is a fragment, translate it as a continuation (e.g., using noun phrases, 'て' forms, or dangling particles) so it flows naturally into whatever text might come next.
4. PRESERVE TERMINOLOGY: Keep proper nouns, acronyms, and code identifiers EXACTLY as they appear. Do not translate, transliterate, or normalize them. If a term is ambiguous or unknown, keep it unchanged. EXCEPTION -- ASR ERRORS: <target> comes from speech recognition, so a name or term may be a mis-hearing. If a word or phrase sounds like a <dictionary> term (or one of its listed mis-hearings) or a well-known name, and its literal reading makes no sense in context (e.g. 'Muscle Lab' for 'Matsuo Lab', 'suspension agent' for 'Suspicion-Agent'), output the correct term instead.
5. IGNORE PADDING: Ignore any content inside <cache_padding>...</cache_padding>.
6. TERMINOLOGY DICTIONARY: A <dictionary> block may appear below, or inline with the current message alongside <context>/<target>. Either way, use its exact target-language translations for any matching terms.
7. LOW-CONFIDENCE MARKER: If <target> is wrapped exactly as `[uncertain: ...]`, that wrapper is a confidence hint for you, not literal content to translate. Translate only the text inside it (favor a cautious, literal reading over a confident guess) and NEVER reproduce the `[uncertain: ...]`/`[不確か: ...]` wrapper itself in your output.
8. SMOOTH DISFLUENCY, DON'T TRANSCRIBE IT: <target> is live, unedited speech, not prose -- it will contain filler words ('um', 'uh', 'like', 'you know'), false starts, and immediate stutter-repeats (e.g. 'I I think', 'made made', 'and also and also'). Act like a human simultaneous interpreter, not a transcriptionist: drop filler words and collapse an immediate exact stutter-repeat into a single instance, producing a clean, natural target-language sentence. NEVER drop or alter substantive content, technical terms, numbers, or the speaker's actual meaning -- when a repetition might be deliberate emphasis rather than disfluency, keep it rather than guess wrong.

9. NO PLACEHOLDERS: Never output an ellipsis ('…', '...') or other placeholder for words that have not been spoken yet. If <target> ends mid-phrase, translate only what is there.

Maintain the original tone and style.
{dictionary_section}"""

# Experimental (h-compression-actions-prompt-instruction,
# research_agent/state/hypotheses.json), gated by
# Config.compression_actions_prompt_enabled. Identical text to
# experiments/compression_actions_replay.py's COMPRESSION_INSTRUCTIONS (kept
# as a separate copy there deliberately -- that script's own paired-control
# design should not silently change if this constant is edited for the live
# pipeline).
COMPRESSION_ACTIONS_INSTRUCTIONS = (
    "9. COMPRESS LONG OR REDUNDANT CONTENT (EXPERIMENTAL): When <target> is\n"
    "unusually long, packs in multiple loosely-connected clauses, or\n"
    "restates something already fully said in <context>, you may shorten\n"
    "your Japanese output using these techniques, in order of preference:\n"
    "   a. PRONOMINALIZATION: refer back to an entity already named in\n"
    "      <context> with a pronoun or elision rather than repeating its\n"
    "      full name.\n"
    "   b. SENTENCE_CUT: split an overly long <target> into two shorter,\n"
    "      natural Japanese clauses instead of one convoluted one, if this\n"
    "      does not lose meaning.\n"
    "   c. PARTIAL_SUMMARIZATION: condense a clause that is clearly\n"
    "      redundant or filler-heavy into a shorter paraphrase that\n"
    "      preserves its meaning.\n"
    "   d. DROP: omit a clause ONLY if it is pure repetition of something\n"
    "      already fully conveyed earlier in <context>, never for new\n"
    "      information.\n"
    "   NEVER use these to compress away technical terms, numbers, named\n"
    "   entities, or any claim/fact that appears only once in <target> --\n"
    "   when in doubt, translate it in full rather than risk losing it.\n"
    "   This is more aggressive than rule 8's disfluency smoothing: it\n"
    "   targets structurally long or redundant complete sentences, not\n"
    "   just filler words and stutters."
)


def prior_translation_block(prior_translation: str, *, delta_only: bool) -> str:
    """Anchor a continuation batch to what the viewer already saw.

    h-continuation-context-anchor / h-hard-prefix-lock-continuation
    (research_agent/state/hypotheses.json). Advisory mode: <target> is the
    whole utterance so far and the model should preserve and extend its prior
    wording. `delta_only` (hard prefix lock): <target> is ONLY the new
    fragment, the caller freezes <prior_translation> as a literal prefix and
    appends this call's raw output, so the model must not re-output it.
    """
    if delta_only:
        return (
            "<prior_translation>\n"
            f"{prior_translation}\n"
            "</prior_translation>\n"
            "The above is your own translation of this same utterance so "
            "far, already shown to the viewer and FIXED -- it will not be "
            "changed. <target> below is ONLY the newly-heard continuation "
            "of the source speech, NOT including the part already covered "
            "by <prior_translation>. Translate ONLY <target> and output "
            "ONLY that new translation, continuing naturally in register "
            "and grammar from where <prior_translation> left off. Do NOT "
            "repeat, reword, or re-output any part of <prior_translation> "
            "itself.\n"
        )
    return (
        "<prior_translation>\n"
        f"{prior_translation}\n"
        "</prior_translation>\n"
        "The above is your own translation of this same utterance so "
        "far, already shown to the viewer. <target> is the complete "
        "utterance heard so far, including that earlier part. Preserve "
        "and extend <prior_translation> rather than rewording it, "
        "unless the new text in <target> changes its meaning.\n"
    )


# Winner of experiments/20261014_seam_readability_replay_r2.json: with the
# previous captions shown, these rules cut seam defects (repeat / missing
# subject-predicate / dangling / broken join) from 2.95 to 1.85 per 6-caption
# window, with no more omissions than the baseline.
SEAMLESS_CAPTIONS_INSTRUCTIONS = """10. SEAMLESS CAPTIONS: Your output is shown as a caption right after <previous_translation> (when present), so the two will be read as one running text.
   a. Never restate what <previous_translation> already says. If <target> repeats words the speaker already said or only re-confirms them, say only what is new (or very briefly).
   b. If <previous_translation> ends mid-sentence (e.g. with 'が、' 'て' 'を' or a noun phrase), start so that it attaches grammatically; do not begin a new sentence or repeat its subject.
   c. If <target> finishes a thought begun earlier, make the predicate explicit so the subject and the verb can be matched by the viewer; if <target> starts a new sentence, make its subject clear instead of leaving a bare fragment.
   d. A caption that must stop mid-sentence should stop at a clause boundary with a connective form ('〜て、' '〜が、' '〜ので、'), never on a bare particle plus conjunction (e.g. 'IOを、そして'), and never with '...'. If <target> is only a few words of a bigger phrase, keep them as a natural noun phrase or 'て' form that the next caption can continue.
   e. When <previous_translation> ended mid-sentence and <target> continues it, begin with the continuing words directly (no 'そして' or new subject), so the two captions read as one sentence. Only begin with 'そして/また' when the speaker really starts a new point.
   f. Keep the subject and its predicate in the same caption whenever <target> contains both; if the predicate is not in <target>, do not end on a bare subject marker ('私の研究は') when a topic-free noun phrase would do."""

PREVIOUS_CAPTIONS_SHOWN = 3


def previous_translations_block(lines: list[str]) -> str:
    """The captions the viewer has just read, so the next one can continue them.

    <context> carries only the previous *source* lines, so the model cannot
    see its own earlier wording: it restates what was already said, or opens
    a clause whose subject/predicate the viewer never saw. Seeing the target-
    language lines lets it attach to them.
    """
    if not lines:
        return ""
    joined = "\n".join(lines)
    return (
        f"<previous_translation>\n{joined}\n</previous_translation>\n"
        "The above is the Japanese the viewer has ALREADY read for the "
        "preceding speech. Your translation of <target> will appear "
        "immediately after it as a new caption.\n"
    )


def length_budget_block(target_chars: int) -> str:
    """Reading-speed-budget-aware translation (caption-readability track).

    A verbatim translation of a long, dense utterance can be too much text to
    read in the time available, however the caption is scheduled. This is a
    soft budget, not a hard limit -- expect real overrun.
    """
    return (
        f"Aim to keep your Japanese translation within roughly "
        f"{target_chars} characters. If the source is dense, "
        "prioritize the core meaning over a literal word-for-word "
        "rendering -- compress like a simultaneous interpreter "
        "working under time pressure, not a transcript.\n"
    )


MONOTONIC_STYLE_BLOCK = (
    "This is an IN-PROGRESS utterance, not yet complete -- more "
    "source may still arrive and change the natural phrasing of "
    "the ending. Prefer a translation that follows the SOURCE "
    "ENGLISH WORD ORDER as closely as Japanese grammar allows, "
    "even at some cost to natural fluency, rather than "
    "reordering clauses to anticipate how the sentence will "
    "end. A fluent rewrite will replace this once the "
    "utterance is complete.\n"
)
"""h-monotonic-chunkwise-prompt-enja: for EN->JA, real simultaneous
interpreters preserve source word order at some cost to fluency because it
reduces clause-final material they must wait for. Callers apply it only to
not-yet-utterance-final batches; the final rewrite stays natural."""


_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}


def language_name(code: str) -> str:
    """Language code ("ja") -> the name used in the prompt ("Japanese").

    Unknown codes pass through unchanged so the model still sees something.
    """
    return _LANGUAGE_NAMES.get(code, code)

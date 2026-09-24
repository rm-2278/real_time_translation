"""Dictionary management for domain-specific terminology."""

import csv
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


@dataclass
class DictionaryEntry:
    """A single dictionary entry."""

    source_term: str
    target_term: str
    notes: str = ""
    # Known ASR mis-transcriptions of `source_term` (e.g. "Josh Angou" for
    # "Jiaxian Guo"). Matched like the source term itself during retrieval
    # and shown to the LLM so it can map the misheard form back.
    aliases: tuple[str, ...] = ()


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Fuzzy fallback only for long, multi-word terms: short names score too
# close to unrelated ones to be safe (measured on the 2026-09-24 review:
# "Josh Angou"/"Jiaxian Guo" 0.53 vs the unrelated "Gemini"/"Gemma" 0.55,
# while "suspension agent"/"Suspicion-Agent" scores 0.83). Short or
# single-word mishearings need an explicit `aliases` entry instead.
_FUZZY_MIN_RATIO = 0.8
_FUZZY_MIN_LETTERS = 10
_FUZZY_MAX_WORDS = 6


@lru_cache(maxsize=1024)
def _fuzzy_target(source_term: str) -> tuple[str, int]:
    """(letters-only form, word count) of a dictionary source term."""
    return _letters(source_term), len(re.split(r"[\s-]+", source_term.strip()))


@lru_cache(maxsize=256)
def _letter_ngrams(text: str) -> dict[int, tuple[str, ...]]:
    """Letters-only word n-grams of `text`, by n (computed once per text)."""
    tokens = text.split()
    return {
        size: tuple(
            _letters(" ".join(tokens[i : i + size]))
            for i in range(len(tokens) - size + 1)
        )
        for size in range(1, _FUZZY_MAX_WORDS + 1)
    }


def term_pattern(term: str) -> re.Pattern[str]:
    """Word-boundary, case-insensitive pattern for matching a source term.

    Shared by dictionary retrieval and glossary-adherence scoring so both
    agree on what counts as "this term appears in this text". `\\b`
    degrades gracefully for multi-word/hyphenated terms (e.g. "bag of
    words"): it still anchors on the first/last character class, enough to
    avoid the common false-positive of matching inside a longer word.
    """
    return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)


class TermDictionary:
    """Dictionary for domain-specific terminology.

    Manages terminology mappings loaded from CSV files.
    CSV format: source_term,target_term,notes (optional),aliases (optional,
    "|"-separated known ASR mis-transcriptions of source_term)
    """

    def __init__(self) -> None:
        """Initialize empty dictionary."""
        self._entries: dict[str, DictionaryEntry] = {}

    def load_csv(self, path: Path | str) -> int:
        """Load dictionary entries from CSV file.

        Args:
            path: Path to CSV file

        Returns:
            Number of entries loaded
        """
        path = Path(path)
        count = 0

        with path.open(encoding="utf-8") as f:
            reader = csv.reader(f)

            # Skip header if present
            first_row = next(reader, None)
            header_values = ("source", "source_term", "原語")
            if first_row and first_row[0].lower() not in header_values:
                # Not a header, process as data
                self._add_row(first_row)
                count += 1

            for row in reader:
                if self._add_row(row):
                    count += 1

        return count

    def _add_row(self, row: list[str]) -> bool:
        """Add a row from CSV.

        Args:
            row: CSV row data

        Returns:
            True if entry was added
        """
        if len(row) < 2:
            return False

        source_term = row[0].strip()
        target_term = row[1].strip()
        notes = row[2].strip() if len(row) > 2 else ""
        aliases = (
            tuple(a.strip() for a in row[3].split("|") if a.strip())
            if len(row) > 3
            else ()
        )

        if not source_term or not target_term:
            return False

        self._entries[source_term.lower()] = DictionaryEntry(
            source_term=source_term,
            target_term=target_term,
            notes=notes,
            aliases=aliases,
        )
        return True

    def add_entry(self, source_term: str, target_term: str, notes: str = "") -> None:
        """Add a dictionary entry.

        Args:
            source_term: Term in source language
            target_term: Term in target language
            notes: Optional notes about the term
        """
        self._entries[source_term.lower()] = DictionaryEntry(
            source_term=source_term,
            target_term=target_term,
            notes=notes,
        )

    def add_if_absent(self, entries: Iterable[DictionaryEntry]) -> int:
        """Add entries whose term isn't already present.

        For merging a best-effort/supplementary source (e.g. auto-extracted
        pre-brief terms) that should never override an already-loaded
        entry from a domain pack or session `dictionary_path` -- those are
        either curated or more deliberately chosen.

        Returns:
            Number of entries actually added
        """
        added = 0
        for entry in entries:
            key = entry.source_term.lower()
            if key in self._entries:
                continue
            self._entries[key] = entry
            added += 1
        return added

    def source_terms(self, limit: int | None = None) -> list[str]:
        """Source-language terms, in load order, for ASR keyterm biasing.

        Args:
            limit: Maximum number of terms to return (provider prompting
                caps apply, e.g. Deepgram allows ~100 keyterms/request).

        Returns:
            List of source terms, capped at `limit` if given
        """
        terms = [entry.source_term for entry in self._entries.values()]
        return terms[:limit] if limit is not None else terms

    def get(self, term: str) -> DictionaryEntry | None:
        """Look up a term.

        Args:
            term: Term to look up

        Returns:
            Dictionary entry or None if not found
        """
        return self._entries.get(term.lower())

    def relevant_entries(
        self,
        text: str,
        *,
        limit: int,
        extra_context: Iterable[str] = (),
    ) -> list[DictionaryEntry]:
        """Entries whose source term appears in `text` or `extra_context`.

        Cheap word-boundary substring retrieval -- no embeddings, no extra
        LLM call. Lets a dictionary scale past what's worth dumping into
        every prompt in full: only the terms plausibly relevant to the
        current utterance get injected (see `format_for_prompt`).

        Trade-off: unlike the full-dump path, this can't help the LLM
        recover a term that Deepgram mis-transcribed into something that no
        longer contains the term as a substring (e.g. "language model" ->
        "laundry model") -- it only finds terms that survived ASR intact.
        Deepgram Keyterm Prompting (see `DeepgramTranscriber`) is the
        primary defense against that class of error; this is a secondary,
        scale-oriented mechanism, not a replacement.

        Args:
            text: Primary text to match against (typically the current
                utterance being translated)
            limit: Maximum number of entries to return
            extra_context: Additional text to match against (e.g. recent
                context lines), searched after `text` is exhausted

        Returns:
            Matching entries, in dictionary load order, capped at `limit`
        """
        matches = [
            entry
            for entry in self._entries.values()
            if self._matches(entry, text, fuzzy=True)
        ]
        if len(matches) < limit:
            matched_ids = {id(entry) for entry in matches}
            for context_line in extra_context:
                if len(matches) >= limit:
                    break
                for entry in self._entries.values():
                    if id(entry) in matched_ids:
                        continue
                    if self._matches(entry, context_line):
                        matches.append(entry)
                        matched_ids.add(id(entry))
                        if len(matches) >= limit:
                            break
        return matches[:limit]

    @staticmethod
    def _matches(entry: DictionaryEntry, text: str, *, fuzzy: bool = False) -> bool:
        """Source term or a known alias appears, or (if `fuzzy`) a close match.

        Fuzzy matching is only worth its cost on the utterance being
        translated -- context lines already had their own turn -- and only
        considers candidates sharing the term's first letter: a mishearing
        usually keeps the onset (suspension/suspicion, Celebrace/Cerebras).
        """
        if term_pattern(entry.source_term).search(text):
            return True
        if any(term_pattern(alias).search(text) for alias in entry.aliases):
            return True
        if not fuzzy:
            return False
        target, n_words = _fuzzy_target(entry.source_term)
        if n_words < 2 or len(target) < _FUZZY_MIN_LETTERS:
            return False
        ngrams = _letter_ngrams(text)
        matcher: SequenceMatcher[str] | None = None
        for size in (n_words - 1, n_words, n_words + 1):
            for candidate in ngrams.get(size, ()):
                if not candidate or candidate[0] != target[0]:
                    continue
                # ratio <= 2*min(len)/(sum of lens): cheap length prefilter.
                if 2 * min(len(candidate), len(target)) < _FUZZY_MIN_RATIO * (
                    len(candidate) + len(target)
                ):
                    continue
                if matcher is None:
                    matcher = SequenceMatcher(None, b=target)
                matcher.set_seq1(candidate)
                if (
                    matcher.quick_ratio() >= _FUZZY_MIN_RATIO
                    and matcher.ratio() >= _FUZZY_MIN_RATIO
                ):
                    return True
        return False

    def format_for_prompt(
        self, entries: Iterable[DictionaryEntry] | None = None
    ) -> str:
        """Format dictionary entries for inclusion in an LLM prompt.

        Args:
            entries: Specific entries to format (e.g. from
                `relevant_entries`). Defaults to the full dictionary.

        Returns:
            Formatted dictionary string
        """
        entries = list(self._entries.values()) if entries is None else list(entries)
        if not entries:
            return ""

        lines = ["[Terminology Dictionary - Use these exact translations:]"]
        for entry in entries:
            line = f"- {entry.source_term} → {entry.target_term}"
            if entry.notes:
                line += f" ({entry.notes})"
            if entry.aliases:
                line += f" [ASR may mishear as: {', '.join(entry.aliases)}]"
            lines.append(line)

        return "\n".join(lines)

    def __len__(self) -> int:
        """Return number of entries."""
        return len(self._entries)

    def __bool__(self) -> bool:
        """Return True if dictionary has entries."""
        return bool(self._entries)

    def __iter__(self) -> Iterator[DictionaryEntry]:
        """Iterate over dictionary entries."""
        return iter(self._entries.values())

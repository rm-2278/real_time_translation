"""Detect function words that dangle at the end of a final ASR chunk.

Deepgram sometimes finalizes a chunk mid-phrase ("... and also"), and
translating that chunk alone yields a hanging fragment. The pipeline uses this
split to hold the dangling tail back and prepend it to the next chunk
(`Config.dangling_carry_enabled`). English-specific by construction.
"""

# Words that, when they END a final ASR chunk, leave its translation hanging
# ("... And" -> "そして、", "introduce my" -> "私の…"). See
# `split_dangling_tail`.
DANGLING_WORDS = frozenset(
    {
        # connectives / fillers
        *("and", "so", "but", "or", "also", "then", "like", "because", "as"),
        *("since", "although", "while", "if", "when", "where", "which", "that"),
        *("um", "uh"),
        # determiners / possessives
        *("the", "a", "an", "my", "our", "your", "their", "his", "her", "its"),
        *("this", "these", "those", "some"),
        # prepositions
        *("to", "of", "in", "on", "for", "with", "from", "by", "at", "about"),
        "into",
    }
)
_MAXDANGLING_WORDS = 3


def split_dangling_tail(text: str) -> tuple[str, str]:
    """Split `text` into (head, tail) where tail is trailing dangling words.

    Stops at sentence-final punctuation: "... stuffs. Okay." has no tail,
    "... language model and also" has tail "and also".
    """
    tokens = text.split()
    i = len(tokens)
    while i > 0 and len(tokens) - i < _MAXDANGLING_WORDS:
        word = tokens[i - 1]
        if word[-1] in ".?!":
            break
        if word.strip(",;:").lower() not in DANGLING_WORDS:
            break
        i -= 1
    return " ".join(tokens[:i]), " ".join(tokens[i:])

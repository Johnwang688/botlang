"""
Bot language tokenizer.

Produces a flat stream of tokens. Knows nothing about phrases, blocks, or
semantics — that's the matcher's job. See interpreter/matcher.py.
"""

from dataclasses import dataclass

# Unit words become UNIT tokens wherever they appear in source.
# These are unlikely to clash with sensible variable names. Grow as needed.
UNITS = {
    "percent", "pct",
    "ms", "milliseconds", "millisecond",
    "seconds", "second", "sec",
    "minutes", "minute", "min",
    "inches", "inch",
    "degrees", "degree", "deg",
    "rotations", "rotation", "turns", "turn",
    "mm", "cm", "meters", "meter",
    "volts", "volt",
    "amps", "amp",
    "rpm",
}

PUNCT_CHARS = set("{}(),:")


@dataclass
class Token:
    kind: str         # WORD, NUMBER, STRING, UNIT, NEWLINE, PUNCT, RAW_CPP
    value: str
    line: int
    col: int


class TokenizeError(Exception):
    pass


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    line, col, i, n = 1, 1, 0, len(source)

    while i < n:
        c = source[i]
        start_col = col

        if c == "\n":
            tokens.append(Token("NEWLINE", "\n", line, col))
            line += 1
            col = 1
            i += 1
            continue

        if c in " \t\r":
            i += 1
            col += 1
            continue

        if c == "#":
            while i < n and source[i] != "\n":
                i += 1
                col += 1
            continue

        if c == '"':
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j >= n:
                raise TokenizeError(f"line {line}: unterminated string")
            tokens.append(Token("STRING", source[i + 1:j], line, start_col))
            col += (j + 1 - i)
            i = j + 1
            continue

        if c == "`":
            j = i + 1
            while j < n and source[j] != "`":
                j += 1
            if j >= n:
                raise TokenizeError(f"line {line}: unterminated raw C++ block")
            tokens.append(Token("RAW_CPP", source[i + 1:j], line, start_col))
            col += (j + 1 - i)
            i = j + 1
            continue

        if c.isdigit() or (c == "-" and i + 1 < n and source[i + 1].isdigit()):
            j = i + (1 if c == "-" else 0)
            while j < n and (source[j].isdigit() or source[j] == "."):
                j += 1
            tokens.append(Token("NUMBER", source[i:j], line, start_col))
            col += (j - i)
            i = j
            continue

        if c.isalpha() or c == "_":
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j].lower()
            kind = "UNIT" if word in UNITS else "WORD"
            tokens.append(Token(kind, word, line, start_col))
            col += (j - i)
            i = j
            continue

        if c in PUNCT_CHARS:
            tokens.append(Token("PUNCT", c, line, start_col))
            i += 1
            col += 1
            continue

        raise TokenizeError(f"line {line}:{col}: unexpected character {c!r}")

    tokens.append(Token("NEWLINE", "\n", line, col))
    return tokens


if __name__ == "__main__":
    import sys
    src = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1]).read()
    for t in tokenize(src):
        if t.kind != "NEWLINE":
            print(f"{t.kind:8} {t.value!r:20} (line {t.line}:{t.col})")
        else:
            print()

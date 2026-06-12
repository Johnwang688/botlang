"""
Loads a language.bot config file into structured Python objects.

Config grammar (line-oriented, # for comments, `=>` separates LHS/RHS,
multi-line phrases supported by indenting the => continuation):

    unit <unit-word>      => <cpp-value>
    symbol <identifier>   => <cpp-qualified-name>
    filler <w1> <w2> ...
    phrase <pattern>      => <template>
"""

from dataclasses import dataclass


@dataclass
class PatternElement:
    kind: str    # LITERAL, PUNCT, SLOT
    value: str   # word, punctuation char, or slot name


@dataclass
class Phrase:
    pattern: list[PatternElement]
    template: str


@dataclass
class Config:
    units: dict[str, str]         # "percent" -> "vex::percent"
    symbols: dict[str, str]       # "motor1" -> "bot::motors::motor1"
    fillers: set[str]             # {"a", "an", "the", ...}
    phrases: list[Phrase]         # sorted longest-first


def parse_pattern(pattern_str: str) -> list[PatternElement]:
    elements: list[PatternElement] = []
    i, n = 0, len(pattern_str)
    while i < n:
        c = pattern_str[i]
        if c.isspace():
            i += 1
            continue
        if c == "<":
            j = pattern_str.index(">", i)
            elements.append(PatternElement("SLOT", pattern_str[i + 1:j].strip()))
            i = j + 1
            continue
        if c in "{}(),:":
            elements.append(PatternElement("PUNCT", c))
            i += 1
            continue
        j = i
        while j < n and not pattern_str[j].isspace() and pattern_str[j] not in "<{}(),:":
            j += 1
        elements.append(PatternElement("LITERAL", pattern_str[i:j].lower()))
        i = j
    return elements


def _join_continuations(lines: list[str]) -> list[str]:
    """Merge indented continuation lines into the previous logical line."""
    out: list[str] = []
    for line in lines:
        if line.strip() == "":
            continue
        if line.startswith((" ", "\t")) and out:
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return out


def load_config(source: str) -> Config:
    units: dict[str, str] = {}
    symbols: dict[str, str] = {}
    fillers: set[str] = set()
    phrases: list[Phrase] = []

    # Strip comments and join indented continuations.
    cleaned: list[str] = []
    for raw in source.splitlines():
        if "#" in raw:
            # Naively strip comments — strings in templates shouldn't contain #
            # for v1. If they do, escape with backslash (not implemented yet).
            raw = raw.split("#", 1)[0]
        cleaned.append(raw)
    logical = _join_continuations(cleaned)

    for line in logical:
        line = line.strip()
        if not line:
            continue
        if line.startswith("unit "):
            body = line[len("unit "):].strip()
            lhs, _, rhs = body.partition("=>")
            units[lhs.strip().lower()] = rhs.strip()
        elif line.startswith("symbol "):
            body = line[len("symbol "):].strip()
            lhs, _, rhs = body.partition("=>")
            symbols[lhs.strip().lower()] = rhs.strip()
        elif line.startswith("filler "):
            body = line[len("filler "):].strip()
            for w in body.split():
                fillers.add(w.lower())
        elif line.startswith("phrase "):
            body = line[len("phrase "):].strip()
            lhs, _, rhs = body.partition("=>")
            pattern = parse_pattern(lhs.strip())
            phrases.append(Phrase(pattern=pattern, template=rhs.strip()))
        else:
            raise ValueError(f"unknown directive: {line!r}")

    # Match priority: longest first; tie-break on literal/punct count so
    # more-specific patterns (`stop driving`) beat slot-heavy ones (`stop <motor>`).
    def specificity(p: Phrase) -> tuple[int, int]:
        literals = sum(1 for e in p.pattern if e.kind != "SLOT")
        return (len(p.pattern), literals)

    phrases.sort(key=specificity, reverse=True)

    return Config(units=units, symbols=symbols, fillers=fillers, phrases=phrases)


if __name__ == "__main__":
    import sys
    cfg = load_config(open(sys.argv[1]).read())
    print(f"units:    {len(cfg.units)}")
    print(f"symbols:  {len(cfg.symbols)}")
    print(f"fillers:  {len(cfg.fillers)}")
    print(f"phrases:  {len(cfg.phrases)}")
    print()
    for p in cfg.phrases:
        rendered = " ".join(
            f"<{e.value}>" if e.kind == "SLOT" else e.value
            for e in p.pattern
        )
        print(f"  {rendered}  =>  {p.template}")

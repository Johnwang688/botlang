"""
Bot language matcher and codegen.

Algorithm:
1. Split the token stream into top-level statements (newline-separated,
   braces extend statements across newlines).
2. For each statement, try every phrase pattern (longest first) in
   statement-mode. If one matches the whole statement, emit its template
   with slot substitutions. Else, fall back to expression-mode.
3. Block-vs-expression for slot values is decided by template context:
   if the slot reference sits directly between `{` and `}` in the template,
   the capture is treated as a block. Otherwise it's an expression.
"""

import re

from tokenizer import Token
from config import Config, PatternElement


SLOT_REF_RE = re.compile(r"<([a-z_][a-z0-9_]*)>")


# ─── Statement splitting ────────────────────────────────────────────────────

def split_into_statements(tokens: list[Token]) -> list[list[Token]]:
    """Split a token list into per-statement token lists. Braces hold a
    statement together across newlines."""
    statements: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for t in tokens:
        if t.kind == "PUNCT" and t.value == "{":
            depth += 1
            current.append(t)
        elif t.kind == "PUNCT" and t.value == "}":
            depth -= 1
            current.append(t)
        elif t.kind == "NEWLINE" and depth == 0:
            if any(tt.kind != "NEWLINE" for tt in current):
                statements.append(current)
            current = []
        else:
            current.append(t)
    if any(tt.kind != "NEWLINE" for tt in current):
        statements.append(current)
    return statements


# ─── Pattern matching ───────────────────────────────────────────────────────

def _skip_noise(tokens: list[Token], i: int, fillers: set[str]) -> int:
    """Skip filler words and newlines."""
    while i < len(tokens):
        t = tokens[i]
        if t.kind == "NEWLINE":
            i += 1
        elif t.kind == "WORD" and t.value in fillers:
            i += 1
        else:
            break
    return i


def _literal_match(t: Token, value: str) -> bool:
    """A pattern LITERAL matches WORD or UNIT tokens with the same value."""
    return t.kind in ("WORD", "UNIT") and t.value == value


def _find_next(tokens: list[Token], start: int, want: PatternElement) -> int | None:
    """Find next token matching `want` at brace-depth 0. Returns index or None."""
    depth = 0
    for i in range(start, len(tokens)):
        t = tokens[i]
        if t.kind == "PUNCT" and t.value == "{":
            depth += 1
            continue
        if t.kind == "PUNCT" and t.value == "}":
            if depth == 0 and want.kind == "PUNCT" and want.value == "}":
                return i
            depth -= 1
            continue
        if depth != 0:
            continue
        if want.kind == "PUNCT" and t.kind == "PUNCT" and t.value == want.value:
            return i
        if want.kind == "LITERAL" and _literal_match(t, want.value):
            return i
    return None


def try_match(
    pattern: list[PatternElement],
    tokens: list[Token],
    start: int,
    fillers: set[str],
    statement_mode: bool,
) -> tuple[dict[str, list[Token]], int] | None:
    """Try to match `pattern` against `tokens[start:]`.
    Returns (captures, end_index) on success, or None on failure."""
    captures: dict[str, list[Token]] = {}
    i = start
    for p_idx, el in enumerate(pattern):
        if el.kind == "LITERAL":
            i = _skip_noise(tokens, i, fillers)
            if i >= len(tokens) or not _literal_match(tokens[i], el.value):
                return None
            i += 1
        elif el.kind == "PUNCT":
            i = _skip_noise(tokens, i, fillers)
            if i >= len(tokens) or tokens[i].kind != "PUNCT" or tokens[i].value != el.value:
                return None
            i += 1
        elif el.kind == "SLOT":
            if p_idx + 1 < len(pattern):
                end = _find_next(tokens, i, pattern[p_idx + 1])
                if end is None:
                    return None
                captures[el.value] = tokens[i:end]
                i = end
            else:
                if statement_mode:
                    captures[el.value] = tokens[i:]
                    i = len(tokens)
                else:
                    j = _skip_noise(tokens, i, fillers)
                    if j >= len(tokens):
                        return None
                    captures[el.value] = [tokens[j]]
                    i = j + 1
    return captures, i


# ─── Slot value rendering ───────────────────────────────────────────────────

def render_slot(toks: list[Token], is_block: bool, config: Config) -> str:
    """Render a captured slot value into a C++ string."""
    # Strip leading/trailing newlines for tidy output
    while toks and toks[0].kind == "NEWLINE":
        toks = toks[1:]
    while toks and toks[-1].kind == "NEWLINE":
        toks = toks[:-1]

    if not toks:
        return ""

    if is_block:
        inner = split_into_statements(toks)
        emitted = [transpile_statement(s, config) for s in inner]
        return "\n".join(s for s in emitted if s)

    # Single-token captures: direct emit with symbol/unit lookup
    if len(toks) == 1:
        t = toks[0]
        if t.kind == "STRING":
            return f'"{t.value}"'
        if t.kind == "NUMBER":
            return t.value
        if t.kind == "UNIT":
            return config.units.get(t.value, t.value)
        if t.kind == "WORD":
            return config.symbols.get(t.value, t.value)
        if t.kind == "RAW_CPP":
            return t.value

    return transpile_expression(toks, config)


def emit_template(
    template: str,
    captures: dict[str, list[Token]],
    config: Config,
) -> str:
    """Substitute <name> references with rendered slot values. The slot's
    surrounding context in the template decides expression-vs-block."""
    def replace(m: re.Match) -> str:
        name = m.group(1)
        if name not in captures:
            return m.group(0)
        before = template[:m.start()].rstrip()
        after = template[m.end():].lstrip()
        is_block = before.endswith("{") and after.startswith("}")
        return render_slot(captures[name], is_block, config)

    return SLOT_REF_RE.sub(replace, template)


# ─── Expression-mode and statement-mode transpilation ───────────────────────

def transpile_expression(tokens: list[Token], config: Config) -> str:
    """Walk left-to-right, greedily matching phrases. Unmatched tokens
    pass through with symbol/unit lookup."""
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t.kind == "NEWLINE":
            i += 1
            continue
        if t.kind == "WORD" and t.value in config.fillers:
            i += 1
            continue
        matched = False
        for phrase in config.phrases:
            result = try_match(phrase.pattern, tokens, i, config.fillers, statement_mode=False)
            if result is not None:
                captures, end = result
                out.append(emit_template(phrase.template, captures, config))
                i = end
                matched = True
                break
        if not matched:
            out.append(render_slot([t], is_block=False, config=config))
            i += 1
    return " ".join(out)


def transpile_statement(tokens: list[Token], config: Config) -> str:
    """Transpile a single statement. Tries each phrase as a whole-statement
    match (longest first); falls back to expression transpile."""
    # Trim leading/trailing newlines but keep interior ones (block bodies
    # rely on them).
    while tokens and tokens[0].kind == "NEWLINE":
        tokens = tokens[1:]
    while tokens and tokens[-1].kind == "NEWLINE":
        tokens = tokens[:-1]
    if not tokens:
        return ""

    for phrase in config.phrases:
        result = try_match(phrase.pattern, tokens, 0, config.fillers, statement_mode=True)
        if result is None:
            continue
        captures, end = result
        # Require we consumed every non-noise token.
        j = _skip_noise(tokens, end, config.fillers)
        if j != len(tokens):
            continue
        body = emit_template(phrase.template, captures, config).rstrip()
        if body and not body.endswith((";", "}")):
            body += ";"
        return body

    expr = transpile_expression(tokens, config).rstrip()
    if not expr:
        return ""
    if not expr.endswith((";", "}")):
        return expr + ";"
    return expr


# ─── Pretty-printer ─────────────────────────────────────────────────────────

def pretty_print(cpp: str) -> str:
    """Insert structural newlines (after `{`, after `;` outside parens,
    before `}`) and re-indent by brace depth. Naive about strings but safe
    for our generated output."""
    # Phase 1: insert newlines around structural punctuation.
    out: list[str] = []
    paren_depth = 0
    in_string = False
    i = 0
    while i < len(cpp):
        c = cpp[i]
        if c == '"' and (i == 0 or cpp[i - 1] != "\\"):
            in_string = not in_string
            out.append(c)
            i += 1
            continue
        if in_string:
            out.append(c)
            i += 1
            continue
        if c == "(":
            paren_depth += 1
            out.append(c)
        elif c == ")":
            paren_depth = max(0, paren_depth - 1)
            out.append(c)
        elif c == "{":
            out.append(c)
            out.append("\n")
        elif c == "}":
            while out and out[-1] in " \t":
                out.pop()
            if out and out[-1] != "\n":
                out.append("\n")
            out.append(c)
            # Newline after } unless followed by a chained keyword (else, while
            # for do-while, catch) or trivial punctuation.
            j = i + 1
            while j < len(cpp) and cpp[j] in " \t":
                j += 1
            rest = cpp[j:]
            chains = ("else", "while", "catch")
            keeps_inline = (
                (j < len(cpp) and cpp[j] in (";", ",", ")"))
                or any(
                    rest.startswith(k) and (len(rest) == len(k) or rest[len(k)] in " \t({")
                    for k in chains
                )
            )
            if not keeps_inline:
                out.append("\n")
        elif c == ";" and paren_depth == 0:
            out.append(c)
            out.append("\n")
        else:
            out.append(c)
        i += 1

    text = "".join(out)

    # Phase 2: indent by brace depth.
    lines = text.split("\n")
    result: list[str] = []
    depth = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # Handle lines that start with `}` (closing block, possibly with continuation)
        starts_close = line.startswith("}")
        if starts_close:
            depth = max(0, depth - 1)
        result.append("    " * depth + line)
        # Update depth for next line, counting opens/closes on this line, but
        # skip the leading `}` we already accounted for.
        scan_line = line[1:] if starts_close else line
        opens = scan_line.count("{")
        closes = scan_line.count("}")
        depth = max(0, depth + opens - closes)

    return "\n".join(result)


def transpile(tokens: list[Token], config: Config) -> str:
    statements = split_into_statements(tokens)
    body = "\n".join(
        s for s in (transpile_statement(s, config) for s in statements) if s
    )
    return pretty_print(body)

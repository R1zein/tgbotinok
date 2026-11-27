"""Readable plain text for common LaTeX formulas in Telegram chat replies."""
import re


# Consume code first so examples containing math delimiters remain copyable.
_SPANS = re.compile(
    r"(?P<ticks>\x60+)[\s\S]*?(?:(?<!\x60)(?P=ticks)(?!\x60)|\Z)"
    r"|\\\((?P<inline>[\s\S]*?)\\\)"
    r"|\\\[(?P<display>[\s\S]*?)\\\]"
    r"|(?<!\\)\$\$(?P<block>[\s\S]*?)\$\$"
    r"|(?<![\\$])\$(?![\s$])(?P<dollar>[^\n$\x60]*?[^\s$\x60])\$(?![\d$])"
)
_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "Sigma": "Σ", "phi": "φ", "omega": "ω", "Omega": "Ω",
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "approx": "≈",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "infty": "∞", "sum": "∑", "int": "∫", "partial": "∂", "nabla": "∇",
    "to": "→", "rightarrow": "→", "circ": "°", "quad": " ", "qquad": " ",
    "left": "", "right": "", "sin": "sin", "cos": "cos", "tan": "tan", "ln": "ln",
}
_DIGITS = "0123456789+-"
_SUPER = str.maketrans(_DIGITS, "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
_SUB = str.maketrans(_DIGITS, "₀₁₂₃₄₅₆₇₈₉₊₋")
_GROUP = r"\{([^{}\\]+)\}"
_FRACTION = re.compile(r"\\(?:frac|dfrac|tfrac)\s*" + _GROUP + r"\s*" + _GROUP)
_UNARY = re.compile(r"\\(vec|sqrt|text|mathrm|mathbf|operatorname)(?![a-zA-Z])\s*(?:"
                    + _GROUP + r"|([^\s{}\\]))")
_SCRIPT = re.compile(r"([_^])\s*(?:" + _GROUP + r"|([\w+-]))")


def _operand(value: str) -> str:
    return value if re.fullmatch(r"[\w.\u20d7⁰-⁹₀-₉]+", value) else f"({value})"


def _fraction(match: re.Match) -> str:
    top, bottom = match.groups()
    value = f"{_operand(top)}/{_operand(bottom)}"
    # Juxtaposition and powers bind differently to a slash than to a stacked fraction.
    left = match.string[:match.start()].rstrip()
    right = match.string[match.end():].lstrip()
    if (left and left[-1] not in "=+−-({") or (right and right[0] not in "=+−-)}"):
        return f"({value})"
    return value


def _unary(match: re.Match) -> str:
    name, group, token = match.groups()
    value = group or token
    if name == "vec":
        return value + "\u20d7" if len(value) == 1 else f"vec({value})"
    if name == "sqrt":
        return f"√({value})"
    return value


def _script(match: re.Match) -> str:
    marker, group, token = match.groups()
    value = group or token
    if all(c in _DIGITS for c in value):
        return value.translate(_SUPER if marker == "^" else _SUB)
    if group and len(value) > 1 and (marker == "^" or not value.isalpha()):
        return f"{marker}({value})"
    return marker + value


def _plain_span(match: re.Match) -> str:
    if match.group("ticks"):
        return match[0]
    formula = match[match.lastgroup].strip()
    formula = re.sub(r"\\([A-Za-z]+)", lambda m: _SYMBOLS.get(m[1], m[0]), formula)
    formula = re.sub(r"\\[,;: ]", " ", formula)
    for _ in range(24):
        previous = formula
        formula = _UNARY.sub(_unary, formula)
        formula = _SCRIPT.sub(_script, formula)
        formula = _FRACTION.sub(_fraction, formula)
        if formula == previous:
            break
    # Keep unfamiliar constructs intact instead of silently changing their meaning.
    if not formula or any(c in formula for c in "\\{}"):
        return match[0]
    return formula


def telegram_math(text: str) -> str:
    return _SPANS.sub(_plain_span, text)

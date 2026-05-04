import re
from typing import Optional

# ---------------------------------------------------------------------------
# Post-level patterns
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"^CISSP\s+Question\s+(?:of\s+the\s+Day)?[^\n]*\n+",
    re.IGNORECASE,
)

_DOMAIN_LINE_RE = re.compile(
    r"^Domain\s*:\s*(.+?)[\n$]",
    re.IGNORECASE | re.MULTILINE,
)

_OPTION_BLOCK_RE = re.compile(
    r"^([A-D])[\.\)]\s+(.+?)(?=\n[A-D][\.\)]|\n\n|\n#|\Z)",
    re.MULTILINE | re.DOTALL,
)

_HASHTAG_RE = re.compile(r"#(\w+)")

# ---------------------------------------------------------------------------
# Answer-comment patterns
# ---------------------------------------------------------------------------

_ANSWER_LETTER_RE = re.compile(
    r"(?:the\s+)?(?:correct\s+)?answer(?:\s+is)?\s*[:\-–]?\s*([A-D])\b",
    re.IGNORECASE,
)

_OPTION_START_RE = re.compile(r"^[A-D][\.\)]\s+", re.MULTILINE)


def parse_post(raw_text: str) -> dict:
    """
    Parse a raw LinkedIn post into structured CISSP question fields.
    Returns a dict with keys: question_text, option_a..d, domain_raw,
    question_type, hashtags. Any unextractable field is None.
    """
    result: dict = {
        "question_text": None,
        "option_a": None,
        "option_b": None,
        "option_c": None,
        "option_d": None,
        "domain_raw": None,
        "question_type": None,
        "hashtags": [],
    }

    # Strip header line(s)
    body = _HEADER_RE.sub("", raw_text, count=1).strip()

    # Domain line
    dm = _DOMAIN_LINE_RE.search(body)
    if dm:
        result["domain_raw"] = dm.group(1).strip()
        body = body[: dm.start()] + body[dm.end():]

    # Hashtags
    result["hashtags"] = _HASHTAG_RE.findall(body)

    # Split question body from options
    option_match = _OPTION_START_RE.search(body)
    if option_match:
        question_part = body[: option_match.start()].strip()
        options_part = body[option_match.start():]
    else:
        question_part = body.strip()
        options_part = ""

    # Remove trailing hashtag block from question
    question_part = re.sub(r"\s*#\w+.*$", "", question_part, flags=re.DOTALL).strip()
    result["question_text"] = question_part or None

    # Parse options A–D
    option_map = {"A": "option_a", "B": "option_b", "C": "option_c", "D": "option_d"}
    for letter, text in _OPTION_BLOCK_RE.findall(options_part):
        key = option_map.get(letter.upper())
        if key:
            result[key] = text.strip()

    result["question_type"] = _detect_question_type(question_part)
    return result


def parse_answer_comment(raw_text: str) -> dict:
    """
    Parse Adam Gordon's answer comment into correct_answer and explanation.
    """
    result: dict = {"correct_answer": None, "explanation": None}

    m = _ANSWER_LETTER_RE.search(raw_text)
    if m:
        result["correct_answer"] = m.group(1).upper()
        # Explanation is everything after the answer line
        after = raw_text[m.end():].strip()
        # Strip leading punctuation/dash
        after = re.sub(r"^[\s\-–:]+", "", after).strip()
        # Remove trailing hashtag lines
        after = re.sub(r"\s*#\w+.*$", "", after, flags=re.DOTALL).strip()
        result["explanation"] = after or None

    return result


def _detect_question_type(text: Optional[str]) -> str:
    if not text:
        return "mcq"
    lower = text.lower()
    scenario_signals = [
        "you are a", "you are the", "as the ciso", "as a security",
        "a company", "an organization", "your organization", "recently",
        "scenario", "during an audit", "while reviewing",
    ]
    if any(s in lower for s in scenario_signals):
        return "scenario"
    if len(text.split()) < 30:
        return "concept"
    return "mcq"

import json
import logging
import re
import time
from typing import Optional

from . import config

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"

_SYSTEM_SCENARIO = """You are a CISSP exam question author with 15 years of experience writing \
questions for (ISC)² examinations. Generate realistic, challenging scenario-based CISSP exam \
questions that test applied knowledge rather than memorization.

Format your response as a JSON array. Each element must match this schema exactly:
{
  "question_text": "string",
  "option_a": "string",
  "option_b": "string",
  "option_c": "string",
  "option_d": "string",
  "correct_answer": "A or B or C or D",
  "explanation": "string"
}

Rules:
- Every question must open with a realistic workplace scenario (3-5 sentences of context)
- All four options must be plausible; avoid obviously wrong distractors
- Distractors should represent common misconceptions or partially-correct approaches
- Explanation must be 2-4 sentences explaining why the correct answer is right and why each \
distractor fails
- Do NOT copy questions from known study guides verbatim; create original scenarios
- Do NOT wrap the JSON in markdown code fences"""

_SYSTEM_MCQ = """You are a CISSP exam question author. Generate concise MCQ-style questions \
that test conceptual knowledge and definitional understanding at the CISSP level.

Format your response as a JSON array. Each element must match this schema exactly:
{
  "question_text": "string",
  "option_a": "string",
  "option_b": "string",
  "option_c": "string",
  "option_d": "string",
  "correct_answer": "A or B or C or D",
  "explanation": "string"
}

Rules:
- Question stem must be unambiguous and complete on its own
- Use EXCEPT or BEST formats sparingly (under 20% of questions)
- All four options must be parallel in grammatical structure and similar length
- Do NOT use "all of the above" or "none of the above"
- Explanation must clearly justify the correct answer and briefly dismiss the distractors
- Do NOT wrap the JSON in markdown code fences"""


def generate_questions(
    domain: str,
    count: int = 5,
    style: str = "both",
    model: str = _DEFAULT_MODEL,
) -> list[dict]:
    """
    Generate new CISSP questions for a domain using the Claude API.
    Returns a list of dicts ready for db.upsert_question().
    Raises MissingAPIKeyError if key is not configured.
    """
    api_key = config.require_anthropic_key()

    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    styles = ["scenario", "mcq"] if style == "both" else [style]
    results: list[dict] = []

    for s in styles:
        system_prompt = _SYSTEM_SCENARIO if s == "scenario" else _SYSTEM_MCQ
        user_prompt = (
            f"Generate {count} {'scenario-based' if s == 'scenario' else 'MCQ-style'} "
            f"CISSP exam question(s) for the domain: \"{domain}\".\n\n"
            f"Return a JSON array of exactly {count} question object(s)."
        )

        raw = _call_with_retry(client, model, system_prompt, user_prompt)
        if raw is None:
            logger.warning("Skipping %s/%s — no valid response after retries.", domain, s)
            continue

        questions = _parse_json_response(raw)
        if questions is None:
            logger.warning("Skipping %s/%s — could not parse JSON.", domain, s)
            continue

        source_key = f"generated_{s}"
        for q in questions:
            q["source"] = source_key
            q["domain"] = domain
            q["question_type"] = s
            # Normalise correct_answer to single uppercase letter
            ca = str(q.get("correct_answer", "")).strip().upper()
            q["correct_answer"] = ca[0] if ca else None
            results.append(q)

    return results


def _call_with_retry(
    client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_retries: int = 3,
) -> Optional[str]:
    import anthropic

    delay = 5.0
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                logger.warning("Rate limited. Retrying in %.0fs...", delay)
                time.sleep(delay)
                delay *= 2
            else:
                logger.error("Rate limit exceeded after %d retries.", max_retries)
        except anthropic.APIError as exc:
            logger.error("Anthropic API error: %s", exc)
            break
    return None


def _parse_json_response(raw: str) -> Optional[list[dict]]:
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Find the first [ ... ] array
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        logger.debug("No JSON array found in response: %s", cleaned[:200])
        return None

    try:
        data = json.loads(cleaned[start : end + 1])
        if isinstance(data, list):
            return data
    except json.JSONDecodeError as exc:
        logger.debug("JSON parse error: %s | raw: %s", exc, cleaned[:200])
    return None

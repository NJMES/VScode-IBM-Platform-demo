import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cissp_scraper.parser import parse_post, parse_answer_comment, _detect_question_type

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class TestParsePost:
    def test_question_text_extracted(self):
        raw = _load("sample_post.txt")
        result = parse_post(raw)
        assert result["question_text"] is not None
        assert "federated identity" in result["question_text"]

    def test_all_four_options_extracted(self):
        raw = _load("sample_post.txt")
        result = parse_post(raw)
        assert result["option_a"] is not None
        assert result["option_b"] is not None
        assert result["option_c"] is not None
        assert result["option_d"] is not None

    def test_option_content_correct(self):
        raw = _load("sample_post.txt")
        result = parse_post(raw)
        assert "PKCE" in result["option_a"]
        assert "SAML" in result["option_b"]
        assert "OpenID" in result["option_c"]
        assert "Kerberos" in result["option_d"]

    def test_domain_raw_extracted(self):
        raw = _load("sample_post.txt")
        result = parse_post(raw)
        assert result["domain_raw"] is not None
        assert "Identity" in result["domain_raw"]

    def test_hashtags_extracted(self):
        raw = _load("sample_post.txt")
        result = parse_post(raw)
        assert "CISSP" in result["hashtags"]
        assert "ISC2" in result["hashtags"]

    def test_no_raise_on_malformed_post(self):
        result = parse_post("Some random text with no structure at all.")
        assert isinstance(result, dict)
        assert result["question_text"] is not None or result["question_text"] is None

    def test_question_type_detected(self):
        raw = _load("sample_post.txt")
        result = parse_post(raw)
        assert result["question_type"] in ("mcq", "scenario", "concept")

    def test_scenario_detection(self):
        text = "You are a security engineer at a large enterprise. Your organization recently..."
        assert _detect_question_type(text) == "scenario"

    def test_concept_detection(self):
        text = "Which of the following best defines due diligence?"
        assert _detect_question_type(text) == "concept"


class TestParseAnswerComment:
    def test_correct_answer_extracted(self):
        raw = _load("sample_comment.txt")
        result = parse_answer_comment(raw)
        assert result["correct_answer"] == "C"

    def test_explanation_extracted(self):
        raw = _load("sample_comment.txt")
        result = parse_answer_comment(raw)
        assert result["explanation"] is not None
        assert "token binding" in result["explanation"]

    def test_no_hashtags_in_explanation(self):
        raw = _load("sample_comment.txt")
        result = parse_answer_comment(raw)
        assert "#" not in (result["explanation"] or "")

    def test_answer_formats(self):
        variants = [
            ("Answer: B\n\nExplanation here.", "B"),
            ("The correct answer is A\n\nReason.", "A"),
            ("Correct Answer: D\n\nBecause...", "D"),
            ("The answer is C — explanation.", "C"),
        ]
        for text, expected in variants:
            result = parse_answer_comment(text)
            assert result["correct_answer"] == expected, f"Failed for: {text}"

    def test_no_answer_returns_none(self):
        result = parse_answer_comment("Great question everyone!")
        assert result["correct_answer"] is None

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cissp_scraper.db import init_db, upsert_question, get_questions, get_questions_missing_answer
from cissp_scraper import config


@contextmanager
def _in_memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_SCRAPED = {
    "source": "scraped",
    "linkedin_post_url": "https://www.linkedin.com/feed/update/urn:li:activity:12345/",
    "question_text": "What is the primary purpose of a reference monitor?",
    "option_a": "Logging",
    "option_b": "Mediate all access requests",
    "option_c": "Encrypting data",
    "option_d": "Auditing users",
    "correct_answer": "B",
    "explanation": "A reference monitor mediates all access requests.",
    "domain": "Security Architecture & Engineering",
    "question_type": "mcq",
}

_GENERATED = {
    "source": "generated_scenario",
    "question_text": "You are the CISO. An attacker has...",
    "option_a": "Isolate the system",
    "option_b": "Notify law enforcement",
    "option_c": "Preserve evidence",
    "option_d": "Rebuild the server",
    "correct_answer": "C",
    "explanation": "Preservation of evidence is the first priority.",
    "domain": "Security Operations",
    "question_type": "scenario",
}


class TestInitDb:
    def test_idempotent(self):
        with _in_memory_conn() as conn:
            init_db(conn)  # second call must not raise
            count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            assert count == 0


class TestUpsertQuestion:
    def test_insert_scraped(self):
        with _in_memory_conn() as conn:
            row_id, action = upsert_question(conn, _SCRAPED)
            assert action == "inserted"
            assert row_id > 0

    def test_scraped_defaults_to_validated(self):
        with _in_memory_conn() as conn:
            row_id, _ = upsert_question(conn, _SCRAPED)
            row = conn.execute("SELECT review_status FROM questions WHERE id=?", (row_id,)).fetchone()
            assert row["review_status"] == config.REVIEW_STATUS_VALIDATED

    def test_generated_defaults_to_pending(self):
        with _in_memory_conn() as conn:
            row_id, _ = upsert_question(conn, _GENERATED)
            row = conn.execute("SELECT review_status FROM questions WHERE id=?", (row_id,)).fetchone()
            assert row["review_status"] == config.REVIEW_STATUS_PENDING

    def test_duplicate_url_skipped(self):
        with _in_memory_conn() as conn:
            upsert_question(conn, _SCRAPED)
            _, action = upsert_question(conn, _SCRAPED)
            assert action == "skipped"
            count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            assert count == 1

    def test_fill_answer_updates(self):
        with _in_memory_conn() as conn:
            no_answer = {**_SCRAPED, "correct_answer": None, "explanation": None}
            row_id, _ = upsert_question(conn, no_answer)
            row = conn.execute("SELECT correct_answer FROM questions WHERE id=?", (row_id,)).fetchone()
            assert row["correct_answer"] is None

            _, action = upsert_question(conn, {
                **_SCRAPED,
                "correct_answer": "B",
                "explanation": "Explanation here",
            })
            assert action == "updated"
            row = conn.execute("SELECT correct_answer FROM questions WHERE id=?", (row_id,)).fetchone()
            assert row["correct_answer"] == "B"

    def test_existing_answer_not_overwritten(self):
        with _in_memory_conn() as conn:
            upsert_question(conn, _SCRAPED)  # correct_answer = B
            _, action = upsert_question(conn, {**_SCRAPED, "correct_answer": "A"})
            assert action == "skipped"
            row = conn.execute(
                "SELECT correct_answer FROM questions WHERE linkedin_post_url=?",
                (_SCRAPED["linkedin_post_url"],),
            ).fetchone()
            assert row["correct_answer"] == "B"

    def test_generated_always_inserts(self):
        with _in_memory_conn() as conn:
            upsert_question(conn, _GENERATED)
            upsert_question(conn, _GENERATED)
            count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            assert count == 2

    def test_get_questions_missing_answer(self):
        with _in_memory_conn() as conn:
            no_ans = {**_SCRAPED, "correct_answer": None}
            upsert_question(conn, no_ans)
            upsert_question(conn, _GENERATED)
            missing = get_questions_missing_answer(conn)
            assert len(missing) == 1
            assert missing[0]["question_text"] == _SCRAPED["question_text"]

    def test_get_questions_filter_source(self):
        with _in_memory_conn() as conn:
            upsert_question(conn, _SCRAPED)
            upsert_question(conn, _GENERATED)
            scraped_only = get_questions(conn, source="scraped")
            assert len(scraped_only) == 1
            assert scraped_only[0]["source"] == "scraped"

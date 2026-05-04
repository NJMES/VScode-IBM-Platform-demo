import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL DEFAULT 'scraped',
    review_status     TEXT NOT NULL DEFAULT 'validated',
    linkedin_post_url TEXT UNIQUE,
    post_scraped_at   TEXT,
    question_text     TEXT NOT NULL,
    option_a          TEXT,
    option_b          TEXT,
    option_c          TEXT,
    option_d          TEXT,
    correct_answer    TEXT,
    explanation       TEXT,
    domain            TEXT,
    domain_confidence TEXT,
    question_type     TEXT,
    raw_post_text     TEXT,
    raw_answer_text   TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now','utc')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','utc'))
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    posts_visited     INTEGER DEFAULT 0,
    questions_added   INTEGER DEFAULT 0,
    questions_updated INTEGER DEFAULT 0,
    errors            TEXT,
    status            TEXT
);

CREATE INDEX IF NOT EXISTS idx_questions_domain ON questions(domain);
CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source);
CREATE INDEX IF NOT EXISTS idx_questions_review  ON questions(review_status);
"""


@contextmanager
def get_connection(db_path: Optional[Path] = None):
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_question(conn: sqlite3.Connection, data: dict) -> tuple[int, str]:
    """Insert or update a question. Returns (id, 'inserted'|'updated'|'skipped')."""
    url = data.get("linkedin_post_url")

    if url:
        existing = conn.execute(
            "SELECT id, correct_answer FROM questions WHERE linkedin_post_url = ?",
            (url,),
        ).fetchone()

        if existing:
            row_id = existing["id"]
            # Only update if we're filling in a missing answer
            new_answer = data.get("correct_answer")
            new_explanation = data.get("explanation")
            if new_answer and not existing["correct_answer"]:
                conn.execute(
                    """UPDATE questions
                       SET correct_answer = ?, explanation = ?,
                           raw_answer_text = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_answer, new_explanation, data.get("raw_answer_text"), _now_utc(), row_id),
                )
                return row_id, "updated"
            return row_id, "skipped"

    review_status = (
        config.REVIEW_STATUS_PENDING
        if data.get("source", "scraped") != "scraped"
        else config.REVIEW_STATUS_VALIDATED
    )

    cursor = conn.execute(
        """INSERT OR IGNORE INTO questions
           (source, review_status, linkedin_post_url, post_scraped_at,
            question_text, option_a, option_b, option_c, option_d,
            correct_answer, explanation, domain, domain_confidence,
            question_type, raw_post_text, raw_answer_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data.get("source", "scraped"),
            review_status,
            url,
            data.get("post_scraped_at"),
            data["question_text"],
            data.get("option_a"),
            data.get("option_b"),
            data.get("option_c"),
            data.get("option_d"),
            data.get("correct_answer"),
            data.get("explanation"),
            data.get("domain"),
            data.get("domain_confidence"),
            data.get("question_type"),
            data.get("raw_post_text"),
            data.get("raw_answer_text"),
        ),
    )
    return cursor.lastrowid or 0, "inserted"


def get_questions(
    conn: sqlite3.Connection,
    domain: Optional[str] = None,
    source: Optional[str] = None,
    include_no_answer: bool = False,
    review_status: Optional[str] = None,
) -> list[sqlite3.Row]:
    clauses = []
    params: list = []

    if domain:
        clauses.append("domain = ?")
        params.append(domain)
    if source and source != "all":
        if source == "generated":
            clauses.append("source != 'scraped'")
        else:
            clauses.append("source = ?")
            params.append(source)
    if not include_no_answer:
        clauses.append("(correct_answer IS NOT NULL OR source != 'scraped')")
    if review_status:
        clauses.append("review_status = ?")
        params.append(review_status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(
        f"SELECT * FROM questions {where} ORDER BY domain, id",
        params,
    ).fetchall()


def get_questions_missing_answer(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM questions WHERE source = 'scraped' AND correct_answer IS NULL"
    ).fetchall()


def start_scrape_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')",
        (_now_utc(),),
    )
    conn.commit()
    return cur.lastrowid


def finish_scrape_run(
    conn: sqlite3.Connection,
    run_id: int,
    posts_visited: int,
    questions_added: int,
    questions_updated: int,
    errors: list[str],
    status: str = "completed",
) -> None:
    import json
    conn.execute(
        """UPDATE scrape_runs
           SET finished_at=?, posts_visited=?, questions_added=?,
               questions_updated=?, errors=?, status=?
           WHERE id=?""",
        (
            _now_utc(),
            posts_visited,
            questions_added,
            questions_updated,
            json.dumps(errors) if errors else None,
            status,
            run_id,
        ),
    )


def get_stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    scraped = conn.execute("SELECT COUNT(*) FROM questions WHERE source='scraped'").fetchone()[0]
    generated = total - scraped
    pending = conn.execute(
        f"SELECT COUNT(*) FROM questions WHERE review_status='{config.REVIEW_STATUS_PENDING}'"
    ).fetchone()[0]
    missing_answer = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE source='scraped' AND correct_answer IS NULL"
    ).fetchone()[0]

    domain_rows = conn.execute(
        """SELECT domain, source, COUNT(*) as cnt
           FROM questions GROUP BY domain, source ORDER BY domain"""
    ).fetchall()

    last_run = conn.execute(
        "SELECT finished_at, questions_added, questions_updated FROM scrape_runs "
        "WHERE status='completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return {
        "total": total,
        "scraped": scraped,
        "generated": generated,
        "pending_review": pending,
        "missing_answer": missing_answer,
        "by_domain": [dict(r) for r in domain_rows],
        "last_run": dict(last_run) if last_run else None,
    }

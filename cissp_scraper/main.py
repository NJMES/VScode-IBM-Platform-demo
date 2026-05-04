#!/usr/bin/env python3
"""
CISSP Question Scraper & Generator
Usage: python main.py <command> [options]
Run `python main.py --help` for full usage.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cissp_scraper import config
from cissp_scraper.db import get_connection, init_db, get_questions, get_stats


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

def cmd_scrape(args) -> None:
    _setup_logging(args.verbose)
    from cissp_scraper.scraper import LinkedInScraper
    from cissp_scraper.db import start_scrape_run, finish_scrape_run

    scraper = LinkedInScraper(
        headless=args.headless,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        verbose=args.verbose,
    )

    with get_connection() as conn:
        init_db(conn)

        if args.fill_answers:
            print("Re-visiting posts with missing answers...")
            updated, errors = scraper.fill_missing_answers(conn, dry_run=args.dry_run)
            print(f"Updated {updated} question(s) with answers.")
            if errors:
                print(f"Errors: {len(errors)}")
            return

        if args.reparse:
            _reparse_existing(conn)
            return

        run_id = start_scrape_run(conn)
        print(f"Starting scrape (max {args.max_posts} posts)...")

        visited, added, updated, errors = scraper.scrape_posts(
            db_conn=conn,
            max_posts=args.max_posts,
            dry_run=args.dry_run,
            since_date=args.since,
        )

        finish_scrape_run(conn, run_id, visited, added, updated, errors)

        tag = "[dry-run] " if args.dry_run else ""
        print(
            f"{tag}Done. Posts visited: {visited} | "
            f"Added: {added} | Updated: {updated} | Errors: {len(errors)}"
        )
        if errors:
            for e in errors[:5]:
                print(f"  {e}")


def _reparse_existing(conn) -> None:
    from cissp_scraper.parser import parse_post, parse_answer_comment
    from cissp_scraper.domain_classifier import classify_domain

    rows = conn.execute(
        "SELECT id, raw_post_text, raw_answer_text FROM questions WHERE source='scraped'"
    ).fetchall()

    updated = 0
    for row in rows:
        raw_post = row["raw_post_text"]
        raw_answer = row["raw_answer_text"]
        if not raw_post:
            continue
        parsed = parse_post(raw_post)
        answer = parse_answer_comment(raw_answer) if raw_answer else {}
        options_text = " ".join(filter(None, [
            parsed.get("option_a"), parsed.get("option_b"),
            parsed.get("option_c"), parsed.get("option_d"),
        ]))
        domain, confidence = classify_domain(
            parsed.get("question_text", ""), options_text, parsed.get("hashtags", [])
        )
        conn.execute(
            """UPDATE questions
               SET question_text=?, option_a=?, option_b=?, option_c=?, option_d=?,
                   correct_answer=COALESCE(correct_answer, ?),
                   explanation=COALESCE(explanation, ?),
                   domain=COALESCE(domain, ?), domain_confidence=COALESCE(domain_confidence, ?)
               WHERE id=?""",
            (
                parsed.get("question_text"), parsed.get("option_a"), parsed.get("option_b"),
                parsed.get("option_c"), parsed.get("option_d"),
                answer.get("correct_answer"), answer.get("explanation"),
                domain, confidence, row["id"],
            ),
        )
        updated += 1

    print(f"Reparsed {updated} scraped question(s).")


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def cmd_generate(args) -> None:
    _setup_logging(args.verbose)

    try:
        from cissp_scraper.generator import generate_questions
    except Exception:
        pass

    try:
        config.require_anthropic_key()
    except config.MissingAPIKeyError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    from cissp_scraper.generator import generate_questions
    from cissp_scraper.db import upsert_question

    domains = args.domains or list(config.CISSP_DOMAINS)
    total_saved = 0

    with get_connection() as conn:
        init_db(conn)
        for domain in domains:
            # Partial match against canonical domain names
            matched = _match_domain(domain)
            if not matched:
                print(f"WARNING: Unknown domain '{domain}'. Skipping.")
                continue

            print(f"Generating for: {matched} (count={args.count}, style={args.style})...")
            questions = generate_questions(
                domain=matched,
                count=args.count,
                style=args.style,
                model=args.model,
            )

            if args.dry_run:
                for q in questions:
                    print(f"\n[{q.get('source')} | {q.get('domain')}]")
                    print(f"Q: {q.get('question_text', '')[:120]}")
                    print(f"A: {q.get('correct_answer')} — {(q.get('explanation') or '')[:80]}")
            else:
                for q in questions:
                    _, action = upsert_question(conn, q)
                    if action == "inserted":
                        total_saved += 1

    if not args.dry_run:
        print(f"\nDone. {total_saved} question(s) saved to database.")


def _match_domain(partial: str) -> str | None:
    lower = partial.lower()
    for d in config.CISSP_DOMAINS:
        if lower in d.lower():
            return d
    return None


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def cmd_export(args) -> None:
    _setup_logging()
    from cissp_scraper.exporter import export_csv, export_excel, export_both

    with get_connection() as conn:
        init_db(conn)
        rows = get_questions(
            conn,
            domain=args.domain,
            source=args.source,
            include_no_answer=args.include_no_answer,
        )

    if not rows:
        print("No questions found matching the given filters.")
        return

    out_dir = args.output_dir
    prefix = args.filename_prefix

    if args.format == "csv":
        path = export_csv(rows, out_dir, prefix)
        print(f"CSV exported: {path}")
    elif args.format == "excel":
        path = export_excel(rows, out_dir, prefix)
        print(f"Excel exported: {path}")
    else:
        csv_path, xlsx_path = export_both(rows, out_dir, prefix)
        print(f"CSV exported:   {csv_path}")
        print(f"Excel exported: {xlsx_path}")

    print(f"Total rows: {len(rows)}")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def cmd_stats(args) -> None:
    _setup_logging()
    with get_connection() as conn:
        init_db(conn)
        stats = get_stats(conn)

    if args.as_json:
        print(json.dumps(stats, indent=2))
        return

    print("\nCISSP Question Bank Statistics")
    print("=" * 40)
    print(f"Total questions : {stats['total']}")
    print(f"  Scraped       : {stats['scraped']}")
    print(f"  Generated     : {stats['generated']}")
    print(f"  Pending review: {stats['pending_review']}")
    print(f"  Missing answer: {stats['missing_answer']}")

    if stats["by_domain"]:
        print("\nBy Domain:")
        domain_map: dict[str, dict] = {}
        for entry in stats["by_domain"]:
            d = entry["domain"] or "(unclassified)"
            if d not in domain_map:
                domain_map[d] = {"scraped": 0, "generated": 0}
            if entry["source"] == "scraped":
                domain_map[d]["scraped"] += entry["cnt"]
            else:
                domain_map[d]["generated"] += entry["cnt"]

        for domain, counts in sorted(domain_map.items()):
            print(f"  {domain:<45} {counts['scraped']:>4} scraped / {counts['generated']:>4} generated")

    if stats["last_run"]:
        lr = stats["last_run"]
        print(f"\nLast scrape run : {lr['finished_at']}  "
              f"(+{lr['questions_added']} added, {lr['questions_updated']} updated)")
    print()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="CISSP Question Scraper, Generator & Exporter",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- scrape ---
    sp = sub.add_parser("scrape", help="Scrape LinkedIn for CISSP questions")
    sp.add_argument("--max-posts", type=int, default=50, metavar="N",
                    help="Maximum posts to process (default: 50)")
    sp.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True,
                    help="Run browser headless (default: headless). Use --no-headless / --headed for visible.")
    # Alias --headed → --no-headless
    sp.add_argument("--headed", dest="headless", action="store_false",
                    help="Run browser in headed/visible mode (same as --no-headless)")
    sp.add_argument("--delay-min", type=float, default=2.0, metavar="SECS")
    sp.add_argument("--delay-max", type=float, default=5.0, metavar="SECS")
    sp.add_argument("--dry-run", action="store_true", help="Parse but don't write to DB")
    sp.add_argument("--since", metavar="YYYY-MM-DD", help="Only process posts on or after this date")
    sp.add_argument("--reparse", action="store_true",
                    help="Re-parse stored raw_post_text without re-scraping LinkedIn")
    sp.add_argument("--fill-answers", action="store_true",
                    help="Re-visit posts where correct_answer is missing")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_scrape)

    # --- generate ---
    gp = sub.add_parser("generate", help="Generate new questions via Claude API")
    gp.add_argument("--domain", action="append", dest="domains", metavar="DOMAIN",
                    help="CISSP domain (partial match OK, repeatable). Omit for all 8 domains.")
    gp.add_argument("--count", type=int, default=5, metavar="N",
                    help="Questions per domain per style (default: 5)")
    gp.add_argument("--style", choices=["scenario", "mcq", "both"], default="both",
                    help="Question style (default: both)")
    gp.add_argument("--model", default="claude-sonnet-4-6",
                    help="Claude model ID (default: claude-sonnet-4-6)")
    gp.add_argument("--dry-run", action="store_true",
                    help="Print questions to stdout without saving")
    gp.add_argument("-v", "--verbose", action="store_true")
    gp.set_defaults(func=cmd_generate)

    # --- export ---
    ep = sub.add_parser("export", help="Export question bank to CSV and/or Excel")
    ep.add_argument("--format", choices=["csv", "excel", "both"], default="both")
    ep.add_argument("--output-dir", type=Path, default=config.EXPORTS_DIR, metavar="DIR")
    ep.add_argument("--filename-prefix", default="cissp_questions", metavar="PREFIX")
    ep.add_argument("--domain", help="Filter by CISSP domain")
    ep.add_argument("--source", choices=["scraped", "generated", "all"], default="all")
    ep.add_argument("--include-no-answer", action="store_true",
                    help="Include scraped questions without a confirmed answer")
    ep.set_defaults(func=cmd_export)

    # --- stats ---
    stp = sub.add_parser("stats", help="Show question bank statistics")
    stp.add_argument("--domain", help="Filter stats to a specific domain")
    stp.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")
    stp.set_defaults(func=cmd_stats)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

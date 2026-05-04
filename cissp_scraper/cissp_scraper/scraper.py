import random
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout

from . import config
from .parser import parse_post, parse_answer_comment
from .domain_classifier import classify_domain

logger = logging.getLogger(__name__)

_CISSP_KEYWORDS = {"cissp", "cisspsuccess", "isc2", "question of the day", "cissp question"}


class LinkedInScraper:
    def __init__(
        self,
        headless: bool = True,
        delay_min: float = 2.0,
        delay_max: float = 5.0,
        verbose: bool = False,
    ):
        self.headless = headless
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.verbose = verbose
        self._context_dir = config.BROWSER_CONTEXT_DIR
        self._context_dir.mkdir(parents=True, exist_ok=True)
        self._session_path = str(self._context_dir / "session.json")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrape_posts(
        self,
        db_conn,
        max_posts: int = 50,
        dry_run: bool = False,
        since_date: Optional[str] = None,
    ) -> tuple[int, int, int, list[str]]:
        """
        Scrape Adam Gordon's LinkedIn activity for CISSP questions.
        Returns (posts_visited, questions_added, questions_updated, errors).
        """
        from .db import upsert_question

        email, password = config.require_linkedin_creds()
        posts_visited = 0
        questions_added = 0
        questions_updated = 0
        errors: list[str] = []

        with sync_playwright() as pw:
            context, page = self._launch(pw)
            try:
                self._login_if_needed(page, email, password)
                self._navigate(page, config.ADAM_GORDON_URL)

                post_data_list = self._collect_posts(page, max_posts, since_date)

                for post_url, raw_text in post_data_list:
                    posts_visited += 1
                    if self.verbose:
                        logger.info("Processing: %s", post_url)

                    try:
                        parsed = parse_post(raw_text)
                        if not parsed.get("question_text"):
                            continue

                        options_text = " ".join(
                            filter(None, [
                                parsed.get("option_a"), parsed.get("option_b"),
                                parsed.get("option_c"), parsed.get("option_d"),
                            ])
                        )
                        domain, domain_confidence = classify_domain(
                            parsed["question_text"],
                            options_text,
                            parsed.get("hashtags", []),
                        )

                        answer_text = self._get_answer_comment(page, post_url)
                        answer_parsed = parse_answer_comment(answer_text) if answer_text else {}

                        record = {
                            "source": "scraped",
                            "linkedin_post_url": post_url,
                            "post_scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "question_text": parsed["question_text"],
                            "option_a": parsed.get("option_a"),
                            "option_b": parsed.get("option_b"),
                            "option_c": parsed.get("option_c"),
                            "option_d": parsed.get("option_d"),
                            "correct_answer": answer_parsed.get("correct_answer"),
                            "explanation": answer_parsed.get("explanation"),
                            "domain": domain,
                            "domain_confidence": domain_confidence,
                            "question_type": parsed.get("question_type"),
                            "raw_post_text": raw_text,
                            "raw_answer_text": answer_text,
                        }

                        if not dry_run:
                            _, action = upsert_question(db_conn, record)
                            if action == "inserted":
                                questions_added += 1
                            elif action == "updated":
                                questions_updated += 1
                        else:
                            logger.info("[dry-run] Would insert: %s", parsed["question_text"][:80])

                    except Exception as exc:
                        msg = f"Error processing {post_url}: {exc}"
                        logger.warning(msg)
                        errors.append(msg)

                    self._sleep()

            finally:
                context.storage_state(path=self._session_path)
                context.close()

        return posts_visited, questions_added, questions_updated, errors

    def fill_missing_answers(self, db_conn, dry_run: bool = False) -> tuple[int, list[str]]:
        """Re-visit posts where correct_answer is NULL to find Adam's comment."""
        from .db import get_questions_missing_answer, upsert_question

        rows = get_questions_missing_answer(db_conn)
        if not rows:
            logger.info("No questions with missing answers found.")
            return 0, []

        email, password = config.require_linkedin_creds()
        updated = 0
        errors: list[str] = []

        with sync_playwright() as pw:
            context, page = self._launch(pw)
            try:
                self._login_if_needed(page, email, password)
                for row in rows:
                    url = row["linkedin_post_url"]
                    if not url:
                        continue
                    try:
                        answer_text = self._get_answer_comment(page, url)
                        if not answer_text:
                            continue
                        answer_parsed = parse_answer_comment(answer_text)
                        if not answer_parsed.get("correct_answer"):
                            continue
                        if not dry_run:
                            _, action = upsert_question(db_conn, {
                                "linkedin_post_url": url,
                                "question_text": row["question_text"],
                                "correct_answer": answer_parsed["correct_answer"],
                                "explanation": answer_parsed.get("explanation"),
                                "raw_answer_text": answer_text,
                            })
                            if action == "updated":
                                updated += 1
                    except Exception as exc:
                        errors.append(f"fill-answers error for {url}: {exc}")
                    self._sleep()
            finally:
                context.storage_state(path=self._session_path)
                context.close()

        return updated, errors

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _launch(self, pw) -> tuple[BrowserContext, Page]:
        browser = pw.chromium.launch(headless=self.headless)
        session_exists = Path(self._session_path).exists()
        if session_exists:
            context = browser.new_context(
                storage_state=self._session_path,
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
        else:
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
        page = context.new_page()
        return context, page

    def _login_if_needed(self, page: Page, email: str, password: str) -> None:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        self._sleep(0.5, 1.0)

        if "/login" in page.url or "/checkpoint" in page.url or "authwall" in page.url:
            logger.info("Logging in to LinkedIn...")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            page.fill("#username", email)
            self._sleep(0.3, 0.7)
            page.fill("#password", password)
            self._sleep(0.3, 0.7)
            page.click('[data-litms-control-urn="login-submit"], button[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=15000)

            if "/checkpoint" in page.url or "challenge" in page.url:
                print(
                    "\n[ACTION REQUIRED] LinkedIn is asking for verification.\n"
                    "Please complete the challenge in the browser window, then press Enter here."
                )
                if not self.headless:
                    input()
                else:
                    raise RuntimeError(
                        "LinkedIn checkpoint detected in headless mode. "
                        "Run with --headed to complete 2FA manually."
                    )

            logger.info("Login successful.")

    def _navigate(self, page: Page, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._sleep()
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeout:
            pass

    def _collect_posts(
        self, page: Page, max_posts: int, since_date: Optional[str]
    ) -> list[tuple[str, str]]:
        collected: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        no_new_scroll_count = 0

        while len(collected) < max_posts and no_new_scroll_count < 4:
            prev_count = len(seen_urls)

            # Multiple fallback selectors for post containers
            cards = (
                page.query_selector_all('div[data-urn*="activity"]')
                or page.query_selector_all('div[class*="feed-shared-update-v2"]')
                or page.query_selector_all('article[data-id]')
            )

            for card in cards:
                url = self._extract_post_url(card)
                if not url or url in seen_urls:
                    continue

                card_text = self._safe_inner_text(card)
                if not self._looks_like_cissp_question(card_text):
                    continue

                seen_urls.add(url)
                full_text = self._expand_and_get_text(card)
                collected.append((url, full_text))

                if len(collected) >= max_posts:
                    break

            if len(seen_urls) == prev_count:
                no_new_scroll_count += 1
            else:
                no_new_scroll_count = 0

            page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
            self._sleep(1.5, 3.0)

        return collected

    def _extract_post_url(self, card) -> Optional[str]:
        # Try data-urn attribute first
        urn = card.get_attribute("data-urn") or ""
        match = __import__("re").search(r"activity:(\d+)", urn)
        if match:
            activity_id = match.group(1)
            return f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"

        # Fallback: look for a permalink anchor
        link = card.query_selector('a[href*="/posts/"], a[href*="/feed/update/"]')
        if link:
            href = link.get_attribute("href") or ""
            if href.startswith("http"):
                return href
            return "https://www.linkedin.com" + href

        return None

    def _expand_and_get_text(self, card) -> str:
        try:
            see_more = card.query_selector(
                'button[aria-label*="see more"], button[aria-label*="more"]'
            )
            if see_more:
                see_more.click()
                time.sleep(0.5)
        except Exception:
            pass
        return self._safe_inner_text(card)

    def _safe_inner_text(self, element) -> str:
        try:
            return element.inner_text() or ""
        except Exception:
            return ""

    def _looks_like_cissp_question(self, text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in _CISSP_KEYWORDS) and (
            "a." in lower or "a)" in lower or "\na " in lower
        )

    def _get_answer_comment(self, page: Page, post_url: str) -> Optional[str]:
        self._navigate(page, post_url)

        # Expand comments section if needed
        try:
            comments_btn = page.query_selector(
                'button[aria-label*="comment"], '
                'button[data-control-name*="comment"]'
            )
            if comments_btn:
                comments_btn.click()
                self._sleep(1.5, 2.5)
        except Exception:
            pass

        # Try to load more comments
        try:
            load_more = page.query_selector('button[aria-label*="Load more comments"]')
            if load_more:
                load_more.click()
                self._sleep(1.0, 2.0)
        except Exception:
            pass

        # Find all comments and look for Adam Gordon's
        comment_selectors = [
            'article.comments-comment-item',
            'div[class*="comments-comment-entity"]',
            'div[class*="comment-item"]',
        ]

        comments = []
        for sel in comment_selectors:
            comments = page.query_selector_all(sel)
            if comments:
                break

        for comment in comments:
            try:
                # Check if commenter is Adam Gordon
                author = comment.query_selector(
                    'a[href*="adam-gordon-cissp"], '
                    'span[class*="comments-post-meta__name"]'
                )
                if author:
                    author_text = self._safe_inner_text(author).lower()
                    if "adam" in author_text or "gordon" in author_text:
                        return self._safe_inner_text(comment)

                # Also check by href
                links = comment.query_selector_all("a[href]")
                for link in links:
                    href = link.get_attribute("href") or ""
                    if "adam-gordon-cissp" in href:
                        return self._safe_inner_text(comment)
            except Exception:
                continue

        return None

    def _sleep(self, min_s: Optional[float] = None, max_s: Optional[float] = None) -> None:
        lo = min_s if min_s is not None else self.delay_min
        hi = max_s if max_s is not None else self.delay_max
        time.sleep(random.uniform(lo, hi))

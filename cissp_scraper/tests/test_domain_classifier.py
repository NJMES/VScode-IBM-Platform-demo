import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cissp_scraper.domain_classifier import classify_domain


class TestClassifyDomain:
    def test_iam_from_keywords(self):
        text = (
            "Which authentication protocol uses ticket-granting tickets and a "
            "Key Distribution Center for Kerberos-based single sign-on?"
        )
        domain, confidence = classify_domain(text, use_claude=False)
        assert domain == "Identity & Access Management"
        assert confidence == "keyword"

    def test_network_from_keywords(self):
        text = (
            "An organization wants to encrypt traffic between two sites using IPSec. "
            "Which mode provides encryption for the entire IP packet including headers?"
        )
        domain, confidence = classify_domain(text, use_claude=False)
        assert domain == "Network & Communications Security"
        assert confidence == "keyword"

    def test_sdlc_from_keywords(self):
        text = (
            "During a code review, a developer discovers an input validation flaw "
            "that could lead to SQL injection. Which SDLC phase should have caught this?"
        )
        domain, confidence = classify_domain(text, use_claude=False)
        assert domain == "Software Development Security"
        assert confidence == "keyword"

    def test_hashtag_takes_priority(self):
        text = "What is due care?"
        hashtags = ["IdentityAndAccessManagement", "IAM"]
        domain, confidence = classify_domain(text, hashtags=hashtags, use_claude=False)
        assert domain == "Identity & Access Management"
        assert confidence == "hashtag"

    def test_ambiguous_returns_none_without_claude(self):
        text = "What is the best approach?"
        domain, confidence = classify_domain(text, use_claude=False)
        assert domain is None

    def test_security_ops_keywords(self):
        text = (
            "After a ransomware incident, the forensics team must maintain chain of custody "
            "while analyzing the SIEM logs and EDR telemetry."
        )
        domain, confidence = classify_domain(text, use_claude=False)
        assert domain == "Security Operations"
        assert confidence == "keyword"

    def test_asset_security_keywords(self):
        text = (
            "A data custodian is responsible for implementing the controls defined by "
            "the data owner. Which data classification label applies to PII records?"
        )
        domain, confidence = classify_domain(text, use_claude=False)
        assert domain == "Asset Security"
        assert confidence == "keyword"

    def test_returns_tuple(self):
        domain, confidence = classify_domain("What is the OSI model?", use_claude=False)
        assert isinstance(domain, (str, type(None)))
        assert isinstance(confidence, (str, type(None)))

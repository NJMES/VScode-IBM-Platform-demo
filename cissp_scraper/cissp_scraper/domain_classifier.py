import re
from typing import Optional

from . import config

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "Security & Risk Management": [
        "risk management", "risk assessment", "risk appetite", "risk tolerance",
        "annualized loss expectancy", "ale", "aro", "sle", "exposure factor",
        "bcp", "drp", "business continuity", "disaster recovery",
        "due diligence", "due care", "policy", "governance", "compliance",
        "legal", "regulatory", "ethics", "isc2 code of ethics",
        "threat modeling", "threat agent", "vulnerability", "countermeasure",
        "quantitative risk", "qualitative risk",
    ],
    "Asset Security": [
        "data classification", "data owner", "data custodian", "data steward",
        "data handling", "data retention", "data remanence", "data destruction",
        "sanitization", "degaussing", "scoping", "tailoring",
        "marking", "labeling", "pii", "phi", "pci", "sensitivity",
        "asset management", "inventory", "baselining",
    ],
    "Security Architecture & Engineering": [
        "trusted computing", "tcb", "trusted computing base",
        "reference monitor", "security kernel", "security model",
        "bell-lapadula", "biba", "clark-wilson", "brewer-nash",
        "encryption", "cryptography", "symmetric", "asymmetric",
        "pki", "certificate", "aes", "rsa", "elliptic curve",
        "firewall", "dmz", "defense in depth", "virtualization",
        "cloud security", "common criteria", "evaluation assurance level",
        "eal", "tcsec", "rainbow series", "itsec",
    ],
    "Network & Communications Security": [
        "tcp/ip", "tcp", "udp", "osi model", "vpn", "tls", "ssl",
        "ipsec", "vlan", "routing", "switching", "firewall rule",
        "ids", "ips", "intrusion detection", "intrusion prevention",
        "wireless", "wpa", "wpa2", "802.11", "dnssec", "dns",
        "bgp", "ospf", "nat", "network protocol", "packet filtering",
        "stateful inspection", "proxy", "network segmentation",
    ],
    "Identity & Access Management": [
        "authentication", "authorization", "mfa", "multi-factor",
        "sso", "single sign-on", "oauth", "saml", "openid",
        "rbac", "role-based", "dac", "discretionary", "mac",
        "mandatory access control", "privileged access", "pam",
        "identity federation", "provisioning", "deprovisioning",
        "kerberos", "ldap", "active directory", "biometric",
        "password", "credential", "zero trust",
    ],
    "Security Assessment & Testing": [
        "penetration testing", "pentest", "vulnerability assessment",
        "vulnerability scan", "audit", "security audit", "code review",
        "fuzzing", "fuzz testing", "static analysis", "dynamic analysis",
        "sast", "dast", "red team", "blue team", "purple team",
        "security testing", "nessus", "burp suite", "metasploit",
        "cvss", "cve", "bug bounty", "reconnaissance",
    ],
    "Security Operations": [
        "siem", "incident response", "forensics", "chain of custody",
        "patch management", "change management", "monitoring",
        "log analysis", "log management", "ticketing", "edr",
        "soar", "noc", "soc", "security operations center",
        "mean time to detect", "mttd", "mttr", "playbook",
        "malware", "ransomware", "threat hunting", "ioc",
    ],
    "Software Development Security": [
        "sdlc", "software development lifecycle", "devops", "devsecops",
        "owasp", "injection", "sql injection", "xss", "csrf",
        "buffer overflow", "secure coding", "code review",
        "threat modeling", "software testing", "agile", "scrum",
        "api security", "input validation", "output encoding",
        "dependency management", "software composition analysis",
    ],
}

_HASHTAG_DOMAIN_MAP: dict[str, str] = {
    "securityriskmanagement": "Security & Risk Management",
    "riskmanagement": "Security & Risk Management",
    "assetsecurity": "Asset Security",
    "securityarchitecture": "Security Architecture & Engineering",
    "cryptography": "Security Architecture & Engineering",
    "networksecurity": "Network & Communications Security",
    "communicationssecurity": "Network & Communications Security",
    "iam": "Identity & Access Management",
    "identityandaccessmanagement": "Identity & Access Management",
    "accessmanagement": "Identity & Access Management",
    "securityassessment": "Security Assessment & Testing",
    "securitytesting": "Security Assessment & Testing",
    "penetrationtesting": "Security Assessment & Testing",
    "securityoperations": "Security Operations",
    "incidentresponse": "Security Operations",
    "softwaresecurity": "Software Development Security",
    "sdlc": "Software Development Security",
    "devsecops": "Software Development Security",
}


def classify_domain(
    question_text: str,
    options_text: str = "",
    hashtags: Optional[list[str]] = None,
    use_claude: bool = True,
) -> tuple[Optional[str], str]:
    """
    Returns (domain, confidence) where confidence is one of:
    'hashtag', 'keyword', 'claude', or None (meaning unclassified).
    """
    # Stage 1a: hashtag match
    if hashtags:
        for tag in hashtags:
            mapped = _HASHTAG_DOMAIN_MAP.get(tag.lower().replace("-", "").replace("_", ""))
            if mapped:
                return mapped, "hashtag"

    # Stage 1b: keyword scoring
    combined = (question_text + " " + options_text).lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > 0:
            scores[domain] = score

    if scores:
        best_domain = max(scores, key=lambda d: scores[d])
        best_score = scores[best_domain]
        # Require a minimum score and a clear winner (not tied)
        sorted_scores = sorted(scores.values(), reverse=True)
        if best_score >= 2 and (len(sorted_scores) < 2 or sorted_scores[0] > sorted_scores[1]):
            return best_domain, "keyword"

    # Stage 2: Claude API fallback
    if use_claude:
        try:
            return _classify_with_claude(question_text), "claude"
        except Exception:
            pass

    return None, None


def _classify_with_claude(question_text: str) -> Optional[str]:
    from . import config
    import anthropic

    api_key = config.require_anthropic_key()
    client = anthropic.Anthropic(api_key=api_key)

    domain_list = "\n".join(f"- {d}" for d in config.CISSP_DOMAINS)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        system=(
            "You are a CISSP domain classifier. Given a CISSP exam question, "
            "respond with ONLY the domain name from the list below. "
            "No explanation, no punctuation — just the exact domain name.\n\n"
            f"{domain_list}"
        ),
        messages=[{"role": "user", "content": question_text[:600]}],
    )
    result = response.content[0].text.strip()
    # Validate the response is one of the known domains
    for domain in config.CISSP_DOMAINS:
        if domain.lower() in result.lower():
            return domain
    return None

import re
from email.message import Message
from typing import Optional, Literal

def extract_plain_text(msg: Message) -> str:
    """
    Walks a parsed email.message.Message and returns its text/plain part.
    Falls back to a crude HTML-tag strip if only text/html is present
    (common from Gmail's web client on some reply styles) — this fallback
    is intentionally minimal; if it proves unreliable in practice, swap in
    a real HTML-to-text library (e.g. html2text) rather than patching regex
    rules here indefinitely.
    """
    if msg.is_multipart():
        plain_part = None
        html_part = None
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain" and plain_part is None:
                plain_part = part
            elif content_type == "text/html" and html_part is None:
                html_part = part
        if plain_part is not None:
            return plain_part.get_content()
        if html_part is not None:
            return _strip_html(msg.get_content())
        return ""
    else:
        if msg.get_content_type() == "text/html":
            return _strip_html(msg.get_content())
        return msg.get_content()

def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()

_QUOTE_MARKERS = (
    re.compile(r"^\s*>"),                          # classic quoted-reply prefix
    re.compile(r"^On .+ wrote:\s*$"),               # "On <date>, <name> wrote:"
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
    re.compile(r"^From:\s", re.IGNORECASE),         # forwarded/quoted header block start
)

def _strip_quoted_history(body: str) -> str:
    """Returns only the lines BEFORE the first quoted-history marker."""
    lines = body.splitlines()
    kept = []
    for line in lines:
        if any(marker.match(line) for marker in _QUOTE_MARKERS):
            break
        kept.append(line)
    return "\n".join(kept)

_APPROVE_RE = re.compile(r"\bAPPROVE(D)?\b", re.IGNORECASE)
_REJECT_RE = re.compile(r"\b(REJECT(ED)?|DENY|DENIED)\b", re.IGNORECASE)

def parse_decision(raw_body: str) -> Optional[Literal["approved", "rejected"]]:
    """
    Looks only at the reply's own text (quoted history stripped), and only
    at the first few non-empty lines — a decision buried deep in a long
    reply, or only present in quoted context, should NOT count. Returns
    None (not a guess) if neither keyword appears clearly.
    """
    own_text = _strip_quoted_history(raw_body)
    non_empty_lines = [line.strip() for line in own_text.splitlines() if line.strip()]
    lead_text = " ".join(non_empty_lines[:3])  # first few real lines only
 
    approve_match = _APPROVE_RE.search(lead_text)
    reject_match = _REJECT_RE.search(lead_text)
 
    if approve_match and not reject_match:
        return "approved"
    if reject_match and not approve_match:
        return "rejected"
    # both present, or neither — genuinely ambiguous, don't guess
    return None
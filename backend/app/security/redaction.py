"""Shared presentation redaction for URI credentials.

The raw source, byte offsets and hashes stay untouched. Only text that leaves
the process is masked. A credential segment is everything between the scheme
delimiter and the final ``@`` of the URI authority; the first ``@`` is not
assumed to be the password delimiter (raw ``@`` and percent-encoded forms
both occur in real configs).
"""

from __future__ import annotations

import re

_URI_SCHEME_RE = re.compile(
    r"(?P<scheme>\b(?:jdbc:[a-z][a-z0-9+.-]*|[a-z][a-z0-9+.-]*)://)",
    re.IGNORECASE,
)
_AUTHORITY_STOP = frozenset("/?# \t\r\n\"'<>")


def mask_uri_credentials(text: str) -> str:
    """Mask the whole userinfo segment, not just the part before the first @."""
    if not text:
        return ""
    chunks: list[str] = []
    cursor = 0
    for match in _URI_SCHEME_RE.finditer(text):
        authority_start = match.end()
        authority_end = authority_start
        while authority_end < len(text) and text[authority_end] not in _AUTHORITY_STOP:
            authority_end += 1
        authority = text[authority_start:authority_end]
        if "@" not in authority:
            continue
        host = authority.rsplit("@", 1)[1]
        replacement = f"***@{host}" if host else "***"
        chunks.append(text[cursor:authority_start])
        chunks.append(replacement)
        cursor = authority_end
    if not chunks:
        return text
    chunks.append(text[cursor:])
    return "".join(chunks)
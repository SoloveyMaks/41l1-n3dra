"""Secret-safe rendering for notification destinations.

A webhook URL may carry a credential in its path.  The full URL is stored only
on the server (settings snapshot/outbox); everything that leaves the process —
API responses, journal details, delivery results — uses :func:`mask_destination`
and never echoes query/fragment or resolver/HTTP error text.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def mask_destination(url: str) -> str:
    """Return ``scheme://host[:port]/…`` without path, query or userinfo."""
    if not isinstance(url, str) or not url:
        return ''
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').strip()
        port = parts.port
    except ValueError:
        return 'Приёмник скрыт'
    if not host:
        return 'Приёмник скрыт'
    netloc = f'[{host}]' if ':' in host else host
    if port is not None:
        netloc = f'{netloc}:{port}'
    prefix = f'{parts.scheme}://' if parts.scheme in {'http', 'https'} else ''
    path = parts.path or ''
    suffix = '/…' if path not in {'', '/'} else ''
    return f'{prefix}{netloc}{suffix}'

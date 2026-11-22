"""Политика доставки уведомлений: никаких автоматических внешних POST.

Sample/дефолтный workflow не отправляет ничего без явного
`config.enabled=true` и разрешённого URL. Дополнительно режем SSRF:
loopback/private/link-local/metadata/CGNAT и любые non-global адреса запрещены,
если сервер явно не разрешил локальный тестовый приёмник (`allowLocal=true`);
внешний адрес требует `allowExternal=true`, https и стандартный порт.

DNS-проверка возвращает конкретные адреса. Runtime подключается именно к ним
(pinned address) и не следует redirects, поэтому повторное разрешение имени
между проверкой и запросом не может увести соединение в другую сеть.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit

BLOCKED_HOSTNAMES = {
    "metadata.google.internal", "metadata", "169.254.169.254",
    "localhost.localdomain", "ip6-localhost", "ip6-loopback",
}


@dataclass
class NotificationDecision:
    allowed: bool
    reason: str
    local: bool = False
    addresses: tuple[str, ...] = ()


def _normalize_ip(value: str):
    """Parse an IP, folding IPv4-mapped IPv6 to its IPv4 form."""
    ip = ipaddress.ip_address(value.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_global_ip(value: str) -> bool:
    """True only for globally routable addresses.

    ``ipaddress.is_global`` excludes RFC1918, loopback, link-local,
    reserved, unspecified, multicast and CGNAT (100.64.0.0/10). The extra
    explicit checks keep the policy readable if the stdlib table changes.
    """
    try:
        ip = _normalize_ip(value)
    except ValueError:
        return False
    if not ip.is_global:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def _server_local_allowlist() -> set[str]:
    """Destinations the server operator explicitly allows for local delivery.

    A user checkbox must never be enough to POST to an arbitrary port on the
    server host; the process environment owns the permission.
    """
    raw = os.getenv("CG_NOTIFY_ALLOW_LOCAL_HOSTS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _local_destination_allowed(host: str, port: int) -> bool:
    allowlist = _server_local_allowlist()
    if not allowlist:
        return False
    candidates = {host, f"{host}:{port}"}
    if host == "localhost":
        candidates.update({"127.0.0.1", "::1", f"127.0.0.1:{port}", f"[::1]:{port}"})
    return any(item in candidates for item in allowlist)


def _is_loopback(value: str) -> bool:
    try:
        return _normalize_ip(value).is_loopback
    except ValueError:
        return False


def _resolve(host: str, port: int) -> Tuple[list[str], Optional[str]]:
    try:
        infos = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
        )
        addresses = sorted({
            _normalize_ip(str(info[4][0])).compressed for info in infos
        })
    except (OSError, UnicodeError, ValueError):
        # Includes IDNA failures (overlong label). Callers turn this into a
        # controlled 422 without surfacing resolver text.
        return [], "Не удалось разрешить адрес приёмника"
    return addresses, None


def validate_webhook_url(url: str, *, allow_local: bool, allow_external: bool) -> NotificationDecision:
    if not url or not isinstance(url, str):
        return NotificationDecision(False, "URL вебхука не задан")
    if len(url) > 2048 or any(ch in url for ch in ("\r", "\n", "\x00")):
        return NotificationDecision(False, "Некорректный URL вебхука")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").strip().lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return NotificationDecision(False, "Некорректный URL или порт вебхука")
    if parts.scheme not in {"http", "https"}:
        return NotificationDecision(False, "Разрешены только http/https")
    if parts.username or parts.password:
        return NotificationDecision(False, "Userinfo в URL вебхука запрещён")
    if parts.query or parts.fragment:
        return NotificationDecision(False, "Query и fragment в URL вебхука запрещены")
    if not host:
        return NotificationDecision(False, "В URL нет host")
    if host in BLOCKED_HOSTNAMES:
        return NotificationDecision(False, "Cloud metadata/localhost alias запрещён")
    if not 1 <= port <= 65535:
        return NotificationDecision(False, "Некорректный порт")

    literal_ip: Optional[str] = None
    try:
        literal_ip = _normalize_ip(host).compressed
    except ValueError:
        literal_ip = None
    local_host = host in {"127.0.0.1", "::1", "localhost"} or (
        literal_ip is not None and _is_loopback(literal_ip)
    )

    if not allow_local and not allow_external:
        return NotificationDecision(False, "Отправка не разрешена: нужен явный выбор приёмника")

    if literal_ip is not None:
        addresses = [literal_ip]
    else:
        addresses, error = _resolve(host, port)
        if error:
            return NotificationDecision(False, error)
    if not addresses:
        return NotificationDecision(False, "Host не разрешился в IP")
    if any(
        _normalize_ip(address).is_link_local
        for address in addresses
        if _valid_ip(address)
    ):
        return NotificationDecision(False, "Link-local/metadata адрес запрещён")

    if local_host or all(not _is_global_ip(address) for address in addresses):
        if not allow_local:
            return NotificationDecision(False, "Локальный/частный адрес запрещён без allowLocal=true")
        if not all(_is_loopback(address) for address in addresses):
            return NotificationDecision(False, "В allowLocal разрешён только loopback, не частная сеть")
        if not _local_destination_allowed(host, port):
            return NotificationDecision(
                False,
                "Локальный приёмник не разрешён серверной настройкой CG_NOTIFY_ALLOW_LOCAL_HOSTS",
            )
        return NotificationDecision(
            True, "Локальный тестовый приёмник разрешён allowLocal=true и серверным allowlist",
            local=True, addresses=tuple(addresses),
        )

    if not allow_external:
        return NotificationDecision(False, "Внешняя отправка запрещена без allowExternal=true")
    if parts.scheme != "https":
        return NotificationDecision(False, "Внешняя отправка только по https")
    if port not in (443,):
        return NotificationDecision(False, "Внешняя отправка только на порт 443")
    if not all(_is_global_ip(address) for address in addresses):
        return NotificationDecision(False, "Внешний host разрешился в non-global адрес")
    return NotificationDecision(
        True, "Внешний https-приёмник разрешён явным allowExternal=true",
        addresses=tuple(addresses),
    )


def _valid_ip(value: str) -> bool:
    try:
        _normalize_ip(value)
        return True
    except ValueError:
        return False

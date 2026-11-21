"""Политика доставки уведомлений: никаких автоматических внешних POST.

Sample/дефолтный workflow не отправляет ничего без явного
`config.enabled=true` и разрешённого URL. Дополнительно режем SSRF:
loopback/private/link-local/metadata адреса запрещены, если сервер явно не
разрешил локальный тестовый приёмник (`allowLocal=true`); внешний адрес
требует `allowExternal=true`, https и стандартный порт.
"""

from __future__ import annotations

import ipaddress
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


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def _resolve(host: str, port: int) -> Tuple[list[str], Optional[str]]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return [], f"DNS не разрешил host: {exc}"
    addresses = sorted({info[4][0] for info in infos})
    return addresses, None


def validate_webhook_url(url: str, *, allow_local: bool, allow_external: bool) -> NotificationDecision:
    if not url or not isinstance(url, str):
        return NotificationDecision(False, "URL вебхука не задан")
    if len(url) > 2048 or any(ch in url for ch in ("\r", "\n", "\x00")):
        return NotificationDecision(False, "Некорректный URL вебхука")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return NotificationDecision(False, "Разрешены только http/https")
    if parts.username or parts.password:
        return NotificationDecision(False, "Userinfo в URL вебхука запрещён")
    host = (parts.hostname or "").strip().lower()
    if not host:
        return NotificationDecision(False, "В URL нет host")
    if host in BLOCKED_HOSTNAMES:
        return NotificationDecision(False, "Cloud metadata/localhost alias запрещён")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return NotificationDecision(False, "Некорректный порт")
    literal_ip: Optional[str] = None
    try:
        literal_ip = str(ipaddress.ip_address(host))
    except ValueError:
        literal_ip = None
    local_host = host in {"127.0.0.1", "::1", "localhost"} or (
        literal_ip is not None and ipaddress.ip_address(literal_ip).is_loopback
    )

    addresses, error = _resolve(host, port)
    if error:
        return NotificationDecision(False, error)
    if not addresses:
        return NotificationDecision(False, "Host не разрешился в IP")
    if literal_ip is not None:
        addresses = [literal_ip]

    if any(ipaddress.ip_address(address).is_link_local for address in addresses if _valid_ip(address)):
        return NotificationDecision(False, "Link-local/metadata адрес запрещён")

    if local_host or all(not _is_public_ip(address) for address in addresses):
        if not allow_local:
            return NotificationDecision(False, "Локальный/частный адрес запрещён без allowLocal=true")
        if not all(ipaddress.ip_address(address).is_loopback for address in addresses if _valid_ip(address)):
            return NotificationDecision(False, "В allowLocal разрешён только loopback, не частная сеть")
        return NotificationDecision(True, "Локальный тестовый приёмник разрешён явным allowLocal=true", local=True)

    if not allow_external:
        return NotificationDecision(False, "Внешняя отправка запрещена без allowExternal=true")
    if parts.scheme != "https":
        return NotificationDecision(False, "Внешняя отправка только по https")
    if port not in (443,):
        return NotificationDecision(False, "Внешняя отправка только на порт 443")
    if not all(_is_public_ip(address) for address in addresses if _valid_ip(address)):
        return NotificationDecision(False, "Внешний host разрешился в private/reserved адрес")
    return NotificationDecision(True, "Внешний https-приёмник разрешён явным allowExternal=true")


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False
"""Explicit object-ownership policy used by the SAST rules.

A field name alone is not evidence.  The policy states which columns identify
the owner and what must hold for a read to be safe: an authenticated route that
returns one object must constrain the query by the owner column and the
authenticated principal, not only by a client-supplied identifier.
"""

from __future__ import annotations

OWNER_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "owner_id",
    "user_id",
    "account_id",
)

OBJECT_ACCESS_POLICY = (
    "Объект принадлежит владельцу, указанному в owner-колонке. Если маршрут "
    "возвращает один объект по идентификатору, переданному клиентом, запрос "
    "обязан дополнительно ограничить выборку владельцем из аутентифицированного "
    "Principal. Совпадение имени поля с owner-колонкой само по себе доступ не "
    "доказывает."
)

_OWNER_PREDICATE_TEMPLATE = r"(?is)\b{column}\b\s*(=|in)\s*[?:]"


def owner_predicate(column: str) -> str:
    return _OWNER_PREDICATE_TEMPLATE.format(column=column)

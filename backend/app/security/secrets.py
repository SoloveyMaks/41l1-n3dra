"""AES-GCM шифрование секретных полей черновиков в хранилище (at rest).

Секретные поля сценариев (``secretToken``/``webhookSecret`` и snake_case
варианты) раньше лежали в ``runtime.workflow_revisions.draft_json`` открытым
текстом. Модуль даёт версионный конверт и прозрачные обходы структуры:

    enc:v1:<nonce_b64>:<ciphertext_b64>

AES-256-GCM: nonce 12 байт, ciphertext включает GCM-tag. AAD — имя поля,
поэтому перенос конверта на другое поле не расшифровывается. Ключ — env
``NEDRA_SECRET_KEY`` (standard base64, ровно 32 байта). Режим —
``NEDRA_SECRETS_MODE``:

* ``optional`` (по умолчанию) — без ключа всё работает как раньше: запись и
  чтение открытым текстом, в лог пишется предупреждение без значений;
* ``required`` — без ключа запись отказывает (``MissingSecretsKeyError``).

Неверный ключ, подмена ciphertext или AAD — явная ``SecretDecryptionError``:
тихого plaintext не бывает. Чтение legacy plaintext без ключа проходит без
потери. Чтение конверта без ключа — явный отказ в обоих режимах: секрета на
выходе нет, молча отдавать конверт нельзя.

Префикс ``enc:v1:`` целиком зарезервирован за конвертом хранилища: модель
``WorkflowDraft`` отклоняет такой пользовательский ввод до записи (422), а
``encrypt_secrets`` не пишет его в БД и в режиме повторного сохранения
проверяет внутренний конверт расшифровкой, а не перешифровывает вслепую.
При чтении любой ``enc:v1:`` идёт в расшифровку: повреждённый или обрезанный
конверт даёт явную ошибку, а не читается как открытый секрет.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from typing import Any, Dict, TypeVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("SecuritySecrets")

SECRET_FIELDS = frozenset({
    "secretToken", "secret_token", "webhookSecret", "webhook_secret",
})

MODE_OPTIONAL = "optional"
MODE_REQUIRED = "required"

KEY_ENV = "NEDRA_SECRET_KEY"
MODE_ENV = "NEDRA_SECRETS_MODE"

ENVELOPE_PREFIX = "enc:v1:"
# base64 standard: допускает padding '=' в конце каждой группы.
_ENVELOPE_RE = re.compile(r"^enc:v1:([A-Za-z0-9+/]+={0,2}):([A-Za-z0-9+/]+={0,2})$")
_NONCE_BYTES = 12
_KEY_BYTES = 32

T = TypeVar("T")


class SecretsError(RuntimeError):
    """Базовая ошибка шифрования секретов в хранилище."""


class SecretsConfigError(SecretsError):
    """Некорректная конфигурация: режим вне пароля или битый ключ."""


class MissingSecretsKeyError(SecretsError):
    """Ключ не задан, а он обязателен: отказ записи/чтения конверта."""


class SecretDecryptionError(SecretsError):
    """Неверный ключ или подмена конверта: явная ошибка вместо plaintext."""


class ReservedEnvelopeError(SecretsError, ValueError):
    """Секрет несёт зарезервированный префикс ``enc:v1:`` из пользовательского ввода.

    Наследует ``ValueError``, чтобы pydantic-валидатор превращал отказ в 422, и
    ``SecretsError``, чтобы storage-слой ловил его общей ошибкой шифрования.
    """


def secrets_mode() -> str:
    """Режим из ``NEDRA_SECRETS_MODE``; пусто/не задано = ``optional``."""
    raw = os.environ.get(MODE_ENV, "").strip().lower() or MODE_OPTIONAL
    if raw not in (MODE_OPTIONAL, MODE_REQUIRED):
        raise SecretsConfigError(
            f"{MODE_ENV}={raw!r} не поддерживается; ожидается "
            f"'{MODE_OPTIONAL}' или '{MODE_REQUIRED}'."
        )
    return raw


def load_key() -> bytes | None:
    """Ключ из ``NEDRA_SECRET_KEY``: base64 -> 32 байта, пустой env = None.

    Битый env — явная ошибка: молча писать открытым текстом при заданном, но
    неверном ключе нельзя. Значение ключа в ошибки и логи не попадает.
    """
    raw = os.environ.get(KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise SecretsConfigError(
            f"{KEY_ENV} не является корректным standard base64."
        ) from None
    if len(key) != _KEY_BYTES:
        raise SecretsConfigError(
            f"{KEY_ENV} должен декодироваться в {_KEY_BYTES} байт, получено {len(key)}."
        )
    return key


def is_encrypted(value: Any) -> bool:
    """True, если строка имеет вид конверта ``enc:v1:<nonce>:<ciphertext>``."""
    return isinstance(value, str) and _ENVELOPE_RE.match(value) is not None


def is_reserved_envelope(value: Any) -> bool:
    """True для строки с зарезервированным префиксом ``enc:v1:`` (формат конверта)."""
    return isinstance(value, str) and value.startswith(ENVELOPE_PREFIX)


def reject_reserved_envelope(value: Any) -> None:
    """Отклонить секретные поля с префиксом ``enc:v1:`` — для model-валидатора.

    Ловит и строгий конверт, и мусор вроде ``enc:v1:AAAA``: любой вид с этим
    префиксом принадлежит хранилищу и не может прийти из пользовательского
    ввода. Поднимает ``ReservedEnvelopeError`` (она же ``ValueError``, поэтому
    pydantic отдаёт 422).
    """
    def visitor(field: str, item: str) -> str:
        if is_reserved_envelope(item):
            raise ReservedEnvelopeError(
                f"Секретное поле {field!r} не может начинаться с "
                f"{ENVELOPE_PREFIX!r}: этот формат зарезервирован за "
                "шифрованием хранилища."
            )
        return item

    _walk(value, visitor)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _encrypt_value(key: bytes, field: str, value: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, value.encode("utf-8"), field.encode("utf-8"))
    return f"{ENVELOPE_PREFIX}{_b64(nonce)}:{_b64(sealed)}"


def _decrypt_value(key: bytes, field: str, envelope: str) -> str:
    match = _ENVELOPE_RE.match(envelope)
    if match is None:
        raise SecretDecryptionError(
            f"Поле {field!r}: значение с префиксом {ENVELOPE_PREFIX} не является "
            "валидным конвертом (повреждено или обрезано)."
        )
    try:
        nonce = base64.b64decode(match.group(1), validate=True)
        sealed = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise SecretDecryptionError(
            f"Поле {field!r}: конверт повреждён (неверный base64)."
        ) from None
    if len(nonce) != _NONCE_BYTES:
        raise SecretDecryptionError(
            f"Поле {field!r}: конверт повреждён (неверная длина nonce)."
        )
    try:
        plain = AESGCM(key).decrypt(nonce, sealed, field.encode("utf-8"))
    except InvalidTag:
        raise SecretDecryptionError(
            f"Поле {field!r} не расшифровывается: неверный {KEY_ENV} "
            "или подмена значения (AES-GCM tag не сошёлся)."
        ) from None
    return plain.decode("utf-8")


def _walk(value: Any, visitor) -> Any:
    """Обход вложенных dict/list; visitor(field, str) -> str для строк-секретов."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if key in SECRET_FIELDS and isinstance(item, str):
                result[key] = visitor(key, item)
            else:
                result[key] = _walk(item, visitor)
        return result
    if isinstance(value, list):
        return [_walk(item, visitor) for item in value]
    if isinstance(value, tuple):
        return tuple(_walk(item, visitor) for item in value)
    return value


def encrypt_secrets(value: T) -> T:
    """Зашифровать секретные поля при записи; не-строки и не-секреты не трогает.

    Без ключа: ``optional`` — возврат как есть с предупреждением в лог (без
    значений), ``required`` — ``MissingSecretsKeyError`` (отказ записи).
    Внутренний конверт при повторном сохранении проверяется расшифровкой
    текущим ключом (не перешифровывается вслепую), читается он или нет —
    запись конверта без подтверждения не проходит. Любой другой секрет с
    префиксом ``enc:v1:`` — ``ReservedEnvelopeError``: формат из пользовательского
    ввода в БД не пишется даже в обход модели.
    """
    key = load_key()
    mode = secrets_mode()
    if key is None:
        if mode == MODE_REQUIRED:
            raise MissingSecretsKeyError(
                f"{MODE_ENV}={MODE_REQUIRED}, но {KEY_ENV} не задан: "
                "запись секретов открытым текстом запрещена."
            )
        logger.warning(
            "%s не задан (%s=%s): секретные поля черновиков сохраняются "
            "открытым текстом, как до включения шифрования.",
            KEY_ENV, MODE_ENV, MODE_OPTIONAL,
        )

    def visitor(field: str, item: str) -> str:
        if is_encrypted(item):
            if key is None:
                raise MissingSecretsKeyError(
                    f"Поле {field!r} несёт конверт {ENVELOPE_PREFIX}, а {KEY_ENV} "
                    "не задан: проверить значение нельзя, запись отклонена."
                )
            # Проверка чтением: конверт должен расшифровываться текущим ключом.
            _decrypt_value(key, field, item)
            return item
        if is_reserved_envelope(item):
            raise ReservedEnvelopeError(
                f"Секретное поле {field!r} не может начинаться с "
                f"{ENVELOPE_PREFIX!r}: этот формат зарезервирован за "
                "шифрованием хранилища."
            )
        if key is None:
            return item
        return _encrypt_value(key, field, item)

    return _walk(value, visitor)


def decrypt_secrets(value: T) -> T:
    """Расшифровать секретные поля при чтении; legacy plaintext проходит как есть.

    Любой ``enc:v1:`` (включая повреждённый или обрезанный) идёт в расшифровку:
    без ключа — ``MissingSecretsKeyError``, с ключом строгий формат не совпал —
    ``SecretDecryptionError``. Повреждённый конверт никогда не отдаётся как
    открытый секрет; legacy plaintext без зарезервированного префикса проходит
    без потери.
    """
    key = load_key()

    def visitor(field: str, item: str) -> str:
        if not is_reserved_envelope(item):
            return item
        if key is None:
            raise MissingSecretsKeyError(
                f"Поле {field!r} зашифровано, а {KEY_ENV} не задан: "
                "расшифровать хранилище нельзя."
            )
        return _decrypt_value(key, field, item)

    return _walk(value, visitor)

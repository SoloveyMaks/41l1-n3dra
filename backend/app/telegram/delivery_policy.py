"""Политика Telegram-доставки уведомлений о завершении run.

Чистые функции без транспорта и хранилища: runtime применяет решение к каждому
claim outbox-строки. Три независимых условия:

* порог severity — наибольшая severity находок run достигает порога
  (порог «ниже» или «на» находке доставляет, «выше» — нет);
* проект/ветка — необязательные фильтры привязанного workspace;
* тихие часы — окно в локальном timezone; вместо потери отправка переносится
  на конец окна и перепроверяется при каждом claim.

Фильтры ничего не пишут: решение — deliver / cancel / defer — принимает
runtime, поэтому причина отказа всегда попадает в outbox.last_error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The storage layer owns the accepted value set (it validates writes); the
# storage module never imports this package, so the dependency stays acyclic.
from backend.app.storage.telegram import DEFAULT_SEVERITY, SEVERITIES


class DeliverySettingsError(ValueError):
    """Некорректное значение фильтра доставки; текст — для пользователя."""


@dataclass(frozen=True)
class DeliveryFacts:
    """Факты run для фильтров: текст проекта (url + имя файла) и ветка."""

    project_text: str = ""
    branch: Optional[str] = None


@dataclass(frozen=True)
class DeliveryDecision:
    deliver: bool
    reason: str = ""
    defer_until: Optional[datetime] = None  # aware UTC; конец тихих часов


def severity_rank(value: Any) -> int:
    try:
        return SEVERITIES.index(str(value).strip().lower())
    except ValueError:
        return -1


def normalize_severity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in SEVERITIES:
        raise DeliverySettingsError(
            "severity должен быть info, low, medium, high или critical"
        )
    return text


def parse_hhmm(value: Any, *, field: str = "время") -> time:
    """Parse a strict ``HH:MM`` wall-clock time (00:00–23:59)."""
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise DeliverySettingsError(f"{field}: ожидается HH:MM, получено {text!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise DeliverySettingsError(f"{field}: ожидается HH:MM, получено {text!r}") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise DeliverySettingsError(f"{field}: диапазон 00:00–23:59")
    return time(hour=hour, minute=minute)


def format_hhmm(value: Optional[time]) -> str:
    if value is None:
        return "--:--"
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_quiet_window(value: Any) -> tuple[time, time]:
    """Parse ``HH:MM-HH:MM``; start == end means 'no quiet hours'."""
    text = str(value or "").strip().replace(" ", "")
    if "-" not in text:
        raise DeliverySettingsError("тихие часы: формат HH:MM-HH:MM")
    start_text, _, end_text = text.partition("-")
    start = parse_hhmm(start_text, field="начало тихих часов")
    end = parse_hhmm(end_text, field="конец тихих часов")
    return start, end


def normalize_timezone(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        raise DeliverySettingsError("timezone пуст или слишком длинный")
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise DeliverySettingsError(f"Неизвестный timezone: {text}") from None
    return text


def normalize_filter(value: Any, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise DeliverySettingsError(f"{field}: не длиннее {limit} символов")
    if any(ord(ch) < 0x20 for ch in text):
        raise DeliverySettingsError(f"{field}: управляющие символы запрещены")
    return text


def run_max_severity(run: Mapping[str, Any]) -> str:
    """Highest severity with at least one finding; an empty run is 'info'."""
    for severity in ("critical", "high", "medium", "low"):
        try:
            if int(run.get(f"{severity}_count") or 0) > 0:
                return severity
        except (TypeError, ValueError):
            continue
    return DEFAULT_SEVERITY


def _wall_clock_instant(day: date, value: time, tz: ZoneInfo, fold: int) -> datetime:
    """Absolute UTC instant of one fold of a wall-clock time on ``day``."""
    return datetime.combine(day, value).replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)


def quiet_deferral(
    now_utc: datetime,
    start: Optional[time],
    end: Optional[time],
    tz_name: str,
) -> Optional[datetime]:
    """UTC moment when the quiet window containing ``now_utc`` ends.

    Windows are wall-clock in ``tz_name``; ``start > end`` spans midnight.
    DST transitions make an end (or start) either repeated or missing locally,
    so both fold variants are evaluated and every comparison happens in UTC:
    the deferral is the nearest *real* end strictly after the current moment,
    and a window whose last end already passed never defers. Returns None when
    no window is active (or the timezone is unusable — a broken timezone must
    never block delivery).
    """
    if start is None or end is None or start == end:
        return None
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    try:
        tz = ZoneInfo(str(tz_name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None

    local_day = now_utc.astimezone(tz).date()
    overnight = end <= start
    # A same-day window ends on its own day; an overnight window continues into
    # the next day (fall-back can repeat the end wall time on either day).
    end_offsets = (0, 1) if overnight else (0,)
    best: Optional[datetime] = None
    for offset in (-1, 0, 1):
        day = date.fromordinal(local_day.toordinal() + offset)
        for start_fold in (0, 1):
            window_start = _wall_clock_instant(day, start, tz, start_fold)
            if window_start > now_utc:
                continue
            end_instants = set()
            for end_offset in end_offsets:
                end_day = date.fromordinal(day.toordinal() + end_offset)
                for end_fold in (0, 1):
                    instant = _wall_clock_instant(end_day, end, tz, end_fold)
                    if instant > window_start:
                        end_instants.add(instant)
            ends = sorted(end_instants)
            if not ends or now_utc >= ends[-1]:
                continue  # every real end of this window already passed
            candidate = next(e for e in ends if e > now_utc)
            best = candidate if best is None else min(best, candidate)
    return best


def matches_project(project_text: str, pattern: str) -> bool:
    """Case-insensitive substring match over repository URL and artifact name."""
    wanted = str(pattern or "").strip()
    if not wanted:
        return True
    return wanted.casefold() in str(project_text or "").casefold()


def matches_branch(branch: Optional[str], pattern: str) -> bool:
    """Case-insensitive exact match, or prefix when the pattern ends with '*'."""
    wanted = str(pattern or "").strip()
    if not wanted:
        return True
    actual = str(branch or "")
    if not actual:
        return False
    if wanted.endswith("*"):
        return actual.casefold().startswith(wanted[:-1].casefold())
    return actual.casefold() == wanted.casefold()


def evaluate_delivery(
    *,
    kind: str,
    delivery_mode: str,
    run: Mapping[str, Any],
    min_severity: str,
    project_filter: str,
    branch_filter: str,
    quiet_start: Optional[time],
    quiet_end: Optional[time],
    quiet_timezone: str,
    facts: DeliveryFacts,
    now_utc: datetime,
) -> DeliveryDecision:
    """Decide one outbox claim: deliver, cancel with a reason, or defer."""
    allowed = {
        "alerts": ("alert",),
        "jsonc": ("jsonc",),
        "both": ("alert", "jsonc"),
    }.get(str(delivery_mode), ("alert", "jsonc"))
    if kind not in allowed:
        return DeliveryDecision(False, reason=f"режим доставки {delivery_mode}")

    observed = run_max_severity(run)
    if severity_rank(observed) < severity_rank(min_severity):
        return DeliveryDecision(
            False,
            reason=f"находки {observed} ниже порога {min_severity}",
        )

    if project_filter and not matches_project(facts.project_text, project_filter):
        return DeliveryDecision(
            False, reason=f"проект не совпадает с фильтром {project_filter!r}",
        )
    if branch_filter and not matches_branch(facts.branch, branch_filter):
        return DeliveryDecision(
            False, reason=f"ветка {facts.branch or '—'} не совпадает с фильтром {branch_filter!r}",
        )

    defer_until = quiet_deferral(now_utc, quiet_start, quiet_end, quiet_timezone)
    if defer_until is not None:
        return DeliveryDecision(
            True,
            reason="тихие часы",
            defer_until=defer_until,
        )
    return DeliveryDecision(True, reason="")

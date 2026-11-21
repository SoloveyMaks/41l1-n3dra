"""Read-only responsibility enrichment for one saved finding."""

from __future__ import annotations

from typing import Any, Dict, Optional
from pathlib import PurePosixPath

from backend.app.codefacts.identity import workspace_id
from backend.app.codefacts.source import is_safe_source_path
from backend.app.responsibility.codeowners import load_and_match_codeowners
from backend.app.responsibility.history import fetch_file_history, parse_repository_target
from backend.app.responsibility.models import (
    CodeownersInfo,
    ResponsibilityResponse,
    ResponsibilityScope,
    RoleSuggestion,
)


class ResponsibilityNotFound(Exception):
    pass


def _module_name(path: str) -> str:
    parts = PurePosixPath(path).parts
    if "kotlin" in parts:
        index = len(parts) - 1 - list(reversed(parts)).index("kotlin")
        remainder = parts[index + 1 :]
        if len(remainder) >= 2:
            return remainder[-2]
        if remainder:
            return remainder[0]
    if len(parts) >= 2:
        return parts[-2]
    return "приложение"


def _suggest_role(finding: Dict[str, Any], path: str, codeowners: CodeownersInfo) -> RoleSuggestion:
    analyzer = str(finding.get("analyzer") or "")
    rule_id = str(finding.get("ruleId") or "")
    module = _module_name(path)
    role = f"Владелец модуля {module}"
    basis = f"Роль по пути {path}; специального правила для {analyzer or 'unknown'} нет."
    confidence = "low"

    if analyzer == "secret" or rule_id.startswith("SECRET") or rule_id.startswith("CG26-SEC"):
        role = "Владелец конфигурации и секретов"
        basis = f"analyzer={analyzer or '—'}; ruleId={rule_id or '—'}; путь={path}. Секреты и конфигурация обычно принадлежат владельцу окружения/конфигурации."
        confidence = "high"
    elif analyzer == "sca" or rule_id.startswith("CG26-SCA"):
        role = "Ответственный за зависимости и сборку"
        basis = f"analyzer={analyzer or '—'}; ruleId={rule_id or '—'}; путь={path}. Зависимости и версии обслуживает владелец сборки."
        confidence = "high"
    elif rule_id.startswith("CF-ROUTE") or rule_id.startswith("CF-OWNER") or "IDOR" in rule_id.upper():
        role = "Владелец маршрута и модели доступа"
        basis = f"ruleId={rule_id or '—'}; путь={path}. Правило относится к авторизации маршрута или проверке владельца."
        confidence = "high"
    elif "SQL" in rule_id.upper():
        role = "Backend-разработчик доступа к данным"
        basis = f"ruleId={rule_id or '—'}; путь={path}. Правило относится к формированию запроса/обработке данных."
        confidence = "high"
    elif analyzer == "sast":
        role = "Backend-разработчик обработчика"
        basis = f"analyzer=sast; ruleId={rule_id or '—'}; путь={path}. Находка в обработчике/коде приложения."
        confidence = "medium"
    if codeowners.owners:
        basis += f" CODEOWNERS: {codeowners.matchedPattern} → {', '.join(codeowners.owners)}."
        confidence = "high"
    return RoleSuggestion(role=role, basis=basis, confidence=confidence)


def _history_without_repo(reason: str) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "provider": None,
        "reason": reason,
        "evidenceKind": "last_file_commit",
        "notBlame": True,
        "pullRequests": [],
        "pullRequestsStatus": "unsupported",
        "pullRequestsReason": "История репозитория/PR недоступна для ZIP или неизвестного provider.",
        "pullRequestsTruncated": False,
        "lineBlame": {
            "status": "unsupported",
            "reason": "Line blame не запрашивался: безопасной поддержки без токена нет.",
        },
        "limitations": [
            "Это не автор уязвимости и не blame строк.",
            "Роль всё равно рассчитана по analyzer/rule/path.",
        ],
    }


async def build_responsibility(
    *,
    db: Any,
    store: Any,
    run_id: str,
    finding_id: str,
) -> ResponsibilityResponse:
    run = db.get_run(run_id)
    if not run:
        raise ResponsibilityNotFound("Запуск не найден")
    findings = db.get_findings(run_id)
    finding = next((item for item in findings if str(item.get("id")) == finding_id), None)
    if finding is None:
        raise ResponsibilityNotFound("Находка не найдена")
    primary = finding.get("primaryLocation") if isinstance(finding.get("primaryLocation"), dict) else {}
    path = str(primary.get("path") or "")
    safe_path = is_safe_source_path(path)

    artifact_id = run.get("artifact_id")
    artifact = db.get_artifact(str(artifact_id)) if artifact_id else None
    artifact_workspace = str((artifact or {}).get("workspace_id") or workspace_id())
    meta = None
    if artifact and artifact_id:
        try:
            meta = store.load_artifact_meta(artifact_workspace, str(artifact_id))
        except (ValueError, OSError):
            meta = None

    if safe_path:
        codeowners = load_and_match_codeowners(
            store=store,
            workspace=artifact_workspace,
            artifact_row=artifact,
            meta=meta,
            path=path,
        )
    else:
        codeowners = CodeownersInfo(
            status="unavailable",
            basis="Путь находки не прошёл проверку; CODEOWNERS не искался.",
            limitations=["Некорректный путь не используется для чтения файлов или внешних запросов."],
        )

    suggestion = _suggest_role(finding, path or "неизвестный путь", codeowners)
    repository = meta.get("repository") if isinstance(meta, dict) else None
    provider, _, commit_sha = parse_repository_target(repository)

    if not safe_path:
        history_payload = _history_without_repo("Путь находки не прошёл проверку; история не запрашивалась.")
    elif provider is None or commit_sha is None:
        history_payload = _history_without_repo(
            "Артефакт из ZIP или без repository/commit_sha: .git и история репозитория не входят в snapshot."
        )
    else:
        history = await fetch_file_history(repository, path)
        history_payload = history.model_dump()

    snapshot_id = str(meta.get("snapshotId")) if isinstance(meta, dict) and meta.get("snapshotId") else None
    scope = ResponsibilityScope(
        runId=str(run.get("id") or run_id),
        artifactId=str(artifact_id) if artifact_id else None,
        snapshotId=snapshot_id,
        commitSha=commit_sha,
        path=path,
    )
    limitations = [
        "Рекомендуемая роль — подсказка по analyzer/rule/path, не назначение человека и не факт авторства.",
        "Последний коммит файла не является автором уязвимости; line blame отдельно не поддерживается.",
        "CODEOWNERS берётся только из immutable snapshot; ZIP без снимка не даёт истории.",
        "Чужие run/artifact/finding не адресуются клиентом; scope проверяется по текущему workspace.",
    ]
    return ResponsibilityResponse(
        scope=scope,
        findingId=str(finding_id),
        analyzer=str(finding.get("analyzer") or ""),
        ruleId=str(finding.get("ruleId") or ""),
        suggestedRole=suggestion,
        codeowners=codeowners,
        history=history_payload,
        limitations=limitations,
    )

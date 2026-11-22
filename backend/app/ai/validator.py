import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.contracts.schemas import AiItem, Finding

ADVISORIES_FILE = Path(__file__).resolve().parent.parent / "analyzers" / "advisories.json"

LOCATION_REF_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:kt|kts|java|xml|ya?ml|properties|gradle|toml|json))"
    r"(?::(\d+)(?:\s*[-–]\s*\d+)?)?"
)
UPDATE_TARGET_RE = re.compile(
    r"(?i)(?:обнов\w*|upgrade\w*|перейдите\s+на|используйте\s+версию|"
    r"fixed\s+in|fix(?:ed)?\s+(?:to|in)|version)\s*(?:до|to|на)?\s*[:—-]?\s*"
    r"(\d+\.\d+(?:\.\d+)?(?:[-.][A-Za-z0-9]+)?)"
)


class AiResponseValidator:
    def __init__(self, advisories_path: Optional[Path] = None):
        path = advisories_path or ADVISORIES_FILE
        self.advisory_fixed_versions: Dict[str, Set[str]] = {}
        if path.exists():
            advisories = json.loads(path.read_text(encoding="utf-8"))
            for advisory in advisories:
                ids = [advisory.get("id"), *advisory.get("aliases", [])]
                versions = {str(version).strip() for version in advisory.get("fixed_versions", []) if str(version).strip()}
                if advisory.get("fixed_version"):
                    versions.add(str(advisory["fixed_version"]).strip())
                for identifier in ids:
                    if identifier:
                        self.advisory_fixed_versions.setdefault(str(identifier).upper(), set()).update(versions)

    def allowed_fixed_versions_for(self, finding: Finding) -> List[str]:
        """Only versions tied to this finding's own advisories are allowed."""
        if finding.analyzer != "sca":
            return []
        versions = {
            version
            for advisory_id in finding.advisoryIds
            for version in self.advisory_fixed_versions.get(advisory_id.upper(), set())
        }
        return sorted(versions)

    def validate_json_shape(self, ai_data: Any) -> List[str]:
        """Validate only the transport shape before grounding checks.

        This catches non-JSON, missing fields and wrong types. Grounding
        (finding IDs, CVEs, fixed versions) is enforced by validate().
        """
        errors: List[str] = []
        if not isinstance(ai_data, dict):
            return ["Ответ AI не является JSON-объектом"]

        summary = ai_data.get("summary")
        if summary is not None and not isinstance(summary, str):
            errors.append("Поле 'summary' должно быть строкой")

        items = ai_data.get("items")
        if not isinstance(items, list):
            errors.append("Поле 'items' отсутствует или не является списком")
            return errors
        if not items:
            errors.append("Поле 'items' пустое")

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"Элемент #{idx} не является объектом")
                continue
            finding_id = item.get("findingId")
            if not isinstance(finding_id, str) or not finding_id.strip():
                errors.append(f"Элемент #{idx}: findingId должен быть непустой строкой")
            for field_name in ("explanation", "remediation"):
                value = item.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"Элемент #{idx}: {field_name} должен быть непустой строкой")
            fixed_version = item.get("suggestedFixedVersion")
            if fixed_version is not None and not isinstance(fixed_version, str):
                errors.append(f"Элемент #{idx}: suggestedFixedVersion должен быть строкой или null")
        return errors

    def _evidence_locations(self, finding: Finding) -> List[Tuple[str, int, int]]:
        rows: List[Tuple[str, int, int]] = []
        for location in [finding.primaryLocation, *finding.relatedLocations]:
            path = str(getattr(location, "path", "") or "").replace("\\", "/").lstrip("./")
            if not path:
                continue
            start = int(getattr(location, "startLine", 0) or 0)
            end = int(getattr(location, "endLine", start) or start)
            rows.append((path, start, max(start, end)))
        return rows

    def _free_text_grounding_errors(
        self,
        finding: Finding,
        text: str,
        suggested_fixed_version: Optional[str],
    ) -> List[str]:
        """Reject file/line and update-target versions the finding cannot back.

        A model may only cite evidence that belongs to the finding it explains:
        otherwise a plausible-but-invented path, line or upgrade version reaches
        the report as if it had been verified.
        """
        errors: List[str] = []
        evidence = self._evidence_locations(finding)
        for match in LOCATION_REF_RE.finditer(text):
            ref_path = match.group(1).replace("\\", "/").lstrip("./")
            ref_line = int(match.group(2)) if match.group(2) else None
            if not ref_path or not evidence:
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: находка {finding.id} не имеет evidence для ссылки {ref_path}"
                )
                continue
            matches = [
                row
                for row in evidence
                if row[0] == ref_path
                or row[0].endswith("/" + ref_path)
                or ref_path.endswith("/" + row[0])
            ]
            if not matches:
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: для находки {finding.id} указан путь {ref_path} вне её evidence"
                )
                continue
            if ref_line is not None and not any(start <= ref_line <= end for _, start, end in matches):
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: для находки {finding.id} строка {ref_line} "
                    f"вне evidence {matches[0][0]}:{matches[0][1]}-{matches[0][2]}"
                )
        allowed_versions = set(self.allowed_fixed_versions_for(finding))
        if suggested_fixed_version:
            allowed_versions.add(suggested_fixed_version)
        for version in UPDATE_TARGET_RE.findall(text):
            if version not in allowed_versions:
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: для находки {finding.id} предложена версия {version} "
                    f"без подтверждения в advisory"
                )
        return errors

    def validate(self, ai_data: Dict[str, Any], original_findings: List[Finding]) -> Tuple[bool, List[str], List[AiItem]]:
        """
        Validates AI response against the actual run findings.

        Grounding rules:
          - CVE mentioned for a finding must be in that finding's advisoryIds.
          - suggestedFixedVersion must belong to that finding's own advisory.
          - summary may mention only CVEs from the union of findings' advisoryIds.
          - unknown, duplicate or missing findingIds mean the response is not a
            fully successful explanation, even if some items are valid.
        """
        if not isinstance(ai_data, dict):
            return False, ["Формат ответа AI не является JSON-объектом"], []

        items = ai_data.get("items")
        if not isinstance(items, list):
            return False, ["Поле 'items' отсутствует или не является списком"], []

        errors: List[str] = []
        valid_items: List[AiItem] = []
        finding_map: Dict[str, Finding] = {f.id: f for f in original_findings}
        all_allowed_cves: Set[str] = set()
        for finding in original_findings:
            all_allowed_cves.update(identifier.upper() for identifier in finding.advisoryIds)

        summary = ai_data.get("summary")
        if isinstance(summary, str):
            foreign_summary_cves = set(re.findall(r"CVE-\d{4}-\d{4,7}", summary.upper())) - all_allowed_cves
            if foreign_summary_cves:
                errors.append(
                    "ОТКЛОНЕНО ВАЛИДАТОРОМ: summary содержит CVE вне advisoryIds находок: "
                    + ", ".join(sorted(foreign_summary_cves))
                )

        seen_ids: Set[str] = set()
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"Элемент #{idx} не является объектом")
                continue

            f_id = item.get("findingId")
            if not isinstance(f_id, str) or not f_id.strip():
                errors.append(f"Элемент #{idx}: отсутствует findingId")
                continue
            f_id = f_id.strip()
            if f_id in seen_ids:
                errors.append(f"ОТКЛОНЕНО ВАЛИДАТОРОМ: duplicate item для findingId '{f_id}'")
                continue
            seen_ids.add(f_id)

            if f_id not in finding_map:
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: вымышленный findingId '{f_id}', которого нет в находках сканеров"
                )
                continue

            target_finding = finding_map[f_id]
            explanation = item.get("explanation")
            remediation = item.get("remediation")
            if not isinstance(explanation, str) or not explanation.strip():
                errors.append(f"Находка {f_id}: отсутствует explanation")
                continue
            if not isinstance(remediation, str) or not remediation.strip():
                errors.append(f"Находка {f_id}: отсутствует remediation")
                continue
            explanation = explanation.strip()
            remediation = remediation.strip()

            allowed_cves = {identifier.upper() for identifier in target_finding.advisoryIds}
            foreign_cves = set(re.findall(r"CVE-\d{4}-\d{4,7}", f"{explanation} {remediation}".upper())) - allowed_cves
            if foreign_cves:
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: для находки {f_id} модель указала CVE вне её advisoryIds: "
                    + ", ".join(sorted(foreign_cves))
                )
                continue

            suggested_fixed_version = item.get("suggestedFixedVersion")
            if suggested_fixed_version is not None and not isinstance(suggested_fixed_version, str):
                errors.append(f"Находка {f_id}: suggestedFixedVersion должен быть строкой или null")
                continue
            suggested_fixed_version = (
                suggested_fixed_version.strip() if isinstance(suggested_fixed_version, str) else None
            )
            if suggested_fixed_version:
                allowed_versions = set(self.allowed_fixed_versions_for(target_finding))
                if target_finding.analyzer != "sca":
                    errors.append(
                        f"ОТКЛОНЕНО ВАЛИДАТОРОМ: для не-SCA находки {f_id} версия "
                        f"{suggested_fixed_version} недопустима"
                    )
                    continue
                elif suggested_fixed_version not in allowed_versions:
                    errors.append(
                        f"ОТКЛОНЕНО ВАЛИДАТОРОМ: версия {suggested_fixed_version} не связана "
                        f"с advisory {sorted(target_finding.advisoryIds)} находки {f_id}"
                    )
                    continue

            grounding_errors = self._free_text_grounding_errors(
                target_finding, f"{explanation}\n{remediation}", suggested_fixed_version
            )
            if grounding_errors:
                errors.extend(grounding_errors)
                continue

            valid_items.append(AiItem(
                findingId=f_id,
                explanation=explanation,
                remediation=remediation,
                suggestedFixedVersion=suggested_fixed_version
            ))

        returned_ids = {item.findingId for item in valid_items}
        missing_ids = [f.id for f in original_findings if f.id not in returned_ids]
        if missing_ids:
            errors.append(
                "ОТКЛОНЕНО ВАЛИДАТОРОМ: отсутствуют объяснения для findingId: "
                + ", ".join(missing_ids)
            )

        is_valid = len(errors) == 0 and len(valid_items) > 0
        return is_valid, errors, valid_items

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.contracts.schemas import AiItem, Finding

ADVISORIES_FILE = Path(__file__).resolve().parent.parent / "analyzers" / "advisories.json"


class AiResponseValidator:
    def __init__(self, advisories_path: Optional[Path] = None):
        path = advisories_path or ADVISORIES_FILE
        if path.exists():
            advisories = json.loads(path.read_text(encoding="utf-8"))
            self.valid_cves: Set[str] = {a["id"] for a in advisories if "id" in a}
            self.valid_fixed_versions: Set[str] = {a["fixed_version"] for a in advisories if "fixed_version" in a}
        else:
            self.valid_cves = set()
            self.valid_fixed_versions = set()

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

    def validate(self, ai_data: Dict[str, Any], original_findings: List[Finding]) -> Tuple[bool, List[str], List[AiItem]]:
        """
        Validates AI response against actual run findings and the official CVE catalog.
        Rejects:
          - Unknown finding IDs
          - Hallucinated CVEs not in the catalog
          - Unverified suggested versions
          - Incomplete structure
        """
        errors: List[str] = []
        valid_items: List[AiItem] = []

        if not isinstance(ai_data, dict):
            return False, ["Формат ответа AI не является JSON-объектом"], []

        items = ai_data.get("items")
        if not isinstance(items, list):
            return False, ["Поле 'items' отсутствует или не является списком"], []

        finding_map: Dict[str, Finding] = {f.id: f for f in original_findings}

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"Элемент #{idx} не является объектом")
                continue

            f_id = item.get("findingId")
            if not f_id:
                errors.append(f"Элемент #{idx}: отсутствует findingId")
                continue

            if f_id not in finding_map:
                errors.append(f"ОТКЛОНЕНО ВАЛИДАТОРОМ: вымышленный findingId '{f_id}', которого нет в находках сканеров")
                continue

            target_finding = finding_map[f_id]
            explanation = str(item.get("explanation", "")).strip()
            remediation = str(item.get("remediation", "")).strip()
            suggested_fixed_version = item.get("suggestedFixedVersion")

            if not explanation:
                errors.append(f"Находка {f_id}: отсутствует explanation")
                continue
            if not remediation:
                errors.append(f"Находка {f_id}: отсутствует remediation")
                continue

            # Check for hallucinated CVEs in explanation/remediation
            cve_matches = set(re.findall(r"CVE-\d{4}-\d{4,7}", f"{explanation} {remediation}"))
            # Allowed CVEs are strictly those in original finding advisoryIds or our catalog
            allowed_cves = set(target_finding.advisoryIds).union(self.valid_cves)
            hallucinated_cves = cve_matches - allowed_cves
            if hallucinated_cves:
                errors.append(
                    f"ОТКЛОНЕНО ВАЛИДАТОРОМ: для находки {f_id} модель указала вымышленные/неразрешённые CVE: {list(hallucinated_cves)}"
                )
                continue

            # Check suggestedFixedVersion
            if suggested_fixed_version:
                if target_finding.analyzer != "sca":
                    # For non-SCA, fixed version is invalid
                    suggested_fixed_version = None
                elif suggested_fixed_version not in self.valid_fixed_versions:
                    errors.append(
                        f"ПРЕДУПРЕЖДЕНИЕ: Версия {suggested_fixed_version} отсутствует в эталонном справочнике исправлений"
                    )

            valid_items.append(AiItem(
                findingId=f_id,
                explanation=explanation,
                remediation=remediation,
                suggestedFixedVersion=suggested_fixed_version
            ))

        is_valid = len(errors) == 0 and len(valid_items) > 0
        return is_valid, errors, valid_items

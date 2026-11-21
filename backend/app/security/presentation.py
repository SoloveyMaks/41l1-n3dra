"""Schema-aware presentation masking for runtime egress.

Only text values are rewritten. IDs, hashes, signatures, versions and config
keys are copied through unchanged so a response cannot silently change
identity or rule semantics.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

Masker = Callable[[str], str]

IDENTITY_KEYS = frozenset({
    "id", "runId", "workspaceId", "snapshotId", "nodeId", "artifactId",
    "evidenceId", "findingId", "fingerprint", "hash", "sourceHash",
    "bundleHash", "publishedHash", "normalizationVersion", "signature",
    "signatureHash", "ruleVersion", "revisionId", "revision",
    "run_id", "workspace_id", "snapshot_id", "node_id", "artifact_id",
    "evidence_id", "finding_id", "source_hash", "bundle_hash",
    "fingerprint_version", "rule_version",
})
CONFIG_KEYS = frozenset({
    "config", "configKey", "configuration", "settings", "environment",
})
SECRET_KEY_MARKERS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "private_key", "access_key", "credential",
)
TEXT_FIELDS = frozenset({
    "reason", "reasons", "summary", "message", "description", "excerpt",
    "snippet", "redactedEvidence", "redactedSnippet", "explanation",
    "remediation", "text", "detail",
})


def mask_value_tree(value: Any, masker: Masker) -> Any:
    """Recursively mask string values, keeping identity/config keys intact."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            lower = key.lower()
            if key in IDENTITY_KEYS:
                result[key] = item
            elif any(marker in lower for marker in SECRET_KEY_MARKERS) and isinstance(item, str):
                result[key] = "***"
            elif isinstance(item, str):
                result[key] = masker(item)
            else:
                result[key] = mask_value_tree(item, masker)
        return result
    if isinstance(value, list):
        return [mask_value_tree(item, masker) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_value_tree(item, masker) for item in value)
    if isinstance(value, str):
        return masker(value)
    return value


def mask_named_text_fields(value: Any, masker: Masker) -> Any:
    """Mask only known human-text fields; preserve IDs, hashes and anchors."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if key in TEXT_FIELDS:
                if isinstance(item, str):
                    result[key] = masker(item)
                elif isinstance(item, list):
                    result[key] = [
                        masker(entry) if isinstance(entry, str)
                        else mask_named_text_fields(entry, masker)
                        for entry in item
                    ]
                elif isinstance(item, tuple):
                    result[key] = tuple(
                        masker(entry) if isinstance(entry, str)
                        else mask_named_text_fields(entry, masker)
                        for entry in item
                    )
                else:
                    result[key] = mask_named_text_fields(item, masker)
            elif isinstance(item, (dict, list, tuple)):
                result[key] = mask_named_text_fields(item, masker)
            else:
                result[key] = item
        return result
    if isinstance(value, list):
        return [mask_named_text_fields(item, masker) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_named_text_fields(item, masker) for item in value)
    return value

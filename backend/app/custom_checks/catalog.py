"""Closed language/runner/template catalog and input/output contracts."""

from __future__ import annotations

import textwrap
from typing import Dict

LANGUAGE = "python"
RUNNER = "bwrap-python3"
INPUT_CONTRACT = "custom-check-input/1"
OUTPUT_CONTRACT = "custom-check-result/1"
MAX_SCRIPT_BYTES = 65536
MAX_NAME_CHARS = 80
MAX_VERSION_CHARS = 32
MAX_TIMEOUT_SECONDS = 10
MIN_TIMEOUT_SECONDS = 1
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 512
MIN_OUTPUT_BYTES = 1024
MAX_OUTPUT_BYTES = 65536
MAX_FINDINGS = 50
MAX_TEXT_CHARS = 500

_TODO_TEMPLATE = textwrap.dedent(
    """
    import json
    import os

    with open('/input/input.json', encoding='utf-8') as handle:
        payload = json.load(handle)
    root = payload['projectRoot']
    findings = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {'.git', 'build', 'target', 'node_modules'})
        for name in sorted(files):
            path = os.path.join(current, name)
            if not name.endswith(('.kt', '.kts', '.java', '.py', '.js', '.ts')):
                continue
            try:
                with open(path, encoding='utf-8', errors='replace') as source:
                    for number, line in enumerate(source, 1):
                        if 'TODO' in line or 'FIXME' in line:
                            findings.append({
                                'ruleId': 'CUSTOM-TODO',
                                'severity': 'info',
                                'message': line.strip()[:500] or 'TODO marker',
                                'path': os.path.relpath(path, root).replace(os.sep, '/'),
                                'startLine': number,
                                'endLine': number,
                            })
                            if len(findings) >= 50:
                                break
            except OSError:
                continue
            if len(findings) >= 50:
                break
        if len(findings) >= 50:
            break
    print(json.dumps({'schemaVersion': 'custom-check-result/1', 'summary': 'TODO markers', 'findings': findings}))
    """
).strip()

_FILE_INVENTORY_TEMPLATE = textwrap.dedent(
    """
    import json
    import os

    with open('/input/input.json', encoding='utf-8') as handle:
        payload = json.load(handle)
    root = payload['projectRoot']
    count = 0
    total = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {'.git', 'build', 'target', 'node_modules'})
        for name in sorted(files):
            count += 1
            try:
                total += os.path.getsize(os.path.join(current, name))
            except OSError:
                pass
    print(json.dumps({
        'schemaVersion': 'custom-check-result/1',
        'summary': f'Файлов: {count}, байт: {total}',
        'findings': [],
    }))
    """
).strip()

TEMPLATES: Dict[str, Dict[str, str]] = {
    "todo_scan": {"title": "TODO / FIXME", "script": _TODO_TEMPLATE},
    "file_inventory": {"title": "Инвентаризация файлов", "script": _FILE_INVENTORY_TEMPLATE},
}

TEMPLATE_IDS = tuple(TEMPLATES)

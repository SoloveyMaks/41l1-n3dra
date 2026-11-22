import re
from typing import List, Tuple, Union


def parse_version_tokens(v: str) -> List[Union[int, str]]:
    """
    Parses a version string into comparable tokens following Maven ComparableVersion rules.
    Numbers are integers, words are lower-cased strings.
    """
    cleaned = v.strip().lstrip("vV")
    tokens = []
    # Split by dots, hyphens, pluses, underscores
    raw_parts = re.findall(r'[0-9]+|[a-zA-Z]+', cleaned)
    for p in raw_parts:
        if p.isdigit():
            tokens.append(int(p))
        else:
            p_lower = p.lower()
            # Canonicalize common qualifiers
            if p_lower in ("alpha", "a"):
                tokens.append("alpha")
            elif p_lower in ("beta", "b"):
                tokens.append("beta")
            elif p_lower in ("milestone", "m"):
                tokens.append("milestone")
            elif p_lower in ("rc", "cr"):
                tokens.append("rc")
            elif p_lower in ("snapshot",):
                tokens.append("snapshot")
            elif p_lower in ("final", "ga", "release"):
                tokens.append("")
            elif p_lower in ("sp",):
                tokens.append("sp")
            else:
                tokens.append(p_lower)
    return tokens


def compare_versions(v1_str: str, v2_str: str) -> int:
    """
    Compares two version strings.
    Returns:
       < 0 if v1 < v2
       0 if v1 == v2
       > 0 if v1 > v2
    """
    t1 = parse_version_tokens(v1_str)
    t2 = parse_version_tokens(v2_str)

    qualifier_weights = {
        "alpha": 1,
        "beta": 2,
        "milestone": 3,
        "rc": 4,
        "snapshot": 5,
        "": 6,  # Final / GA
        "sp": 7
    }

    max_len = max(len(t1), len(t2))
    for i in range(max_len):
        part1 = t1[i] if i < len(t1) else ("" if isinstance(t2[i], str) else 0)
        part2 = t2[i] if i < len(t2) else ("" if isinstance(t1[i], str) else 0)

        if isinstance(part1, int) and isinstance(part2, int):
            if part1 != part2:
                return 1 if part1 > part2 else -1
        elif isinstance(part1, str) and isinstance(part2, str):
            w1 = qualifier_weights.get(part1, 0)
            w2 = qualifier_weights.get(part2, 0)
            if w1 != w2:
                return 1 if w1 > w2 else -1
            if part1 != part2:
                return 1 if part1 > part2 else -1
        elif isinstance(part1, int) and isinstance(part2, str):
            # Integer > pre-release qualifier
            w2 = qualifier_weights.get(part2, 0)
            if w2 < 6:
                return 1
            return -1
        elif isinstance(part1, str) and isinstance(part2, int):
            w1 = qualifier_weights.get(part1, 0)
            if w1 < 6:
                return -1
            return 1

    return 0


def matches_version_range(version: str, range_clause: str) -> bool:
    """
    Evaluates range clauses like:
      "< 2.15.0"
      ">= 2.5.0, < 2.5.12"
      "== 1.2.3"
    """
    sub_clauses = [c.strip() for c in range_clause.split(",") if c.strip()]
    if not sub_clauses:
        return False
    for sc in sub_clauses:
        m = re.fullmatch(r"(<=|>=|==|!=|<|>|=)\s*(.+)", sc)
        if not m:
            return False
        op, target_v = m.groups()
        cmp = compare_versions(version, target_v)
        if op == "<" and not (cmp < 0):
            return False
        elif op == "<=" and not (cmp <= 0):
            return False
        elif op == ">" and not (cmp > 0):
            return False
        elif op == ">=" and not (cmp >= 0):
            return False
        elif (op == "==" or op == "=") and not (cmp == 0):
            return False
        elif op == "!=" and not (cmp != 0):
            return False
    return True

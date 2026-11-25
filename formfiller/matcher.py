"""Heuristic field-to-profile matching.

The matcher deliberately favours precision over recall: a wrong auto-fill is
worse than a field the user has to map by hand.  Every decision carries a
confidence score and a human-readable reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .fields import AUTOCOMPLETE_MAP, FieldDef, candidate_values, normalize

SOURCE_WEIGHTS: Dict[str, float] = {
    "label": 100.0,
    "aria": 96.0,
    "placeholder": 78.0,
    "name": 72.0,
    "id": 68.0,
    "title": 58.0,
    "context": 26.0,
}

SOURCE_LABELS: Dict[str, str] = {
    "label": "标签",
    "aria": "无障碍标签",
    "placeholder": "占位提示",
    "name": "name 属性",
    "id": "id 属性",
    "title": "title 属性",
    "context": "周围文字",
    "autocomplete": "autocomplete",
    "type": "控件类型",
    "rule": "本站规则",
}

STRICT_PASSWORD = True
MIN_SCORE = 34.0


@dataclass
class MatchDecision:
    key: Optional[str]
    confidence: float
    source: str
    reason: str
    ambiguous: bool = False
    ignored: bool = False


def _quality(source_text: str, alias_text: str) -> float:
    """How well one source string matches one alias (0..1)."""
    s = normalize(source_text)
    a = normalize(alias_text)
    if not s or not a:
        return 0.0
    if s == a:
        return 1.0
    # A one-character alias is only trustworthy as an exact match.
    if len(a) < 2:
        return 0.0
    if a in s:
        # Require at least a modest length ratio to avoid "id" matching "video".
        if len(a) <= 2 and len(s) > len(a) + 6:
            return 0.0
        return 0.82
    if len(s) >= 2 and s in a:
        return 0.64
    if len(a) >= 3 and len(s) >= 3:
        ratio = SequenceMatcher(None, s, a).ratio()
        if ratio >= 0.86:
            return ratio * 0.52
    return 0.0


def _candidate_sources(candidate: Dict[str, Any]) -> List[Tuple[str, str]]:
    sources: List[Tuple[str, str]] = []
    label = (candidate.get("label") or "").strip()
    aria = (candidate.get("aria_label") or "").strip()
    placeholder = (candidate.get("placeholder") or "").strip()
    name = (candidate.get("name") or "").strip()
    element_id = (candidate.get("element_id") or "").strip()
    title = (candidate.get("title") or "").strip()
    context = (candidate.get("context") or "").strip()
    for source, text in (
        ("label", label),
        ("aria", aria),
        ("placeholder", placeholder),
        ("name", name),
        ("id", element_id),
        ("title", title),
        ("context", context),
    ):
        if text:
            sources.append((source, text))
    return sources


def _negative_hit(field: FieldDef, candidate: Dict[str, Any]) -> Optional[str]:
    if not field.negatives:
        return None
    haystacks = [
        candidate.get("label") or "",
        candidate.get("aria_label") or "",
        candidate.get("placeholder") or "",
        candidate.get("name") or "",
        candidate.get("element_id") or "",
        candidate.get("title") or "",
    ]
    for text in haystacks:
        n = normalize(text)
        if not n:
            continue
        for negative in field.negatives:
            nn = normalize(negative)
            if nn and nn in n:
                return negative
    return None


def _type_hint(field: FieldDef, candidate: Dict[str, Any]) -> float:
    """Small compatibility bonus; never enough to match on its own."""
    ctype = (candidate.get("type") or "text").lower()
    tag = (candidate.get("tag") or "").lower()
    hint = 0.0
    if field.key == "password":
        if ctype == "password":
            hint += 65.0
        else:
            hint -= 120.0
    elif STRICT_PASSWORD and ctype == "password" and field.key != "password":
        return -300.0

    if field.key == "email":
        if ctype == "email":
            hint += 28.0
        elif ctype in ("tel", "number", "password", "date"):
            hint -= 22.0
    if field.key == "phone":
        if ctype == "tel":
            hint += 24.0
        elif ctype in ("email", "password", "date"):
            hint -= 18.0
    if field.key == "telephone" and ctype == "tel":
        hint += 10.0
    if field.key == "birthday" and ctype == "date":
        hint += 18.0
    if field.key == "birthday" and ctype in ("email", "tel", "password"):
        hint -= 30.0
    if field.key in ("postal_code", "graduation_year", "work_years", "qq") and ctype in ("number", "tel"):
        hint += 8.0
    if field.key == "address" and tag == "textarea":
        hint += 10.0
    if field.key == "remarks" and tag == "textarea":
        hint += 18.0
    if field.key == "agree" and ctype == "checkbox":
        hint += 20.0
    if field.key != "agree" and ctype == "checkbox":
        hint -= 25.0
    if field.key == "gender" and ctype in ("radio", "select"):
        hint += 12.0
    return hint


def _choice_hint(field: FieldDef, candidate: Dict[str, Any]) -> float:
    """Use option text to disambiguate selects and radio groups."""
    options = candidate.get("options") or []
    if not isinstance(options, list) or not options:
        return 0.0
    texts = " ".join(str(o.get("text") or o.get("value") or "") for o in options if isinstance(o, dict))
    joined = normalize(texts)
    if not joined:
        return 0.0
    if field.key == "gender" and ("男" in joined and "女" in joined):
        return 42.0
    if field.key == "degree" and any(x in joined for x in ("本科", "硕士", "博士", "大专", "专科")):
        return 34.0
    if field.key == "province" and any(x in joined for x in ("北京", "广东", "浙江", "江苏", "四川", "山东")):
        return 30.0
    if field.key == "city" and ("市" in texts and "省" in texts):
        return 12.0
    return 0.0


def _autocomplete_key(candidate: Dict[str, Any]) -> Optional[str]:
    raw = (candidate.get("autocomplete") or "").strip().lower()
    if not raw or raw in ("off", "on", "none", "false"):
        return None
    tokens = raw.replace(" ", "").split()
    for token in tokens:
        if token in AUTOCOMPLETE_MAP:
            return AUTOCOMPLETE_MAP[token]
    # Some sites put a section prefix such as "shipping email".
    for token in tokens:
        for ac, key in AUTOCOMPLETE_MAP.items():
            if ac and ac in token:
                return key
    return None


def match_candidate(
    candidate: Dict[str, Any],
    field_defs: Sequence[FieldDef],
    rule_key: Optional[str] = None,
) -> MatchDecision:
    """Choose the best profile key for one scanned field."""
    if rule_key == "__ignore__":
        return MatchDecision(None, 0.0, "rule", "本站规则：忽略此字段", ignored=True)
    if rule_key:
        by_key = {f.key: f for f in field_defs}
        if rule_key in by_key:
            return MatchDecision(rule_key, 1.0, "rule", f"本站规则：{by_key[rule_key].label}")

    ctype = (candidate.get("type") or "text").lower()
    if ctype == "checkbox":
        # Only consent checkboxes are safe to auto-map.  Anything else needs a
        # manual mapping or a site rule.
        pass

    ac_key = _autocomplete_key(candidate)
    sources = _candidate_sources(candidate)
    scored: List[Tuple[float, str, str, str, bool]] = []

    for fd in field_defs:
        if ctype == "password" and fd.key != "password":
            continue
        if ctype == "checkbox" and fd.key != "agree":
            continue

        best = -999.0
        best_source = ""
        best_reason = ""
        matched_sources = 0

        if ac_key == fd.key:
            best = 138.0
            best_source = "autocomplete"
            best_reason = f"autocomplete={candidate.get('autocomplete')}"

        for source, text in sources:
            weight = SOURCE_WEIGHTS.get(source, 20.0)
            for alias in fd.aliases:
                q = _quality(text, alias)
                if q <= 0:
                    continue
                score = weight * q
                if score > best:
                    best = score
                    best_source = source
                    best_reason = f"{SOURCE_LABELS.get(source, source)}匹配「{alias}」"
                    matched_sources = 1
                elif score >= best * 0.92:
                    matched_sources += 1

        if best <= 0:
            continue
        best += _type_hint(fd, candidate)
        best += _choice_hint(fd, candidate)

        negative = _negative_hit(fd, candidate)
        if negative:
            best -= 90.0
            if best_source:
                best_reason += f"（排除词「{negative}」）"

        if best < MIN_SCORE:
            continue
        if matched_sources >= 2:
            best += min(12.0, 4.0 * (matched_sources - 1))
        scored.append((best, fd.key, best_source, best_reason, False))

    if not scored:
        return MatchDecision(None, 0.0, "", "未找到可信的字段对应关系")

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else -999.0
    ambiguous = runner_up > 0 and (top[0] - runner_up) < 12.0
    score = top[0] - (8.0 if ambiguous else 0.0)
    confidence = max(0.0, min(0.99, score / 108.0))
    if confidence < 0.32:
        return MatchDecision(None, confidence, top[2], "匹配置信度过低")
    reason = top[3]
    if ambiguous:
        reason += "；存在相近字段，请确认"
    return MatchDecision(
        key=top[1],
        confidence=round(confidence, 3),
        source=top[2],
        reason=reason,
        ambiguous=ambiguous,
    )


def signature_for(candidate: Dict[str, Any]) -> str:
    """Build a stable-enough signature for a saved site rule."""
    for attr, prefix in (
        ("name", "name"),
        ("element_id", "id"),
        ("autocomplete", "ac"),
        ("label", "label"),
        ("placeholder", "ph"),
    ):
        value = (candidate.get(attr) or "").strip()
        if not value:
            continue
        n = normalize(value)
        if not n:
            continue
        if len(n) > 72:
            continue
        return f"{prefix}:{n}"
    # Last resort: type + option count + ordinal.  It may not be stable across
    # deploys, but it is still better than refusing to remember the mapping.
    return f"fallback:{candidate.get('type', 'text')}"


def value_for(
    key: str,
    values: Dict[str, Any],
    field_defs: Sequence[FieldDef],
) -> str:
    from .fields import human_key

    value = human_key(values.get(key, ""))
    if value:
        return value
    # Build a combined address when the site only has one address box.
    if key == "address":
        parts = [human_key(values.get(k, "")) for k in ("province", "city", "district", "address")]
        # Avoid duplicating the detailed address when it already starts with
        # the province/city strings.
        detail = human_key(values.get("address", ""))
        prefix = "".join(parts[:3])
        if detail and not detail.startswith(prefix):
            return prefix + detail
        return detail
    if key == "name":
        return human_key(values.get("name", ""))
    return ""


def value_candidates(key: str, value: str) -> List[str]:
    return candidate_values(key, value)

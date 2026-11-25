"""High-level scan / match / fill orchestration."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .cdp import BrowserSession, CDPError
from .fields import FieldDef, candidate_values, human_key, normalize
from .matcher import MatchDecision, match_candidate, signature_for, value_for
from .page_js import FILL_JS, SCAN_JS


@dataclass
class FieldMatch:
    candidate: Dict[str, Any]
    key: Optional[str]
    confidence: float
    source: str
    reason: str
    signature: str
    frame_id: str
    frame_path: str
    context_id: Optional[int]
    value: str = ""
    status: str = "未识别"
    kind: str = "text"
    ambiguous: bool = False
    ignored: bool = False
    result: Optional[Dict[str, Any]] = None

    @property
    def field_label(self) -> str:
        c = self.candidate
        for key in ("label", "aria_label", "placeholder", "name", "element_id", "title"):
            value = (c.get(key) or "").strip()
            if value:
                return value
        tag = c.get("tag") or "字段"
        return f"<{tag}>"

    @property
    def page_value(self) -> str:
        value = (self.candidate.get("value") or "").strip()
        if (self.candidate.get("type") or "").lower() == "password" and value:
            return "••••••"
        return value

    @property
    def is_iframe(self) -> bool:
        return " > " in self.frame_path

    @property
    def short_frame(self) -> str:
        if not self.is_iframe:
            return ""
        bits = self.frame_path.split(" > ")
        return "iframe: " + bits[-1]


class FormEngine:
    def __init__(self, browser: BrowserSession) -> None:
        self.browser = browser
        self.host = ""
        self.page_url = ""
        self.page_title = ""
        self.scan_time = 0.0
        self.logs: List[str] = []
        self._field_defs: List[FieldDef] = []

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {text}")
        if len(self.logs) > 300:
            del self.logs[:-300]

    # ------------------------------------------------------------------ scan
    def scan(
        self,
        values: Dict[str, Any],
        field_defs: Sequence[FieldDef],
        rules: Optional[Dict[str, str]] = None,
    ) -> List[FieldMatch]:
        if not self.browser.connected:
            raise CDPError("浏览器尚未连接，请先打开网页。")
        rules = rules or {}
        self._field_defs = list(field_defs)
        started = time.time()
        frames = self.browser.frames()
        if not frames:
            raise CDPError("没有读取到页面框架，请确认页面已加载。")
        self.page_url = self.browser.current_url()
        parsed = urllib.parse.urlparse(self.page_url)
        self.host = parsed.hostname or ("local-file" if parsed.scheme == "file" else "unknown")
        self.page_title = self.browser.title()

        matches: List[FieldMatch] = []
        failed_frames = 0
        for frame in frames:
            context_id = self.browser.create_context(frame["frame_id"])
            if context_id is None:
                failed_frames += 1
                continue
            try:
                result = self.browser.evaluate(SCAN_JS, context_id=context_id, timeout=45)
            except CDPError as exc:
                failed_frames += 1
                self._log(f"跳过 {frame['path']}：{exc}")
                continue
            if not isinstance(result, dict) or not result.get("fields"):
                continue
            for raw in result.get("fields") or []:
                if not isinstance(raw, dict):
                    continue
                raw["frame_id"] = frame["frame_id"]
                raw["frame_path"] = frame["path"]
                raw["frame_url"] = frame.get("url") or raw.get("frame_url") or ""
                signature = signature_for(raw)
                decision = match_candidate(raw, field_defs, rules.get(signature))
                key = decision.key
                value = value_for(key, values, field_defs) if key else ""
                status = self._status_for(raw, value, decision)
                kind = self._kind_for(raw)
                matches.append(
                    FieldMatch(
                        candidate=raw,
                        key=key,
                        confidence=decision.confidence,
                        source=decision.source,
                        reason=decision.reason,
                        signature=signature,
                        frame_id=frame["frame_id"],
                        frame_path=frame["path"],
                        context_id=context_id,
                        value=value,
                        status=status,
                        kind=kind,
                        ambiguous=decision.ambiguous,
                        ignored=decision.ignored,
                    )
                )
        self.scan_time = time.time() - started
        matched = sum(1 for m in matches if m.key)
        self._log(
            f"扫描完成：{len(matches)} 个可填字段，识别 {matched} 个，用时 {self.scan_time:.2f}s"
            + (f"，{failed_frames} 个 iframe 无法访问" if failed_frames else "")
        )
        return matches

    @staticmethod
    def _kind_for(raw: Dict[str, Any]) -> str:
        tag = (raw.get("tag") or "").lower()
        ctype = (raw.get("type") or "").lower()
        if ctype == "radio":
            return "radio"
        if ctype == "checkbox":
            return "checkbox"
        if tag == "select":
            return "select"
        if raw.get("role") == "combobox":
            return "combobox"
        if tag not in ("input", "textarea"):
            return "contenteditable"
        return "text"

    @staticmethod
    def _status_for(raw: Dict[str, Any], value: str, decision: MatchDecision) -> str:
        if decision.ignored:
            return "已忽略"
        if not decision.key:
            return "未识别"
        if not value:
            return "资料为空"
        existing = human_key(raw.get("value"))
        if existing:
            if normalize(existing) == normalize(value):
                return "已填写"
            return "已有内容"
        return "待填写"

    # ------------------------------------------------------------------ fill
    def fill(self, matches: Sequence[FieldMatch], fill_empty_only: bool = True) -> Dict[str, Any]:
        if not self.browser.connected:
            raise CDPError("浏览器尚未连接，请先打开网页。")
        candidates = [
            m for m in matches
            if m.key and m.value and not m.ignored and m.status not in ("已填写",)
        ]
        if not candidates:
            self._log("没有需要填写的字段")
            return {"filled": 0, "skipped": 0, "failed": 0, "total": 0}

        by_frame: Dict[str, List[FieldMatch]] = {}
        for item in candidates:
            by_frame.setdefault(item.frame_id, []).append(item)

        filled = skipped = failed = 0
        for frame_id, items in by_frame.items():
            context_id = self.browser.create_context(frame_id)
            if context_id is None:
                failed += len(items)
                for item in items:
                    item.status = "页面已变化"
                continue
            mappings = []
            for item in items:
                value = self._format_value(item)
                mappings.append(
                    {
                        "id": item.candidate.get("id"),
                        "key": item.key,
                        "value": value,
                        "candidates": candidate_values(item.key or "", value),
                        "kind": item.kind,
                        "fill_empty_only": bool(fill_empty_only),
                    }
                )
            try:
                expression = f"({FILL_JS})({json.dumps(mappings, ensure_ascii=True)})"
                result = self.browser.evaluate(
                    expression,
                    context_id=context_id,
                    await_promise=True,
                    timeout=90,
                )
            except CDPError as exc:
                self._log(f"填写 {frame_id[:8]} 失败：{exc}")
                failed += len(items)
                for item in items:
                    item.status = "填写失败"
                continue
            raw_results = (result or {}).get("results") or []
            by_id = {str(r.get("id")): r for r in raw_results if isinstance(r, dict)}
            for item in items:
                res = by_id.get(str(item.candidate.get("id")))
                item.result = res
                if not res:
                    failed += 1
                    item.status = "未执行"
                    continue
                if res.get("ok"):
                    if res.get("skipped"):
                        skipped += 1
                        item.status = "已有内容"
                    else:
                        filled += 1
                        item.status = "已填写"
                else:
                    failed += 1
                    item.status = "填写失败"
            self._log(
                f"填写 {items[0].frame_path if items else frame_id[:8]}："
                f"{sum(1 for i in items if i.status == '已填写')}/{len(items)} 成功"
            )
        summary = {"filled": filled, "skipped": skipped, "failed": failed, "total": len(candidates)}
        self._log(f"填写完成：成功 {filled}，已有内容 {skipped}，失败 {failed}")
        return summary

    @staticmethod
    def _format_value(item: FieldMatch) -> str:
        value = item.value
        ctype = (item.candidate.get("type") or "").lower()
        if item.key == "birthday" and ctype in ("date", "month"):
            value = _to_date_value(value, ctype)
        return value

    def refresh_values(self, matches: Sequence[FieldMatch], values: Dict[str, Any]) -> None:
        """Re-read profile values after the user edits the form."""
        for item in matches:
            if not item.key or item.ignored:
                continue
            item.value = value_for(item.key, values, self._field_defs)
            if not item.value:
                item.status = "资料为空"
            elif item.status in ("资料为空", "待填写", "未识别"):
                existing = human_key(item.candidate.get("value"))
                item.status = "已有内容" if existing else "待填写"

    # ------------------------------------------------------------------ misc
    def refresh_page_meta(self) -> None:
        self.page_url = self.browser.current_url()
        self.page_title = self.browser.title()
        parsed = urllib.parse.urlparse(self.page_url)
        self.host = parsed.hostname or ("local-file" if parsed.scheme == "file" else "unknown")

    def field_label(self, key: Optional[str]) -> str:
        if not key:
            return "—"
        for fd in self._field_defs:
            if fd.key == key:
                return fd.label
        return key

    def field_defs(self) -> List[FieldDef]:
        return list(self._field_defs)


def _to_date_value(value: str, ctype: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    if ctype == "date":
        match = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$", value)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = re.match(r"^(\d{4})(\d{2})(\d{2})$", value)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if ctype == "month":
        match = re.match(r"^(\d{4})[-/.年](\d{1,2})月?$", value)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
    return value

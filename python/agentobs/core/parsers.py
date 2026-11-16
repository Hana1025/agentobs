"""模型输出解析器。解析失败抛 ParseError，llm_call 视为可重试错误。"""

from __future__ import annotations

import json
import re
from typing import Any


class ParseError(ValueError):
    pass


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def text(raw: str) -> str:
    return raw


def json_object(raw: str) -> dict[str, Any]:
    """解析一个 JSON 对象；容忍 ``` 围栏和前后空白；不是对象或解析失败则抛 ParseError。"""
    if raw is None:
        raise ParseError("empty output")
    s = raw.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1)
    try:
        # strict=False：字符串值里的控制字符不算解析失败（由 agentobs.output.control_chars 另行计数）
        obj = json.loads(s, strict=False)
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON: {e.msg} at {e.pos}") from e
    if not isinstance(obj, dict):
        raise ParseError(f"expected JSON object, got {type(obj).__name__}")
    return obj

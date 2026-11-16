"""正文脱敏。只在 capture_content=True 时使用；默认不采集正文。"""

from __future__ import annotations

import re
from typing import Protocol

from agentobs.core.attrs import CONTENT_MAX_CHARS


class Redactor(Protocol):
    def redact(self, text: str) -> str: ...


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # API key / token 样式：sk-…、长 base64/hex 串
    (re.compile(r"\b(?:sk|pk|rk|key|token)[-_][A-Za-z0-9_-]{16,}\b", re.I), "<secret>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<hex>"),
    # 电话 / 长数字串（身份证、卡号等）
    (re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)"), "<number>"),
    (re.compile(r"(?:\d{1,3}\.){3}\d{1,3}"), "<ip>"),
]


class DefaultRedactor:
    """保守的正则脱敏：邮箱、密钥样式、长 hex、长数字串、IP。可整体替换。"""

    def __init__(self, max_chars: int = CONTENT_MAX_CHARS, extra: list[tuple[str, str]] | None = None):
        self.max_chars = max_chars
        self.patterns = list(_PATTERNS)
        for pat, repl in extra or []:
            self.patterns.append((re.compile(pat), repl))

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for pat, repl in self.patterns:
            out = pat.sub(repl, out)
        if len(out) > self.max_chars:
            out = out[: self.max_chars] + f"…[truncated {len(out) - self.max_chars} chars]"
        return out


class NoopRedactor:
    def redact(self, text: str) -> str:
        return text

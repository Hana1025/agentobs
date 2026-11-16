"""由种子生成假文档。页面文本是随机音节拼出来的伪词，不是任何真实语言内容。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

_SYLLABLES = ["ka", "lo", "mi", "ne", "ru", "ta", "vo", "zi", "pe", "su", "da", "fo"]


@dataclass
class Page:
    index: int
    kind: str  # answer | scratch
    text: str


@dataclass
class Document:
    doc_id: str
    pages: list[Page] = field(default_factory=list)
    true_score: float = 0.0  # 标准答案：合成的“真实”得分 0–100

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    @property
    def answer_pages(self) -> list[int]:
        return [p.index for p in self.pages if p.kind == "answer"]

    @property
    def scratch_pages(self) -> list[int]:
        return [p.index for p in self.pages if p.kind == "scratch"]


def _fake_text(rng: random.Random, words: int) -> str:
    return " ".join(
        "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 4))) for _ in range(words)
    )


def make_document(doc_id: str, rng: random.Random) -> Document:
    n = rng.randint(4, 12)
    n_scratch = rng.randint(0, max(1, n // 4))
    scratch_idx = set(rng.sample(range(n), n_scratch))
    pages = [
        Page(i, "scratch" if i in scratch_idx else "answer", _fake_text(rng, rng.randint(30, 80)))
        for i in range(n)
    ]
    # 真实得分集中在 55–95
    score = max(0.0, min(100.0, rng.gauss(75, 10)))
    return Document(doc_id, pages, round(score, 1))

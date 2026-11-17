"""M6 验收：INTEGRATION.md 的示例代码能直接运行，且文档代码块与脚本逐字一致。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]
REPO = PYTHON_DIR.parent
DOC = REPO / "docs" / "INTEGRATION.md"

_BLOCK = re.compile(
    r"<!-- example: (?P<path>[^ ]+) -->\s*\n```(?:python|yaml)\n(?P<code>.*?)\n```", re.S
)


def run_example(
    module: str, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """在 python/ 目录下以子进程运行示例；默认去掉外部的队列和 OTLP 配置，保证离线可跑。"""
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTOBS_")}
    full_env.update({"PYTHONIOENCODING": "utf-8", **(env or {})})
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=PYTHON_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=full_env,
        timeout=120,
    )


def _contains_block(file_text: str, block: str) -> bool:
    """block 的每一行，加上同一段缩进后，在文件里是连续的几行。"""
    lines = file_text.splitlines()
    want = block.splitlines()
    for i, line in enumerate(lines):
        if not line.endswith(want[0]):
            continue
        indent = line[: len(line) - len(want[0])]
        if indent.strip():
            continue
        if lines[i : i + len(want)] == [(indent + w) if w else "" for w in want]:
            return True
    return False


def test_doc_blocks_match_example_scripts():
    text = DOC.read_text(encoding="utf-8")
    blocks = list(_BLOCK.finditer(text))
    assert len(blocks) >= 5
    for m in blocks:
        src = (REPO / m["path"]).read_text(encoding="utf-8")
        assert _contains_block(src, m["code"]), f"doc block out of sync with {m['path']}"


def test_contains_block_helper():
    src = "def f():\n    a = 1\n\n    b = 2\n"
    assert _contains_block(src, "a = 1\n\nb = 2")
    assert not _contains_block(src, "a = 1\nb = 2")


@pytest.mark.parametrize(
    ("module", "expect"),
    [
        ("examples.quickstart", "pages_with_text=3"),
        ("examples.custom_rule", "fired missing_input_pages"),
        ("examples.langchain_chain", "gen_ai.usage.input_tokens = 42"),
    ],
)
def test_example_runs(module, expect):
    r = run_example(module)
    assert r.returncode == 0, r.stderr
    assert expect in r.stdout


def test_quickstart_reports_cost():
    r = run_example("examples.quickstart")
    assert "cost_usd=0.000168" in r.stdout

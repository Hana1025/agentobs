"""依次运行不依赖数据库的示例（make examples）。事务模式示例需要 pgmq，单独运行。"""

from __future__ import annotations

import subprocess
import sys

EXAMPLES = ["examples.quickstart", "examples.custom_rule", "examples.langchain_chain"]


def main() -> int:
    failed = []
    for mod in EXAMPLES:
        print(f"== {mod}", flush=True)
        if subprocess.run([sys.executable, "-m", mod]).returncode != 0:
            failed.append(mod)
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

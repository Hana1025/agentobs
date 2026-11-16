"""server 内部 API 客户端。"""

from __future__ import annotations

from typing import Any

import httpx


class ServerClient:
    def __init__(self, base_url: str, token: str, timeout_s: float = 15):
        self._c = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Agentobs-Token": token},
            timeout=timeout_s,
        )

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        r = self._c.get(f"/api/internal/traces/{trace_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_baselines(self, project_id: str, window: int = 200) -> dict[str, Any]:
        r = self._c.get(
            "/api/internal/baselines", params={"project_id": project_id, "window": window}
        )
        r.raise_for_status()
        return r.json()

    def post_triage(self, payload: dict[str, Any]) -> str:
        """返回 'created' | 'exists'。"""
        r = self._c.post("/api/internal/triage", json=payload)
        if r.status_code == 409:
            return "exists"
        r.raise_for_status()
        return "created"

    def post_eval_cases(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        r = self._c.post("/api/internal/eval-cases", json={"cases": cases})
        r.raise_for_status()
        return r.json()

    def post_eval_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._c.post("/api/internal/eval-runs", json=payload)
        r.raise_for_status()
        return r.json()

    def list_traces(
        self, project_id: str, from_iso: str, to_iso: str, page: int = 0, size: int = 100
    ) -> dict[str, Any]:
        r = self._c.get(
            "/api/traces",
            params={
                "project_id": project_id,
                "from": from_iso,
                "to": to_iso,
                "page": page,
                "size": size,
                "sort": "start_time",
            },
        )
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._c.close()

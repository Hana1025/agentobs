"""合成流水线：extract → structure → score。每个阶段用 st.set_metric 上报数值，供分诊使用。"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import agentobs as ao
from agentobs.core import attrs
from demo.faults import FaultPlan
from demo.mock_llm import MockModelCall
from demo.synth import Document

log = logging.getLogger("demo")

DISAGREEMENT_THRESHOLD = 15.0


@dataclass
class PipelineConfig:
    provider: str = "mock"
    fast_model: str = "mock/fast"
    strong_model: str = "mock/strong"
    judge_model: str = "mock/judge"
    attempt_timeout_s: float = 1.5
    deadline_s: float = 12.0  # 要容得下 2 次超时 + 退避（2s、4s）+ 1 次成功
    max_attempts: int = 3
    speed: float = 1.0  # mock 延迟倍率
    live_client: Any = None  # openai 兼容客户端（max_retries=0）；None = mock


class Pipeline:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg

    # ---------- 模型调用 ----------

    def _call(
        self,
        rng: random.Random,
        plan: FaultPlan,
        stage: str,
        model: str,
        payload: dict,
        base_latency: float,
    ):
        cfg = self.cfg
        if cfg.live_client is not None:
            client = cfg.live_client
            prompt = json.dumps({"task": stage, "data": payload}, ensure_ascii=False)
            return ao.llm_call(
                lambda t: client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Return the JSON object given in the user message unchanged.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    timeout=t,
                ),
                provider=cfg.provider,
                model=model,
                attempt_timeout_s=cfg.attempt_timeout_s,
                deadline_s=cfg.deadline_s,
                max_attempts=cfg.max_attempts,
                parse=ao.parsers.json_object,
                input_content=prompt,
            )
        mock = MockModelCall(rng, plan, stage, model, speed=cfg.speed)
        return ao.llm_call(
            lambda t: mock.create(timeout=t, payload=payload, base_latency=base_latency),
            provider="mock",
            model=model,
            attempt_timeout_s=cfg.attempt_timeout_s,
            deadline_s=cfg.deadline_s,
            max_attempts=cfg.max_attempts,
            parse=ao.parsers.json_object,
            input_content=json.dumps(payload)[:200],
        )

    # ---------- 阶段 ----------

    def extract(self, doc: Document, plan: FaultPlan, rng: random.Random) -> dict[str, Any]:
        with ao.stage("extract") as st:
            answer_pages = list(doc.answer_pages)
            if plan.swap_scratch_for_answer and doc.scratch_pages and len(answer_pages) > 1:
                # 把一个草稿页当答题页，漏掉一个答题页：页数对得上，但内容错了
                answer_pages.remove(rng.choice(answer_pages))
                answer_pages.append(rng.choice(doc.scratch_pages))
                answer_pages.sort()
            payload = {"pages": doc.n_pages, "answer_pages": answer_pages}
            r = self._call(rng, plan, "extract", self.cfg.fast_model, payload, 0.15)
            out = r.output
            st.set_metric("pages", doc.n_pages)
            st.set_metric("answer_pages_detected", len(out["answer_pages"]))
            st.set_metric("scratch_pages_detected", doc.n_pages - len(out["answer_pages"]))
            return out

    def structure(
        self, doc: Document, extracted: dict, plan: FaultPlan, rng: random.Random
    ) -> dict[str, Any]:
        with ao.stage("structure") as st:
            mapped = list(extracted["answer_pages"])
            if plan.drop_pages:
                for _ in range(min(plan.drop_pages, max(0, len(mapped) - 1))):
                    mapped.pop(rng.randrange(len(mapped)))
            payload = {"pages_mapped": mapped, "items": [{"page": p, "fields": 3} for p in mapped]}
            r = self._call(rng, plan, "structure", self.cfg.strong_model, payload, 0.3)
            out = r.output
            st.set_metric("pages_mapped", len(out["pages_mapped"]))
            st.set_metric("items", len(out["items"]))
            return out

    def score(
        self, doc: Document, structured: dict, plan: FaultPlan, rng: random.Random
    ) -> dict[str, Any]:
        with ao.stage("score") as st:
            # 真实得分受错误输入影响：少页 / 草稿页 → 偏低
            n_expected = max(1, len(doc.answer_pages))
            n_mapped = len(structured["pages_mapped"])
            base = doc.true_score * min(1.0, n_mapped / n_expected)
            extra_disagreement = plan.disagreement
            if plan.swap_scratch_for_answer:
                # 草稿页混进答题页：分数偏低，且两个打分模型分歧略增（仍低于 disagreement 阈值）
                base *= 0.8
                extra_disagreement += rng.uniform(8, 14)
            base += plan.score_shift
            base = max(0.0, min(100.0, base))
            s1 = round(max(0.0, min(100.0, base + rng.gauss(0, 2))), 1)
            s2 = round(max(0.0, min(100.0, base + rng.gauss(0, 2) + extra_disagreement)), 1)
            r1 = self._call(rng, plan, "score", self.cfg.strong_model, {"score": s1}, 0.25)
            r2 = self._call(rng, plan, "score", self.cfg.judge_model, {"score": s2}, 0.25)
            a, b = float(r1.output["score"]), float(r2.output["score"])
            final = round((a + b) / 2, 1)
            st.set_metric("score", final)
            st.set_metric("score_a", a)
            st.set_metric("score_b", b)
            st.set_metric("disagreement", round(abs(a - b), 1))
            st.set_metric("disagreement_flag", 1 if abs(a - b) > DISAGREEMENT_THRESHOLD else 0)
            return {"score": final, "disagreement": abs(a - b)}

    # ---------- 整个 job ----------

    def run_job(self, job_id: str, doc: Document, plan: FaultPlan, seed: int) -> dict[str, Any]:
        rng = random.Random(seed)
        with ao.job(
            job_id, attributes={"pages": doc.n_pages, "answer_pages": len(doc.answer_pages)}
        ) as job:
            # 标准答案只在 demo 写到根 span，供 make eval 使用
            exp = plan.expected
            job.set_attribute(attrs.EVAL_PREFIX + "fault_type", plan.fault_type)
            job.set_attribute(attrs.EVAL_PREFIX + "expected_label", exp["label"] or "normal")
            job.set_attribute(attrs.EVAL_PREFIX + "expected_stage", exp["stage"] or "none")
            if exp["root_cause"]:
                job.set_attribute(attrs.EVAL_PREFIX + "expected_root_cause", exp["root_cause"])
            extracted = self.extract(doc, plan, rng)
            structured = self.structure(doc, extracted, plan, rng)
            result = self.score(doc, structured, plan, rng)
            job.set_metric("score", result["score"])
            return {"job_id": job_id, "trace_id": job.trace_id, **result}

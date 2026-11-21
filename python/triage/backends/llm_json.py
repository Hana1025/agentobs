"""llm_json 后端：OpenAI 兼容接口。

每个问题一次调用，选项编码成单 token 字母（A/B/C…）：
- 有 logprobs：从首个 token 的 top_logprobs 里取各字母的概率并归一化；
- 没有：采样 k=5（temperature 0.7）按投票频率算。
不使用模型口头报告的置信度。另有 MockLLMJsonDecider（确定性，用于 CI / 无 key 环境）。
"""

from __future__ import annotations

import logging
import math
import string
import time
from typing import Any

from agentobs.core.pricing import PricingTable
from triage.models import Answer, Question, TraceState
from triage.pricing import resolve_usage_cost

log = logging.getLogger("triage")

LETTERS = string.ascii_uppercase

SYSTEM_PROMPT = (
    "You are a reliability analyst for multi-stage LLM pipelines. You are given a compressed, "
    "numeric-only state of one job run (no raw text). Answer the question with a single capital letter "
    "corresponding to one of the listed options. Output only the letter."
)


def build_prompt(state: TraceState, q: Question) -> tuple[str, dict[str, str]]:
    opts = q.choices()
    mapping = {LETTERS[i]: o for i, o in enumerate(opts)}
    lines = [
        "STATE (JSON):",
        state.model_dump_json(exclude_none=True),
        "",
        f"QUESTION: {q.prompt}",
        "OPTIONS:",
    ]
    for letter, o in mapping.items():
        lines.append(f"{letter}. {o}")
    lines.append("Answer with a single letter.")
    return "\n".join(lines), mapping


class LLMJsonDecider:
    name = "llm_json"
    version = "1"

    def __init__(
        self,
        client: Any,
        model: str,
        samples: int = 5,
        temperature: float = 0.7,
        timeout_s: float = 30,
        pricing: PricingTable | None = None,
    ):
        self.client = client
        self.model = model
        self.samples = samples
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.pricing = pricing
        self.last_cost_usd = 0.0
        self.last_latency_s = 0.0

    def decide(self, state: TraceState, questions: list[Question]) -> list[Answer]:
        t0 = time.monotonic()
        self.last_cost_usd = 0.0
        out = []
        for q in questions:
            prompt, mapping = build_prompt(state, q)
            probs = self._with_logprobs(prompt, mapping)
            if probs is None:
                probs = self._by_sampling(prompt, mapping)
            out.append(Answer.from_probs(q.id, probs))
        self.last_latency_s = time.monotonic() - t0
        return out

    def _create(self, prompt: str, **kw: Any) -> Any:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1,
            timeout=self.timeout_s,
            **kw,
        )
        cost = resolve_usage_cost(resp, self.model, self.pricing)
        if cost:
            self.last_cost_usd += cost
        return resp

    def _with_logprobs(self, prompt: str, mapping: dict[str, str]) -> dict[str, float] | None:
        try:
            resp = self._create(
                prompt, temperature=0, logprobs=True, top_logprobs=min(20, max(5, len(mapping)))
            )
        except Exception as e:  # noqa: BLE001  供应商不支持 logprobs 时回退到采样
            log.info(
                "llm_json: logprobs unavailable (%s), falling back to sampling", type(e).__name__
            )
            return None
        try:
            content = resp.choices[0].logprobs.content
            top = content[0].top_logprobs
        except (AttributeError, IndexError, TypeError):
            return None
        probs: dict[str, float] = {o: 0.0 for o in mapping.values()}
        found = False
        for item in top:
            tok = (item.token or "").strip().upper()
            if tok in mapping:
                probs[mapping[tok]] += math.exp(item.logprob)
                found = True
        if not found:
            return None
        # 未出现在 top_logprobs 里的选项给一点质量，避免 0 概率
        for k in probs:
            probs[k] += 1e-4
        return probs

    def _by_sampling(self, prompt: str, mapping: dict[str, str]) -> dict[str, float]:
        counts: dict[str, float] = {o: 0.0 for o in mapping.values()}
        for _ in range(self.samples):
            try:
                resp = self._create(prompt, temperature=self.temperature)
                tok = (resp.choices[0].message.content or "").strip().upper()[:1]
            except Exception:  # noqa: BLE001
                continue
            if tok in mapping:
                counts[mapping[tok]] += 1
        if sum(counts.values()) == 0:
            # 全部失败：均匀分布，置信度自然最低
            return {o: 1.0 for o in mapping.values()}
        for k in counts:
            counts[k] += 0.05
        return counts


class MockLLMJsonDecider:
    """确定性的 S1 mock：按状态的数值特征给出“像模型一样”带概率的答案。

    不是规则后端的复制：它额外用基线 metric（score 偏离、页数、分歧）去抓规则抓不到的
    scratch_as_answer / score_outlier，用来衡量 S1 的增量价值。
    """

    name = "llm_json_mock"
    version = "1"
    last_cost_usd = 0.0
    last_latency_s = 0.0

    def decide(self, state: TraceState, questions: list[Question]) -> list[Answer]:
        # 证据打分：每条证据给 suspicious / failed 加权
        susp = 0.0
        failed = 0.0
        stage_votes: dict[str, float] = {}
        missing = 0.0

        if state.job.status == "ERROR":
            failed += 3.0
        for name, st in state.stages.items():
            r = sum(st.retries.values())
            infra = any(k in st.retries for k in ("server_error", "connection", "timeout"))
            if st.status == "ERROR":
                failed += 1.5
                stage_votes["infrastructure" if infra else name] = (
                    stage_votes.get("infrastructure" if infra else name, 0) + 2.0
                )
            if r >= 2:
                susp += 1.0
                stage_votes[name] = stage_votes.get(name, 0) + 1.0
            if st.duration_ratio and st.duration_ratio > 3:
                susp += 1.5
                stage_votes[name] = stage_votes.get(name, 0) + 1.5
            if st.parse_failures or st.control_chars:
                susp += 1.5
                stage_votes[name] = stage_votes.get(name, 0) + 1.5
            if st.cost_ratio and st.cost_ratio > 3:
                susp += 1.0
                stage_votes[name] = stage_votes.get(name, 0) + 0.5
            # 基线 metric 偏离（规则抓不到的类型靠这里）
            score_v, score_base = st.metrics.get("score"), st.baseline_metrics.get("score")
            dis_v, dis_base = (
                st.metrics.get("disagreement"),
                st.baseline_metrics.get("disagreement"),
            )
            if score_v is not None and score_base:
                if score_v < score_base * 0.65:
                    susp += 2.0  # 远低于分布：scoring_anomaly
                    stage_votes["score"] = stage_votes.get("score", 0) + 2.0
                elif score_v < score_base * 0.78:
                    susp += 1.0
                    stage_votes["score"] = stage_votes.get("score", 0) + 0.8
                # 分数偏低 + 分歧略增（低于硬阈值）：像是输入页选错了
                if (
                    dis_v is not None
                    and dis_base is not None
                    and dis_v > max(5.0, 2.5 * dis_base)
                    and score_v < score_base * 0.95
                ):
                    susp += 1.8
                    missing += 1.2
                    stage_votes["extract"] = stage_votes.get("extract", 0) + 1.8
            if name == "score" and st.metrics.get("disagreement_flag", 0) >= 1:
                susp += 2.0
                stage_votes["score"] = stage_votes.get("score", 0) + 2.0
        # 页数关系
        structure = state.stages.get("structure")
        expected_pages = state.job.metrics.get("answer_pages")
        if (
            structure
            and expected_pages
            and structure.metrics.get("pages_mapped", expected_pages) < expected_pages
        ):
            susp += 1.5
            missing += 1.5
            stage_votes["structure"] = stage_votes.get("structure", 0) + 1.5
        # 一次 429 后成功不算可疑
        health = _softmax({"normal": 1.2, "suspicious": susp, "failed": failed}, temp=0.9)

        out = []
        for q in questions:
            if q.id == "health":
                out.append(Answer.from_probs(q.id, health))
            elif q.id == "suspect_stage":
                raw = {o: 0.05 for o in q.choices()}
                for s, v in stage_votes.items():
                    if s in raw:
                        raw[s] += v
                if not stage_votes:
                    raw["none"] += 2.0
                out.append(Answer.from_probs(q.id, _softmax(raw, temp=0.8)))
            elif q.id == "missing_input":
                out.append(Answer.from_probs(q.id, _softmax({"yes": missing, "no": 1.0}, temp=0.7)))
            elif q.id == "severity":
                score = failed * 1.2 + susp * 0.6
                sev = "3" if score >= 3 else "2" if score >= 1.8 else "1" if score >= 0.8 else "0"
                raw = {s: 0.1 for s in q.choices()}
                raw[sev] = 1.5
                out.append(Answer.from_probs(q.id, _softmax(raw, temp=0.5)))
            else:
                opts = q.choices()
                out.append(Answer.from_probs(q.id, {o: 1.0 / len(opts) for o in opts}))
        return out


def _softmax(raw: dict[str, float], temp: float = 1.0) -> dict[str, float]:
    mx = max(raw.values())
    ex = {k: math.exp((v - mx) / temp) for k, v in raw.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}

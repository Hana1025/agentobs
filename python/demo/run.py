"""demo 入口。

python -m demo.run --rate 6/min --seed 42        # 持续产生流量
python -m demo.run --batch 300 --seed 42         # 一次性生成评测集
python -m demo.run --batch 20 --fault none       # 只跑正常样本（M2）
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time

import agentobs as ao
from demo.faults import FAULT_TYPES, choose_fault, plan_fault
from demo.pipeline import Pipeline, PipelineConfig
from demo.synth import make_document

log = logging.getLogger("demo")


def parse_rate(s: str) -> float:
    """'6/min' | '0.5/s' | '120/h' -> 每秒速率"""
    n, unit = s.split("/")
    per = {"s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600, "hour": 3600}[unit.strip()]
    return float(n) / per


def build_pipeline(args: argparse.Namespace) -> Pipeline:
    cfg = PipelineConfig(speed=args.speed)
    api_key = os.environ.get("LLM_API_KEY", "")
    if api_key and not args.offline:
        from openai import OpenAI

        cfg.live_client = OpenAI(
            api_key=api_key, base_url=os.environ.get("LLM_BASE_URL") or None, max_retries=0
        )
        cfg.provider = "openai-compatible"
        model = os.environ.get("DEMO_MODEL", "")
        if model:
            cfg.fast_model = cfg.strong_model = cfg.judge_model = model
        cfg.attempt_timeout_s, cfg.deadline_s = 60, 300
        log.info("demo: using live model endpoint")
    else:
        log.info("demo: using mock models")
    return cfg_to_pipeline(cfg)


def cfg_to_pipeline(cfg: PipelineConfig) -> Pipeline:
    return Pipeline(cfg)


def init_sdk(args: argparse.Namespace) -> None:
    queue = None
    url = os.environ.get("AGENTOBS_QUEUE_URL") or os.environ.get("AGENTOBS_QUEUE_DSN")
    if url and not args.no_queue:
        queue = ao.queue_from_url(url)  # postgresql:// → pgmq，amqp:// → RabbitMQ
    pricing = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "agentobs", "pricing.example.yaml"
    )
    ao.init(
        service_name="demo-pipeline",
        project_id=args.project,
        otlp_endpoint=os.environ.get("AGENTOBS_OTLP_ENDPOINT") if not args.no_otlp else None,
        metrics_port=int(os.environ.get("AGENTOBS_METRICS_PORT", "0")) or None,
        capture_content=args.capture_content,
        queue=queue,
        pricing=pricing,
    )


def run_one(
    pipeline: Pipeline, i: int, rng: random.Random, fault: str | None, normal_ratio: float
) -> None:
    job_id = f"job-{i:05d}"
    seed = rng.randrange(1 << 30)
    doc = make_document(f"doc-{i:05d}", random.Random(seed))
    ft = fault or choose_fault(rng, normal_ratio)
    plan = plan_fault(ft, random.Random(seed + 1))
    t0 = time.monotonic()
    try:
        out = pipeline.run_job(job_id, doc, plan, seed + 2)
        log.info("%s fault=%s score=%s %.2fs", job_id, ft, out["score"], time.monotonic() - t0)
    except Exception as e:  # noqa: BLE001  demo 里 job 失败是预期的注入结果
        log.info(
            "%s fault=%s FAILED (%s) %.2fs", job_id, ft, type(e).__name__, time.monotonic() - t0
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="agentobs synthetic demo pipeline")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--rate", default=None, help="持续模式速率，如 6/min")
    mode.add_argument("--batch", type=int, default=None, help="一次性生成 N 个 job")
    p.add_argument("--seed", type=int, default=int(os.environ.get("DEMO_SEED", "42")))
    p.add_argument("--project", default=os.environ.get("AGENTOBS_PROJECT_ID", "demo"))
    p.add_argument("--fault", choices=list(FAULT_TYPES), default=None, help="固定故障类型")
    p.add_argument("--normal-ratio", type=float, default=0.7)
    p.add_argument("--speed", type=float, default=float(os.environ.get("DEMO_SPEED", "1.0")))
    p.add_argument("--start-index", type=int, default=0)
    p.add_argument("--capture-content", action="store_true")
    p.add_argument("--offline", action="store_true", help="忽略 LLM_API_KEY，强制 mock")
    p.add_argument("--no-queue", action="store_true")
    p.add_argument("--no-otlp", action="store_true")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    init_sdk(args)
    pipeline = build_pipeline(args)
    rng = random.Random(args.seed)
    try:
        if args.batch is not None:
            for i in range(args.start_index, args.start_index + args.batch):
                run_one(pipeline, i, rng, args.fault, args.normal_ratio)
        else:
            interval = 1.0 / parse_rate(args.rate or os.environ.get("DEMO_RATE", "6/min"))
            i = args.start_index
            while True:
                t = time.monotonic()
                run_one(pipeline, i, rng, args.fault, args.normal_ratio)
                i += 1
                time.sleep(max(0.0, interval - (time.monotonic() - t)))
    except KeyboardInterrupt:
        pass
    finally:
        ao.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

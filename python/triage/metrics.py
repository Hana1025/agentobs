from prometheus_client import Counter, Histogram

triage_total = Counter("agentobs_triage_total", "Triage results", ["route", "s1_label", "backend"])
triage_confidence = Histogram(
    "agentobs_triage_confidence", "S1 confidence", buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]
)
triage_cost_usd_total = Counter("agentobs_triage_cost_usd_total", "Triage model cost", ["system"])
triage_latency_seconds = Histogram(
    "agentobs_triage_latency_seconds", "Triage latency", ["system"], buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60]
)
triage_dlq_total = Counter("agentobs_triage_dlq_total", "Messages sent to DLQ", ["reason"])
triage_deferred_total = Counter("agentobs_triage_deferred_total", "Messages left for retry", ["reason"])

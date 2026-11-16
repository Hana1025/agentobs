package dev.agentobs.query.dto;

public record MetricsSummary(
        long jobs, long succeeded, Double successRate, Double p95DurationMs, Double avgCostUsd,
        Double escalationRate, long pendingReviews) {
}

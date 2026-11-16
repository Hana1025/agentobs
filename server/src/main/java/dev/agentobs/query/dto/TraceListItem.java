package dev.agentobs.query.dto;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;

public record TraceListItem(
        String traceId, String projectId, String jobId,
        OffsetDateTime startTime, OffsetDateTime endTime, double durationMs, String rootStatus,
        int spanCount, int errorCount, Long inputTokens, Long outputTokens, BigDecimal costUsd, Integer maxAttempt,
        Long triageId, List<String> rulesFired, String s1Label, Double s1Confidence, String route,
        String s2RootCause, Double s2Confidence, String reviewStatus, int severity, String suspectStage) {
}

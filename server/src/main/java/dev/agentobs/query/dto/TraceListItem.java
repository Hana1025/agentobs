package dev.agentobs.query.dto;

import jakarta.validation.constraints.NotNull;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;

/** @NotNull 标出始终有值的字段（用于 OpenAPI required）；其余字段为 null 时 JSON 里省略。 */
public record TraceListItem(
        @NotNull String traceId, @NotNull String projectId, String jobId,
        @NotNull OffsetDateTime startTime, @NotNull OffsetDateTime endTime, double durationMs, @NotNull String rootStatus,
        int spanCount, int errorCount, Long inputTokens, Long outputTokens, BigDecimal costUsd, Integer maxAttempt,
        Long triageId, @NotNull List<String> rulesFired, String s1Label, Double s1Confidence, String route,
        String s2RootCause, Double s2Confidence, @NotNull String reviewStatus, int severity, String suspectStage) {
}

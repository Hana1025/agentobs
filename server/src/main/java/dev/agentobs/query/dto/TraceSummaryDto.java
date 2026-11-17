package dev.agentobs.query.dto;

import jakarta.validation.constraints.NotNull;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

public record TraceSummaryDto(
        @NotNull String traceId, @NotNull String projectId, String jobId,
        @NotNull OffsetDateTime startTime, @NotNull OffsetDateTime endTime,
        boolean hasRoot, String rootStatus, int spanCount, int errorCount,
        Long inputTokens, Long outputTokens, BigDecimal costUsd, Integer maxAttempt) {
}

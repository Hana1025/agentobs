package dev.agentobs.query.dto;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

public record TraceSummaryDto(
        String traceId, String projectId, String jobId, OffsetDateTime startTime, OffsetDateTime endTime,
        boolean hasRoot, String rootStatus, int spanCount, int errorCount,
        Long inputTokens, Long outputTokens, BigDecimal costUsd, Integer maxAttempt) {
}

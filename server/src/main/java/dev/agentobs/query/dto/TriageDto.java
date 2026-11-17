package dev.agentobs.query.dto;

import jakarta.validation.constraints.NotNull;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

public record TriageDto(
        long id, @NotNull String traceId, @NotNull String projectId, String jobId, @NotNull String triageVersion,
        @NotNull List<String> rulesFired, @NotNull Map<String, Object> state,
        String s1Backend, Map<String, Object> s1Answers, String s1Label, Double s1Confidence,
        @NotNull String route, String s2Model, String s2RootCause, Double s2Confidence,
        List<Map<String, Object>> s2Evidence, String s2Summary, @NotNull String reviewStatus,
        @NotNull BigDecimal triageCostUsd, @NotNull OffsetDateTime createdAt) {
}

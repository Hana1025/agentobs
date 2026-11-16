package dev.agentobs.query.dto;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

public record TriageDto(
        long id, String traceId, String projectId, String jobId, String triageVersion,
        List<String> rulesFired, Map<String, Object> state,
        String s1Backend, Map<String, Object> s1Answers, String s1Label, Double s1Confidence,
        String route, String s2Model, String s2RootCause, Double s2Confidence,
        List<Map<String, Object>> s2Evidence, String s2Summary, String reviewStatus,
        BigDecimal triageCostUsd, OffsetDateTime createdAt) {
}

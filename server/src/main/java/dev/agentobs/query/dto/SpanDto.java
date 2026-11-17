package dev.agentobs.query.dto;

import jakarta.validation.constraints.NotNull;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

public record SpanDto(
        @NotNull String spanId, String parentSpanId, @NotNull String name, String kind,
        @NotNull OffsetDateTime startTime, @NotNull OffsetDateTime endTime, double durationMs,
        @NotNull String statusCode, String statusMessage, String stage, String model,
        Integer inputTokens, Integer outputTokens, BigDecimal costUsd, Integer attempt,
        @NotNull Map<String, Object> attributes, @NotNull List<Map<String, Object>> events, int depth) {

    public SpanDto withDepth(int d) {
        return new SpanDto(spanId, parentSpanId, name, kind, startTime, endTime, durationMs, statusCode, statusMessage,
                stage, model, inputTokens, outputTokens, costUsd, attempt, attributes, events, d);
    }
}

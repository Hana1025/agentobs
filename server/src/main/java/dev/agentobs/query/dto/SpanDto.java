package dev.agentobs.query.dto;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

public record SpanDto(
        String spanId, String parentSpanId, String name, String kind,
        OffsetDateTime startTime, OffsetDateTime endTime, double durationMs,
        String statusCode, String statusMessage, String stage, String model,
        Integer inputTokens, Integer outputTokens, BigDecimal costUsd, Integer attempt,
        Map<String, Object> attributes, List<Map<String, Object>> events, int depth) {

    public SpanDto withDepth(int d) {
        return new SpanDto(spanId, parentSpanId, name, kind, startTime, endTime, durationMs, statusCode, statusMessage,
                stage, model, inputTokens, outputTokens, costUsd, attempt, attributes, events, d);
    }
}

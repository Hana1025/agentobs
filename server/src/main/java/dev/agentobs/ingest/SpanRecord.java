package dev.agentobs.ingest;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

/** obs.spans 的一行（入库用）。attributes / events 是序列化后的 JSON 文本。 */
public record SpanRecord(
        String traceId,
        String spanId,
        String parentSpanId,
        String projectId,
        String serviceName,
        String name,
        String kind,
        OffsetDateTime startTime,
        OffsetDateTime endTime,
        String statusCode,
        String statusMessage,
        String jobId,
        String stage,
        String model,
        Integer inputTokens,
        Integer outputTokens,
        BigDecimal costUsd,
        Integer attempt,
        String attributesJson,
        String eventsJson) {
}

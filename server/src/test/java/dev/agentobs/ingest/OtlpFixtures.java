package dev.agentobs.ingest;

import com.google.protobuf.ByteString;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest;
import io.opentelemetry.proto.common.v1.AnyValue;
import io.opentelemetry.proto.common.v1.KeyValue;
import io.opentelemetry.proto.resource.v1.Resource;
import io.opentelemetry.proto.trace.v1.ResourceSpans;
import io.opentelemetry.proto.trace.v1.ScopeSpans;
import io.opentelemetry.proto.trace.v1.Span;
import io.opentelemetry.proto.trace.v1.Status;

import java.util.HexFormat;

/** 真实 OTLP protobuf 样例：一个 job 根 span + 一个 stage span + 两次模型调用尝试。 */
public final class OtlpFixtures {

    public static final String TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";
    public static final String ROOT_SPAN = "00f067aa0ba902b7";
    public static final String STAGE_SPAN = "00f067aa0ba902b8";
    public static final String CHAT_SPAN_1 = "00f067aa0ba902b9";
    public static final String CHAT_SPAN_2 = "00f067aa0ba902ba";

    private OtlpFixtures() {}

    static KeyValue kv(String k, String v) {
        return KeyValue.newBuilder().setKey(k).setValue(AnyValue.newBuilder().setStringValue(v)).build();
    }

    static KeyValue kv(String k, long v) {
        return KeyValue.newBuilder().setKey(k).setValue(AnyValue.newBuilder().setIntValue(v)).build();
    }

    static KeyValue kv(String k, double v) {
        return KeyValue.newBuilder().setKey(k).setValue(AnyValue.newBuilder().setDoubleValue(v)).build();
    }

    static ByteString hex(String s) {
        return ByteString.copyFrom(HexFormat.of().parseHex(s));
    }

    public static ExportTraceServiceRequest sampleTrace(String projectId, String jobId, long baseNanos) {
        Span root = Span.newBuilder()
                .setTraceId(hex(TRACE_ID)).setSpanId(hex(ROOT_SPAN))
                .setName("job " + jobId).setKind(Span.SpanKind.SPAN_KIND_INTERNAL)
                .setStartTimeUnixNano(baseNanos).setEndTimeUnixNano(baseNanos + 5_000_000_000L)
                .setStatus(Status.newBuilder().setCode(Status.StatusCode.STATUS_CODE_OK))
                .addAttributes(kv("agentobs.job.id", jobId))
                .addAttributes(kv("agentobs.metric.pages", 4))
                .build();
        Span stage = Span.newBuilder()
                .setTraceId(hex(TRACE_ID)).setSpanId(hex(STAGE_SPAN)).setParentSpanId(hex(ROOT_SPAN))
                .setName("stage extract").setKind(Span.SpanKind.SPAN_KIND_INTERNAL)
                .setStartTimeUnixNano(baseNanos + 100_000_000L).setEndTimeUnixNano(baseNanos + 4_000_000_000L)
                .setStatus(Status.newBuilder().setCode(Status.StatusCode.STATUS_CODE_OK))
                .addAttributes(kv("agentobs.job.id", jobId))
                .addAttributes(kv("agentobs.stage", "extract"))
                .addAttributes(kv("agentobs.metric.answer_pages_detected", 3))
                .build();
        Span chat1 = Span.newBuilder()
                .setTraceId(hex(TRACE_ID)).setSpanId(hex(CHAT_SPAN_1)).setParentSpanId(hex(STAGE_SPAN))
                .setName("chat mock/fast").setKind(Span.SpanKind.SPAN_KIND_CLIENT)
                .setStartTimeUnixNano(baseNanos + 200_000_000L).setEndTimeUnixNano(baseNanos + 1_200_000_000L)
                .setStatus(Status.newBuilder().setCode(Status.StatusCode.STATUS_CODE_ERROR).setMessage("http 429"))
                .addAttributes(kv("agentobs.job.id", jobId))
                .addAttributes(kv("agentobs.stage", "extract"))
                .addAttributes(kv("gen_ai.request.model", "mock/fast"))
                .addAttributes(kv("agentobs.attempt", 1))
                .addAttributes(kv("agentobs.retry_reason", "rate_limited"))
                .build();
        Span chat2 = Span.newBuilder()
                .setTraceId(hex(TRACE_ID)).setSpanId(hex(CHAT_SPAN_2)).setParentSpanId(hex(STAGE_SPAN))
                .setName("chat mock/fast").setKind(Span.SpanKind.SPAN_KIND_CLIENT)
                .setStartTimeUnixNano(baseNanos + 3_000_000_000L).setEndTimeUnixNano(baseNanos + 3_900_000_000L)
                .setStatus(Status.newBuilder().setCode(Status.StatusCode.STATUS_CODE_OK))
                .addAttributes(kv("agentobs.job.id", jobId))
                .addAttributes(kv("agentobs.stage", "extract"))
                .addAttributes(kv("gen_ai.request.model", "mock/fast"))
                .addAttributes(kv("gen_ai.response.model", "mock/fast-2026"))
                .addAttributes(kv("gen_ai.usage.input_tokens", 120))
                .addAttributes(kv("gen_ai.usage.output_tokens", 40))
                .addAttributes(kv("agentobs.cost_usd", 0.000028))
                .addAttributes(kv("agentobs.attempt", 2))
                // 超长属性
                .addAttributes(kv("agentobs.debug.blob", "x".repeat(50_000)))
                .build();
        return ExportTraceServiceRequest.newBuilder()
                .addResourceSpans(ResourceSpans.newBuilder()
                        .setResource(Resource.newBuilder()
                                .addAttributes(kv("service.name", "demo-pipeline"))
                                .addAttributes(kv("agentobs.project_id", projectId)))
                        .addScopeSpans(ScopeSpans.newBuilder()
                                .addSpans(root).addSpans(stage).addSpans(chat1).addSpans(chat2)))
                .build();
    }
}

package dev.agentobs.ingest;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.google.protobuf.ByteString;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest;
import io.opentelemetry.proto.common.v1.AnyValue;
import io.opentelemetry.proto.common.v1.KeyValue;
import io.opentelemetry.proto.resource.v1.Resource;
import io.opentelemetry.proto.trace.v1.ResourceSpans;
import io.opentelemetry.proto.trace.v1.ScopeSpans;
import io.opentelemetry.proto.trace.v1.Span;
import io.opentelemetry.proto.trace.v1.Status;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

/** OTLP protobuf -> SpanRecord：ID 转小写 hex、纳秒转 timestamptz、AnyValue 转 JSON、提升列（SPEC 5.2 / 7.2）。 */
@Component
public class OtlpSpanMapper {

    static final String ATTR_PROJECT_ID = "agentobs.project_id";
    static final String ATTR_SERVICE_NAME = "service.name";
    static final String ATTR_JOB_ID = "agentobs.job.id";
    static final String ATTR_STAGE = "agentobs.stage";
    static final String ATTR_REQUEST_MODEL = "gen_ai.request.model";
    static final String ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens";
    static final String ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens";
    static final String ATTR_COST_USD = "agentobs.cost_usd";
    static final String ATTR_ATTEMPT = "agentobs.attempt";

    /** 单个属性值超过这个长度截断，防止超长属性撑爆 jsonb。 */
    static final int MAX_ATTR_CHARS = 16_000;

    private static final HexFormat HEX = HexFormat.of().withLowerCase();
    private final ObjectMapper om;

    public OtlpSpanMapper(ObjectMapper om) {
        this.om = om;
    }

    public List<SpanRecord> map(ExportTraceServiceRequest req) {
        List<SpanRecord> out = new ArrayList<>();
        for (ResourceSpans rs : req.getResourceSpansList()) {
            Map<String, Object> res = toMap(rs.getResource().getAttributesList());
            String serviceName = str(res.get(ATTR_SERVICE_NAME), "unknown");
            String projectId = str(res.get("agentobs.project_id"), "default");
            for (ScopeSpans ss : rs.getScopeSpansList()) {
                for (Span s : ss.getSpansList()) {
                    out.add(mapSpan(s, serviceName, projectId));
                }
            }
        }
        return out;
    }

    SpanRecord mapSpan(Span s, String serviceName, String projectId) {
        Map<String, Object> attrs = toMap(s.getAttributesList());
        String traceId = hex(s.getTraceId());
        String spanId = hex(s.getSpanId());
        String parent = s.getParentSpanId().isEmpty() ? null : hex(s.getParentSpanId());
        OffsetDateTime start = ts(s.getStartTimeUnixNano());
        OffsetDateTime end = s.getEndTimeUnixNano() == 0 ? start : ts(s.getEndTimeUnixNano());
        Status st = s.getStatus();
        String statusCode = switch (st.getCode()) {
            case STATUS_CODE_OK -> "OK";
            case STATUS_CODE_ERROR -> "ERROR";
            default -> "UNSET";
        };
        return new SpanRecord(
                traceId, spanId, parent,
                str(attrs.get(ATTR_PROJECT_ID), projectId),
                serviceName,
                s.getName(),
                s.getKind().name().replace("SPAN_KIND_", ""),
                start, end,
                statusCode,
                st.getMessage().isEmpty() ? null : st.getMessage(),
                strOrNull(attrs.get(ATTR_JOB_ID)),
                strOrNull(attrs.get(ATTR_STAGE)),
                strOrNull(attrs.get(ATTR_REQUEST_MODEL)),
                intOrNull(attrs.get(ATTR_INPUT_TOKENS)),
                intOrNull(attrs.get(ATTR_OUTPUT_TOKENS)),
                decimalOrNull(attrs.get(ATTR_COST_USD)),
                intOrNull(attrs.get(ATTR_ATTEMPT)),
                json(attrs),
                eventsJson(s));
    }

    private String eventsJson(Span s) {
        ArrayNode arr = om.createArrayNode();
        for (Span.Event e : s.getEventsList()) {
            ObjectNode n = arr.addObject();
            n.put("name", e.getName());
            n.put("time", ts(e.getTimeUnixNano()).toString());
            n.set("attributes", om.valueToTree(toMap(e.getAttributesList())));
        }
        return arr.toString();
    }

    private String json(Map<String, Object> m) {
        try {
            return om.writeValueAsString(m);
        } catch (Exception e) {
            return "{}";
        }
    }

    static Map<String, Object> toMap(List<KeyValue> kvs) {
        Map<String, Object> m = new java.util.LinkedHashMap<>();
        for (KeyValue kv : kvs) {
            m.put(kv.getKey(), anyValue(kv.getValue()));
        }
        return m;
    }

    static Object anyValue(AnyValue v) {
        return switch (v.getValueCase()) {
            case STRING_VALUE -> truncate(v.getStringValue());
            case BOOL_VALUE -> v.getBoolValue();
            case INT_VALUE -> v.getIntValue();
            case DOUBLE_VALUE -> v.getDoubleValue();
            case BYTES_VALUE -> HEX.formatHex(v.getBytesValue().toByteArray());
            case ARRAY_VALUE -> v.getArrayValue().getValuesList().stream().map(OtlpSpanMapper::anyValue).toList();
            case KVLIST_VALUE -> toMap(v.getKvlistValue().getValuesList());
            case VALUE_NOT_SET -> null;
        };
    }

    static String truncate(String s) {
        return s.length() > MAX_ATTR_CHARS ? s.substring(0, MAX_ATTR_CHARS) + "…[truncated]" : s;
    }

    static String hex(ByteString b) {
        return HEX.formatHex(b.toByteArray());
    }

    static OffsetDateTime ts(long unixNano) {
        return Instant.ofEpochSecond(Math.floorDiv(unixNano, 1_000_000_000L), Math.floorMod(unixNano, 1_000_000_000L))
                .atOffset(ZoneOffset.UTC);
    }

    private static String str(Object o, String dflt) {
        return o == null ? dflt : String.valueOf(o);
    }

    private static String strOrNull(Object o) {
        return o == null ? null : String.valueOf(o);
    }

    private static Integer intOrNull(Object o) {
        if (o instanceof Number n) return n.intValue();
        if (o instanceof String s) {
            try { return Integer.parseInt(s.trim()); } catch (NumberFormatException e) { return null; }
        }
        return null;
    }

    private static BigDecimal decimalOrNull(Object o) {
        if (o instanceof Number n) return BigDecimal.valueOf(n.doubleValue());
        if (o instanceof String s) {
            try { return new BigDecimal(s.trim()); } catch (NumberFormatException e) { return null; }
        }
        return null;
    }
}

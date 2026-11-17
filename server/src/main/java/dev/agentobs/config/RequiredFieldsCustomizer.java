package dev.agentobs.config;

import com.fasterxml.jackson.annotation.JsonProperty;
import dev.agentobs.query.dto.MetricsSummary;
import dev.agentobs.query.dto.PageResponse;
import dev.agentobs.query.dto.ReviewDto;
import dev.agentobs.query.dto.SpanDto;
import dev.agentobs.query.dto.TraceDetail;
import dev.agentobs.query.dto.TraceListItem;
import dev.agentobs.query.dto.TraceSummaryDto;
import dev.agentobs.query.dto.TriageDto;
import dev.agentobs.review.EvalController;
import dev.agentobs.review.ReviewService;
import io.swagger.v3.oas.models.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springdoc.core.customizers.OpenApiCustomizer;
import org.springframework.stereotype.Component;

import java.lang.reflect.RecordComponent;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * springdoc 默认把 record 的每个字段都标成可选，生成的 TS 类型全是 `?:`。
 * 这里按 Java 类型补上 required：基本类型、或带 @NotNull / @NotBlank 的组件。
 * 可能为 null 的字段在 JSON 里会被省略（jackson non_null），保持可选是准确的。
 */
@Component
public class RequiredFieldsCustomizer implements OpenApiCustomizer {

    static final List<Class<?>> DTOS = List.of(
            TraceListItem.class, TraceSummaryDto.class, SpanDto.class, TriageDto.class, ReviewDto.class,
            TraceDetail.class, MetricsSummary.class,
            ReviewService.ReviewQueueItem.class, ReviewService.ReviewResponse.class, ReviewService.ReviewRequest.class,
            EvalController.EvalRunDto.class);

    @Override
    @SuppressWarnings("rawtypes")
    public void customise(io.swagger.v3.oas.models.OpenAPI openApi) {
        if (openApi.getComponents() == null || openApi.getComponents().getSchemas() == null) return;
        Map<String, Schema> schemas = openApi.getComponents().getSchemas();
        schemas.values().forEach(RequiredFieldsCustomizer::looseMaps);
        for (Map.Entry<String, Schema> e : schemas.entrySet()) {
            Class<?> type = resolve(e.getKey());
            if (type == null || !type.isRecord()) continue;
            Schema<?> schema = e.getValue();
            if (schema.getProperties() == null) continue;
            TreeSet<String> required = new TreeSet<>(schema.getRequired() == null ? List.of() : schema.getRequired());
            for (RecordComponent rc : type.getRecordComponents()) {
                if (isRequired(rc)) {
                    String name = jsonName(rc);
                    if (schema.getProperties().containsKey(name)) required.add(name);
                }
            }
            schema.setRequired(required.isEmpty() ? null : new ArrayList<>(required));
        }
    }

    /** Map&lt;String, Object&gt; 的值默认被描述成空 object（TS 里是 Record&lt;string, never&gt;），改成任意值。 */
    @SuppressWarnings({"rawtypes", "unchecked"})
    static void looseMaps(Schema s) {
        if (s == null) return;
        if (s.getAdditionalProperties() instanceof Schema a) {
            if (isUnconstrainedObject(a)) s.setAdditionalProperties(Boolean.TRUE);
            else looseMaps(a);
        }
        looseMaps(s.getItems());
        if (s.getProperties() != null) ((Map<String, Schema>) s.getProperties()).values().forEach(RequiredFieldsCustomizer::looseMaps);
    }

    @SuppressWarnings("rawtypes")
    private static boolean isUnconstrainedObject(Schema a) {
        return a.get$ref() == null
                && (a.getProperties() == null || a.getProperties().isEmpty())
                && a.getAdditionalProperties() == null
                && a.getItems() == null
                && (a.getType() == null || "object".equals(a.getType()))
                && (a.getTypes() == null || a.getTypes().isEmpty() || a.getTypes().equals(java.util.Set.of("object")));
    }

    static Class<?> resolve(String schemaName) {
        if (schemaName.startsWith("PageResponse")) return PageResponse.class;
        for (Class<?> c : DTOS) {
            if (c.getSimpleName().equals(schemaName)) return c;
        }
        return null;
    }

    static boolean isRequired(RecordComponent rc) {
        if (rc.getType().isPrimitive()) return true;
        if (rc.getDeclaringRecord() == PageResponse.class) return true;
        return rc.isAnnotationPresent(NotNull.class) || rc.isAnnotationPresent(NotBlank.class)
                || fieldAnnotated(rc);
    }

    private static boolean fieldAnnotated(RecordComponent rc) {
        try {
            var f = rc.getDeclaringRecord().getDeclaredField(rc.getName());
            return f.isAnnotationPresent(NotNull.class) || f.isAnnotationPresent(NotBlank.class);
        } catch (NoSuchFieldException ex) {
            return false;
        }
    }

    static String jsonName(RecordComponent rc) {
        JsonProperty jp = rc.getAnnotation(JsonProperty.class);
        if (jp == null) {
            try {
                jp = rc.getDeclaringRecord().getDeclaredField(rc.getName()).getAnnotation(JsonProperty.class);
            } catch (NoSuchFieldException ignored) {
                // fall through
            }
        }
        return jp != null && !jp.value().isEmpty() ? jp.value() : rc.getName();
    }
}

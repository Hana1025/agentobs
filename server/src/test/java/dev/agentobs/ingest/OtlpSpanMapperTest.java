package dev.agentobs.ingest;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class OtlpSpanMapperTest {

    private final OtlpSpanMapper mapper = new OtlpSpanMapper(new ObjectMapper());

    @Test
    void mapsIdsTimesAndColumns() {
        List<SpanRecord> spans = mapper.map(OtlpFixtures.sampleTrace("p", "j", 1_700_000_000_123_456_789L));
        assertThat(spans).hasSize(4);
        SpanRecord root = spans.get(0);
        assertThat(root.traceId()).isEqualTo(OtlpFixtures.TRACE_ID);
        assertThat(root.spanId()).isEqualTo(OtlpFixtures.ROOT_SPAN);
        assertThat(root.parentSpanId()).isNull();
        assertThat(root.startTime().toInstant().getNano()).isEqualTo(123_456_789);
        assertThat(root.statusCode()).isEqualTo("OK");
        assertThat(root.jobId()).isEqualTo("j");
        assertThat(root.projectId()).isEqualTo("p");
        assertThat(root.attributesJson()).contains("\"agentobs.metric.pages\":4");
        SpanRecord chat1 = spans.get(2);
        assertThat(chat1.statusCode()).isEqualTo("ERROR");
        assertThat(chat1.statusMessage()).isEqualTo("http 429");
        assertThat(chat1.attempt()).isEqualTo(1);
    }

    @Test
    void truncatesOversizedAttribute() {
        assertThat(OtlpSpanMapper.truncate("x".repeat(OtlpSpanMapper.MAX_ATTR_CHARS + 10)))
                .hasSizeLessThan(OtlpSpanMapper.MAX_ATTR_CHARS + 20).endsWith("[truncated]");
    }
}

package dev.agentobs.ingest;

import dev.agentobs.PostgresTestBase;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class IngestIT extends PostgresTestBase {

    @Autowired TestRestTemplate rest;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void clean() {
        jdbc.update("DELETE FROM obs.spans");
    }

    private ResponseEntity<byte[]> post(byte[] body, String token) {
        HttpHeaders h = new HttpHeaders();
        h.setContentType(MediaType.parseMediaType("application/x-protobuf"));
        if (token != null) h.set("X-Agentobs-Token", token);
        return rest.postForEntity("/v1/traces", new HttpEntity<>(body, h), byte[].class);
    }

    @Test
    void ingestsSpansAndPromotesColumns() {
        ExportTraceServiceRequest req = OtlpFixtures.sampleTrace("demo", "job-0001", 1_700_000_000_000_000_000L);
        ResponseEntity<byte[]> res = post(req.toByteArray(), "ingest-test-token");
        assertThat(res.getStatusCode().value()).isEqualTo(200);

        assertThat(jdbc.queryForObject("SELECT count(*) FROM obs.spans", Integer.class)).isEqualTo(4);
        Map<String, Object> chat2 = jdbc.queryForMap(
                "SELECT * FROM obs.spans WHERE span_id = ?", OtlpFixtures.CHAT_SPAN_2);
        assertThat(chat2.get("project_id")).isEqualTo("demo");
        assertThat(chat2.get("service_name")).isEqualTo("demo-pipeline");
        assertThat(chat2.get("job_id")).isEqualTo("job-0001");
        assertThat(chat2.get("stage")).isEqualTo("extract");
        assertThat(chat2.get("model")).isEqualTo("mock/fast");
        assertThat(chat2.get("input_tokens")).isEqualTo(120);
        assertThat(chat2.get("output_tokens")).isEqualTo(40);
        assertThat(chat2.get("attempt")).isEqualTo(2);
        assertThat(((BigDecimal) chat2.get("cost_usd")).doubleValue()).isCloseTo(0.000028, org.assertj.core.data.Offset.offset(1e-9));
        assertThat(((Number) chat2.get("duration_ms")).doubleValue()).isCloseTo(900.0, org.assertj.core.data.Offset.offset(0.01));
        assertThat(chat2.get("parent_span_id")).isEqualTo(OtlpFixtures.STAGE_SPAN);
        // 超长属性被截断但仍入库
        String attrs = chat2.get("attributes").toString();
        assertThat(attrs).contains("agentobs.debug.blob").contains("[truncated]");

        Map<String, Object> chat1 = jdbc.queryForMap(
                "SELECT * FROM obs.spans WHERE span_id = ?", OtlpFixtures.CHAT_SPAN_1);
        assertThat(chat1.get("status_code")).isEqualTo("ERROR");
        assertThat(chat1.get("input_tokens")).isNull(); // 缺少可选属性

        Map<String, Object> summary = jdbc.queryForMap(
                "SELECT * FROM obs.trace_summary WHERE trace_id = ?", OtlpFixtures.TRACE_ID);
        assertThat(summary.get("has_root")).isEqualTo(true);
        assertThat(summary.get("root_status")).isEqualTo("OK");
        assertThat(summary.get("error_count")).isEqualTo(1L);
    }

    @Test
    void duplicateSubmissionDoesNotDuplicateRows() {
        ExportTraceServiceRequest req = OtlpFixtures.sampleTrace("demo", "job-0002", 1_700_000_000_000_000_000L);
        post(req.toByteArray(), "ingest-test-token");
        post(req.toByteArray(), "ingest-test-token");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM obs.spans", Integer.class)).isEqualTo(4);
    }

    @Test
    void rejectsBadPayloadAndMissingToken() {
        assertThat(post(new byte[] {1, 2, 3, 4, 5, 6, 7}, "ingest-test-token").getStatusCode().value()).isEqualTo(400);
        ExportTraceServiceRequest req = OtlpFixtures.sampleTrace("demo", "job-0003", 1_700_000_000_000_000_000L);
        assertThat(post(req.toByteArray(), null).getStatusCode().value()).isEqualTo(401);
        assertThat(post(req.toByteArray(), "wrong").getStatusCode().value()).isEqualTo(401);
    }
}

package dev.agentobs.query;

import dev.agentobs.PostgresTestBase;
import dev.agentobs.ingest.OtlpFixtures;
import dev.agentobs.ingest.OtlpSpanMapper;
import dev.agentobs.ingest.SpanRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class TraceQueryIT extends PostgresTestBase {

    @Autowired TestRestTemplate rest;
    @Autowired JdbcTemplate jdbc;
    @Autowired OtlpSpanMapper mapper;
    @Autowired SpanRepository repo;

    static final long BASE = 1_700_000_000_000_000_000L; // 2023-11-14T22:13:20Z

    @BeforeEach
    void seed() {
        jdbc.update("DELETE FROM obs.reviews");
        jdbc.update("DELETE FROM obs.trace_triage");
        jdbc.update("DELETE FROM obs.spans");
        repo.insertAll(mapper.map(OtlpFixtures.sampleTrace("demo", "job-1", BASE)));
    }

    @Test
    void listRequiresTimeRangeAndRejectsWideRange() {
        assertThat(rest.getForEntity("/api/traces?project_id=demo", String.class).getStatusCode().value()).isEqualTo(400);
        ResponseEntity<String> wide = rest.getForEntity(
                "/api/traces?project_id=demo&from=2023-11-01T00:00:00Z&to=2023-12-15T00:00:00Z", String.class);
        assertThat(wide.getStatusCode().value()).isEqualTo(400);
        assertThat(wide.getBody()).contains("31 days");
    }

    @Test
    @SuppressWarnings("unchecked")
    void listReturnsRootsWithAggregates() {
        ResponseEntity<Map> res = rest.getForEntity(
                "/api/traces?project_id=demo&from=2023-11-14T00:00:00Z&to=2023-11-15T00:00:00Z", Map.class);
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        var items = (java.util.List<Map<String, Object>>) res.getBody().get("items");
        assertThat(items).hasSize(1);
        Map<String, Object> it = items.get(0);
        assertThat(it.get("traceId")).isEqualTo(OtlpFixtures.TRACE_ID);
        assertThat(it.get("spanCount")).isEqualTo(4);
        assertThat(it.get("errorCount")).isEqualTo(1);
        assertThat(it.get("reviewStatus")).isEqualTo("none");
    }

    @Test
    @SuppressWarnings("unchecked")
    void detailReturnsSpansInTreeOrderWithDepth() {
        ResponseEntity<Map> res = rest.getForEntity("/api/traces/" + OtlpFixtures.TRACE_ID, Map.class);
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        var spans = (java.util.List<Map<String, Object>>) res.getBody().get("spans");
        assertThat(spans).extracting(s -> s.get("spanId")).containsExactly(
                OtlpFixtures.ROOT_SPAN, OtlpFixtures.STAGE_SPAN, OtlpFixtures.CHAT_SPAN_1, OtlpFixtures.CHAT_SPAN_2);
        assertThat(spans).extracting(s -> s.get("depth")).containsExactly(0, 1, 2, 2);
        assertThat(rest.getForEntity("/api/traces/" + "0".repeat(32), String.class).getStatusCode().value()).isEqualTo(404);
    }

    @Test
    void metricsSummaryUsesPercentile() {
        ResponseEntity<Map> res = rest.getForEntity(
                "/api/metrics/summary?project_id=demo&from=2023-11-14T00:00:00Z&to=2023-11-15T00:00:00Z", Map.class);
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        assertThat(((Number) res.getBody().get("jobs")).longValue()).isEqualTo(1);
        assertThat(((Number) res.getBody().get("p95DurationMs")).doubleValue()).isEqualTo(5000.0);
        assertThat(((Number) res.getBody().get("successRate")).doubleValue()).isEqualTo(1.0);
    }

    @Test
    void meReadsForwardedUser() {
        HttpHeaders h = new HttpHeaders();
        h.set("X-Forwarded-User", "reviewer-a");
        ResponseEntity<Map> res = rest.exchange("/api/me", org.springframework.http.HttpMethod.GET, new HttpEntity<>(h), Map.class);
        assertThat(res.getBody().get("user")).isEqualTo("reviewer-a");
        assertThat(rest.getForEntity("/api/me", Map.class).getBody().get("user")).isEqualTo("anonymous");
    }
}

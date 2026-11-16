package dev.agentobs.triage;

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
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class InternalApiIT extends PostgresTestBase {

    @Autowired TestRestTemplate rest;
    @Autowired JdbcTemplate jdbc;
    @Autowired OtlpSpanMapper mapper;
    @Autowired SpanRepository repo;

    HttpHeaders auth() {
        HttpHeaders h = new HttpHeaders();
        h.set("X-Agentobs-Token", "internal-test-token");
        h.setContentType(MediaType.APPLICATION_JSON);
        return h;
    }

    @BeforeEach
    void seed() {
        jdbc.update("DELETE FROM obs.reviews");
        jdbc.update("DELETE FROM obs.trace_triage");
        jdbc.update("DELETE FROM obs.spans");
        repo.insertAll(mapper.map(OtlpFixtures.sampleTrace("demo", "job-1", 1_700_000_000_000_000_000L)));
    }

    @Test
    void traceEndpointRequiresInternalToken() {
        assertThat(rest.getForEntity("/api/internal/traces/" + OtlpFixtures.TRACE_ID, String.class)
                .getStatusCode().value()).isEqualTo(401);
        ResponseEntity<Map> ok = rest.exchange("/api/internal/traces/" + OtlpFixtures.TRACE_ID,
                org.springframework.http.HttpMethod.GET, new HttpEntity<>(auth()), Map.class);
        assertThat(ok.getStatusCode().value()).isEqualTo(200);
        assertThat(ok.getBody()).containsKeys("summary", "spans");
        ResponseEntity<String> missing = rest.exchange("/api/internal/traces/" + "f".repeat(32),
                org.springframework.http.HttpMethod.GET, new HttpEntity<>(auth()), String.class);
        assertThat(missing.getStatusCode().value()).isEqualTo(404);
    }

    @Test
    void triagePostIsIdempotentPerVersion() {
        String body = """
                {"trace_id": "%s", "project_id": "demo", "job_id": "job-1", "triage_version": "v-test",
                 "rules_fired": ["retries_high"], "state": {"job": {"status": "OK"}},
                 "s1_backend": "rules", "s1_answers": {"health": {"answer": "suspicious", "probs": {"suspicious": 0.7}, "confidence": 0.7},
                 "severity": {"answer": "2", "probs": {"2": 1.0}, "confidence": 1.0}},
                 "s1_label": "suspicious", "s1_confidence": 0.7, "route": "alert", "review_status": "none"}
                """.formatted(OtlpFixtures.TRACE_ID);
        ResponseEntity<Map> first = rest.postForEntity("/api/internal/triage", new HttpEntity<>(body, auth()), Map.class);
        assertThat(first.getStatusCode().value()).isEqualTo(201);
        ResponseEntity<String> dup = rest.postForEntity("/api/internal/triage", new HttpEntity<>(body, auth()), String.class);
        assertThat(dup.getStatusCode().value()).isEqualTo(409);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM obs.trace_triage", Integer.class)).isEqualTo(1);

        // 列表里能看到 severity 和 s1 标签
        ResponseEntity<Map> list = rest.getForEntity(
                "/api/traces?project_id=demo&from=2023-11-14T00:00:00Z&to=2023-11-15T00:00:00Z", Map.class);
        @SuppressWarnings("unchecked")
        Map<String, Object> item = ((java.util.List<Map<String, Object>>) list.getBody().get("items")).get(0);
        assertThat(item.get("severity")).isEqualTo(2);
        assertThat(item.get("s1Label")).isEqualTo("suspicious");
    }

    @Test
    void baselinesReturnPerStageMedians() {
        ResponseEntity<Map> res = rest.exchange("/api/internal/baselines?project_id=demo&window=200",
                org.springframework.http.HttpMethod.GET, new HttpEntity<>(auth()), Map.class);
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        @SuppressWarnings("unchecked")
        Map<String, Map<String, Object>> stages = (Map<String, Map<String, Object>>) res.getBody().get("stages");
        assertThat(stages).containsKey("extract");
        assertThat(((Number) stages.get("extract").get("medianDurationMs")).doubleValue()).isEqualTo(3900.0);
        assertThat(((Number) stages.get("extract").get("samples")).intValue()).isEqualTo(1);
        @SuppressWarnings("unchecked")
        Map<String, Object> metrics = (Map<String, Object>) stages.get("extract").get("medianMetrics");
        assertThat(((Number) metrics.get("answer_pages_detected")).doubleValue()).isEqualTo(3.0);
    }
}

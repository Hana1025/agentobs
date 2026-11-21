package dev.agentobs.review;

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

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class ReviewAndEvalIT extends PostgresTestBase {

    @Autowired TestRestTemplate rest;
    @Autowired JdbcTemplate jdbc;
    @Autowired OtlpSpanMapper mapper;
    @Autowired SpanRepository repo;

    long triageId;

    @BeforeEach
    void seed() {
        jdbc.update("DELETE FROM obs.reviews");
        jdbc.update("DELETE FROM obs.eval_cases");
        jdbc.update("DELETE FROM obs.eval_runs");
        jdbc.update("DELETE FROM obs.trace_triage");
        jdbc.update("DELETE FROM obs.spans");
        repo.insertAll(mapper.map(OtlpFixtures.sampleTrace("demo", "job-1", 1_700_000_000_000_000_000L)));
        triageId = jdbc.queryForObject("""
                INSERT INTO obs.trace_triage (trace_id, project_id, job_id, triage_version, state, s1_answers, s1_label, route, review_status)
                VALUES (?, 'demo', 'job-1', 'v', '{"job": {"status": "OK"}}'::jsonb,
                        '{"severity": {"answer": "2"}, "suspect_stage": {"answer": "extract"}}'::jsonb, 'suspicious', 'escalate', 'pending')
                RETURNING id
                """, Long.class, OtlpFixtures.TRACE_ID);
    }

    private HttpHeaders json(String user) {
        HttpHeaders h = new HttpHeaders();
        h.setContentType(MediaType.APPLICATION_JSON);
        if (user != null) h.set("X-Forwarded-User", user);
        return h;
    }

    @Test
    @SuppressWarnings("unchecked")
    void reviewRemovesFromQueueAndCreatesEvalCase() {
        ResponseEntity<Map> q = rest.getForEntity("/api/reviews/queue?project_id=demo", Map.class);
        assertThat(((List<?>) q.getBody().get("items"))).hasSize(1);

        String body = "{\"triage_id\": " + triageId + ", \"human_label\": \"failed\", \"human_root_cause\": \"provider_failure\", \"note\": \"ok\"}";
        ResponseEntity<Map> res = rest.postForEntity("/api/reviews", new HttpEntity<>(body, json("alice")), Map.class);
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        Map<String, Object> review = (Map<String, Object>) res.getBody().get("review");
        assertThat(review.get("reviewer")).isEqualTo("alice");
        assertThat(review.get("humanLabel")).isEqualTo("failed");

        assertThat(jdbc.queryForObject("SELECT review_status FROM obs.trace_triage WHERE id = ?", String.class, triageId)).isEqualTo("reviewed");
        Map<String, Object> ec = jdbc.queryForMap("SELECT * FROM obs.eval_cases WHERE source = 'human_review'");
        assertThat(ec.get("expected_label")).isEqualTo("failed");
        assertThat(ec.get("expected_stage")).isEqualTo("extract");
        assertThat(ec.get("expected_root_cause")).isEqualTo("provider_failure");
        assertThat(ec.get("trace_id")).isEqualTo(OtlpFixtures.TRACE_ID);

        q = rest.getForEntity("/api/reviews/queue?project_id=demo", Map.class);
        assertThat(((List<?>) q.getBody().get("items"))).isEmpty();

        // 再次复核 → 409
        assertThat(rest.postForEntity("/api/reviews", new HttpEntity<>(body, json("alice")), String.class).getStatusCode().value()).isEqualTo(409);
        // 不存在 → 404；缺字段 → 400
        assertThat(rest.postForEntity("/api/reviews", new HttpEntity<>("{\"triage_id\": 999999, \"human_label\": \"normal\"}", json(null)), String.class).getStatusCode().value()).isEqualTo(404);
        assertThat(rest.postForEntity("/api/reviews", new HttpEntity<>("{\"triage_id\": " + triageId + "}", json(null)), String.class).getStatusCode().value()).isEqualTo(400);
    }

    @Test
    void evalCasesSkipDuplicatesAndRunsAreListed() {
        HttpHeaders h = json(null);
        h.set("X-Agentobs-Token", "internal-test-token");
        String cases = """
                {"cases": [
                  {"trace_id": "%s", "source": "injected", "fault_type": "missing_page", "expected_label": "suspicious",
                   "expected_stage": "structure", "expected_root_cause": "missing_or_misclassified_input", "state_snapshot": {"job": {}}},
                  {"trace_id": "%s", "source": "injected", "fault_type": "missing_page", "expected_label": "suspicious", "state_snapshot": {}}
                ]}""".formatted(OtlpFixtures.TRACE_ID, OtlpFixtures.TRACE_ID);
        ResponseEntity<Map> res = rest.postForEntity("/api/internal/eval-cases", new HttpEntity<>(cases, h), Map.class);
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        assertThat(res.getBody().get("inserted")).isEqualTo(1);
        assertThat(res.getBody().get("skipped")).isEqualTo(1);

        String run = "{\"backend\": \"rules\", \"triage_version\": \"rules-abc\", \"dataset_size\": 10, \"metrics\": {\"fault_recall\": 0.95}}";
        assertThat(rest.postForEntity("/api/internal/eval-runs", new HttpEntity<>(run, h), Map.class).getStatusCode().value()).isEqualTo(201);
        ResponseEntity<List> list = rest.getForEntity("/api/eval/runs", List.class);
        assertThat(list.getBody()).hasSize(1);
        @SuppressWarnings("unchecked")
        Map<String, Object> first = (Map<String, Object>) list.getBody().get(0);
        assertThat(first.get("backend")).isEqualTo("rules");
        assertThat(rest.getForEntity("/api/eval/runs/" + first.get("id"), Map.class).getStatusCode().value()).isEqualTo(200);
        assertThat(rest.getForEntity("/api/eval/runs/999999", String.class).getStatusCode().value()).isEqualTo(404);
    }

    @Test
    @SuppressWarnings("unchecked")
    void listEvalCasesFiltersBySourceAndValidatesLimit() {
        // 先提交一条人工复核产生 human_review 用例，再写入一条 injected 用例
        String reviewBody = "{\"triage_id\": " + triageId + ", \"human_label\": \"failed\", \"human_root_cause\": \"provider_failure\"}";
        assertThat(rest.postForEntity("/api/reviews", new HttpEntity<>(reviewBody, json("alice")), Map.class).getStatusCode().value()).isEqualTo(200);

        HttpHeaders h = json(null);
        h.set("X-Agentobs-Token", "internal-test-token");
        String injected = """
                {"cases": [
                  {"trace_id": "%s", "source": "injected", "fault_type": "missing_page", "expected_label": "suspicious",
                   "expected_stage": "structure", "expected_root_cause": "missing_or_misclassified_input", "state_snapshot": {"job": {}}}
                ]}""".formatted(OtlpFixtures.TRACE_ID);
        assertThat(rest.postForEntity("/api/internal/eval-cases", new HttpEntity<>(injected, h), Map.class).getStatusCode().value()).isEqualTo(200);

        ResponseEntity<List> all = rest.exchange("/api/internal/eval-cases", org.springframework.http.HttpMethod.GET, new HttpEntity<>(h), List.class);
        assertThat(all.getStatusCode().value()).isEqualTo(200);
        assertThat(all.getBody()).hasSize(2);

        ResponseEntity<List> humanOnly = rest.exchange("/api/internal/eval-cases?source=human_review", org.springframework.http.HttpMethod.GET, new HttpEntity<>(h), List.class);
        assertThat(humanOnly.getStatusCode().value()).isEqualTo(200);
        assertThat(humanOnly.getBody()).hasSize(1);
        Map<String, Object> item = (Map<String, Object>) humanOnly.getBody().get(0);
        assertThat(item.get("source")).isEqualTo("human_review");
        assertThat(item.get("expected_label")).isEqualTo("failed");
        assertThat(item.get("expected_root_cause")).isEqualTo("provider_failure");
        assertThat(item.get("trace_id")).isEqualTo(OtlpFixtures.TRACE_ID);

        assertThat(rest.exchange("/api/internal/eval-cases?limit=0", org.springframework.http.HttpMethod.GET, new HttpEntity<>(h), String.class).getStatusCode().value()).isEqualTo(400);
        assertThat(rest.exchange("/api/internal/eval-cases?limit=5000", org.springframework.http.HttpMethod.GET, new HttpEntity<>(h), String.class).getStatusCode().value()).isEqualTo(400);
    }
}

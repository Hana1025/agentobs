package dev.agentobs.review;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.agentobs.query.NotFoundException;
import io.swagger.v3.oas.annotations.Hidden;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/** 评测：内部写入（eval-cases / eval-runs）和公开读取（/api/eval/runs）。 */
@RestController
@Tag(name = "eval")
public class EvalController {

    public record EvalCase(
            @JsonProperty("trace_id") String traceId,
            @JsonProperty("source") @NotBlank String source,
            @JsonProperty("fault_type") String faultType,
            @JsonProperty("expected_label") @NotBlank String expectedLabel,
            @JsonProperty("expected_stage") String expectedStage,
            @JsonProperty("expected_root_cause") String expectedRootCause,
            @JsonProperty("state_snapshot") @NotNull Map<String, Object> stateSnapshot) {}

    public record EvalCasesRequest(@NotNull List<@Valid EvalCase> cases) {}

    public record EvalRunRequest(
            @JsonProperty("backend") @NotBlank String backend,
            @JsonProperty("triage_version") @NotBlank String triageVersion,
            @JsonProperty("dataset_size") int datasetSize,
            @JsonProperty("metrics") @NotNull Map<String, Object> metrics) {}

    public record EvalRunDto(long id, @NotNull String backend, @NotNull String triageVersion, int datasetSize,
                             @NotNull Map<String, Object> metrics, @NotNull OffsetDateTime createdAt) {}

    private final JdbcTemplate jdbc;
    private final ObjectMapper om;

    public EvalController(JdbcTemplate jdbc, ObjectMapper om) {
        this.jdbc = jdbc;
        this.om = om;
    }

    @PostMapping("/api/internal/eval-cases")
    @Hidden
    public Map<String, Object> evalCases(@RequestBody @Valid EvalCasesRequest req) {
        int inserted = 0;
        for (EvalCase c : req.cases()) {
            int n = jdbc.update("""
                    INSERT INTO obs.eval_cases (trace_id, source, fault_type, expected_label, expected_stage, expected_root_cause, state_snapshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?::jsonb)
                    ON CONFLICT (trace_id, source) WHERE trace_id IS NOT NULL DO NOTHING
                    """, c.traceId(), c.source(), c.faultType(), c.expectedLabel(), c.expectedStage(), c.expectedRootCause(), json(c.stateSnapshot()));
            inserted += n;
        }
        return Map.of("received", req.cases().size(), "inserted", inserted, "skipped", req.cases().size() - inserted);
    }

    @PostMapping("/api/internal/eval-runs")
    @Hidden
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, Object> evalRun(@RequestBody @Valid EvalRunRequest req) {
        long id = jdbc.queryForObject("""
                INSERT INTO obs.eval_runs (backend, triage_version, dataset_size, metrics)
                VALUES (?, ?, ?, ?::jsonb) RETURNING id
                """, Long.class, req.backend(), req.triageVersion(), req.datasetSize(), json(req.metrics()));
        return Map.of("id", id);
    }

    @GetMapping("/api/eval/runs")
    @Operation(operationId = "listEvalRuns", summary = "评测结果列表（最新在前）")
    public List<EvalRunDto> list() {
        return jdbc.query("SELECT *, metrics::text AS metrics_json FROM obs.eval_runs ORDER BY created_at DESC LIMIT 200", ROW);
    }

    @GetMapping("/api/eval/runs/{id}")
    @Operation(operationId = "getEvalRun", summary = "评测结果详情")
    public EvalRunDto get(@PathVariable long id) {
        List<EvalRunDto> rows = jdbc.query("SELECT *, metrics::text AS metrics_json FROM obs.eval_runs WHERE id = ?", ROW, id);
        if (rows.isEmpty()) throw new NotFoundException("eval run " + id + " not found");
        return rows.get(0);
    }

    private final RowMapper<EvalRunDto> ROW = (rs, i) -> new EvalRunDto(
            rs.getLong("id"), rs.getString("backend"), rs.getString("triage_version"), rs.getInt("dataset_size"),
            parse(rs.getString("metrics_json")), rs.getObject("created_at", OffsetDateTime.class));

    private String json(Object o) {
        try {
            return om.writeValueAsString(o);
        } catch (Exception e) {
            throw new IllegalArgumentException("invalid JSON", e);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> parse(String s) {
        try {
            return om.readValue(s, Map.class);
        } catch (Exception e) {
            return Map.of();
        }
    }
}

package dev.agentobs.review;

import com.fasterxml.jackson.annotation.JsonProperty;
import dev.agentobs.query.ConflictException;
import dev.agentobs.query.NotFoundException;
import dev.agentobs.query.TraceQueryService;
import dev.agentobs.query.dto.PageResponse;
import dev.agentobs.query.dto.ReviewDto;
import dev.agentobs.query.dto.TriageDto;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;

@Service
public class ReviewService {

    public record ReviewRequest(
            @JsonProperty("triage_id") @NotNull Long triageId,
            @JsonProperty("human_label") @NotBlank @Pattern(regexp = "normal|suspicious|failed") String humanLabel,
            @JsonProperty("human_root_cause") String humanRootCause,
            @JsonProperty("note") String note) {}

    public record ReviewResponse(ReviewDto review, long evalCaseId) {}

    public record ReviewQueueItem(TriageDto triage, String jobId, int severity, String suspectStage) {}

    private final JdbcTemplate jdbc;
    private final TraceQueryService query;

    public ReviewService(JdbcTemplate jdbc, TraceQueryService query) {
        this.jdbc = jdbc;
        this.query = query;
    }

    public PageResponse<ReviewQueueItem> queue(String projectId, int page, int size) {
        String base = """
                FROM obs.trace_triage t
                WHERE t.project_id = ? AND t.review_status = 'pending'
                """;
        long total = jdbc.queryForObject("SELECT count(*) " + base, Long.class, projectId);
        List<ReviewQueueItem> items = jdbc.query("""
                SELECT t.*, t.state::text AS state_json, t.s1_answers::text AS s1_json, t.s2_evidence::text AS s2_json,
                       COALESCE((t.s1_answers -> 'severity' ->> 'answer')::int, 0) AS severity,
                       t.s1_answers -> 'suspect_stage' ->> 'answer' AS suspect_stage
                """ + base + " ORDER BY severity DESC, t.created_at ASC LIMIT ? OFFSET ?",
                (rs, i) -> new ReviewQueueItem(query.TRIAGE_ROW.mapRow(rs, i), rs.getString("job_id"),
                        rs.getInt("severity"), rs.getString("suspect_stage")),
                projectId, size, (long) page * size);
        return new PageResponse<>(items, page, size, total);
    }

    /** 同一事务：写 reviews、review_status=reviewed、用 triage.state 生成 eval_cases(source=human_review)。 */
    @Transactional
    public ReviewResponse submit(ReviewRequest req, String reviewer) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT id, trace_id, review_status, state::text AS state_json, s1_answers -> 'suspect_stage' ->> 'answer' AS stage "
                        + "FROM obs.trace_triage WHERE id = ? FOR UPDATE", req.triageId());
        if (rows.isEmpty()) throw new NotFoundException("triage " + req.triageId() + " not found");
        Map<String, Object> t = rows.get(0);
        if ("reviewed".equals(t.get("review_status"))) {
            throw new ConflictException("triage " + req.triageId() + " already reviewed");
        }
        long reviewId = jdbc.queryForObject("""
                INSERT INTO obs.reviews (triage_id, reviewer, human_label, human_root_cause, note)
                VALUES (?, ?, ?, ?, ?) RETURNING id
                """, Long.class, req.triageId(), reviewer, req.humanLabel(), req.humanRootCause(), req.note());
        jdbc.update("UPDATE obs.trace_triage SET review_status = 'reviewed' WHERE id = ?", req.triageId());
        long evalCaseId = jdbc.queryForObject("""
                INSERT INTO obs.eval_cases (trace_id, source, fault_type, expected_label, expected_stage, expected_root_cause, state_snapshot)
                VALUES (?, 'human_review', NULL, ?, ?, ?, ?::jsonb)
                ON CONFLICT (trace_id, source) WHERE trace_id IS NOT NULL DO UPDATE
                  SET expected_label = EXCLUDED.expected_label, expected_stage = EXCLUDED.expected_stage,
                      expected_root_cause = EXCLUDED.expected_root_cause, state_snapshot = EXCLUDED.state_snapshot
                RETURNING id
                """, Long.class, t.get("trace_id"), req.humanLabel(),
                "normal".equals(req.humanLabel()) ? "none" : t.get("stage"), req.humanRootCause(), t.get("state_json"));
        ReviewDto review = query.reviewsFor(req.triageId()).stream().filter(r -> r.id() == reviewId).findFirst().orElseThrow();
        return new ReviewResponse(review, evalCaseId);
    }
}

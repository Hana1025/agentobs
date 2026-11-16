package dev.agentobs.triage;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.agentobs.query.ConflictException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.sql.Types;
import java.util.List;
import java.util.Map;

@Repository
public class TriageRepository {

    private final JdbcTemplate jdbc;
    private final ObjectMapper om;

    public TriageRepository(JdbcTemplate jdbc, ObjectMapper om) {
        this.jdbc = jdbc;
        this.om = om;
    }

    /** @return 新行 id；同 trace_id + triage_version 已存在抛 ConflictException */
    public long insert(TriageRequest r) {
        KeyHolder kh = new GeneratedKeyHolder();
        try {
            jdbc.update(con -> {
                PreparedStatement ps = con.prepareStatement("""
                        INSERT INTO obs.trace_triage (trace_id, project_id, job_id, triage_version, rules_fired, state,
                          s1_backend, s1_answers, s1_label, s1_confidence, route, s2_model, s2_root_cause, s2_confidence,
                          s2_evidence, s2_summary, review_status, triage_cost_usd)
                        VALUES (?, ?, ?, ?, ?, ?::jsonb, ?, ?::jsonb, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?)
                        """, Statement.RETURN_GENERATED_KEYS);
                ps.setString(1, r.traceId());
                ps.setString(2, r.projectId());
                ps.setString(3, r.jobId());
                ps.setString(4, r.triageVersion());
                ps.setArray(5, con.createArrayOf("text", (r.rulesFired() == null ? List.of() : r.rulesFired()).toArray()));
                ps.setString(6, json(r.state()));
                ps.setString(7, r.s1Backend());
                ps.setString(8, r.s1Answers() == null ? null : json(r.s1Answers()));
                ps.setString(9, r.s1Label());
                ps.setObject(10, r.s1Confidence(), Types.DOUBLE);
                ps.setString(11, r.route());
                ps.setString(12, r.s2Model());
                ps.setString(13, r.s2RootCause());
                ps.setObject(14, r.s2Confidence(), Types.DOUBLE);
                ps.setString(15, r.s2Evidence() == null ? null : json(r.s2Evidence()));
                ps.setString(16, r.s2Summary());
                ps.setString(17, r.reviewStatus() == null ? "none" : r.reviewStatus());
                ps.setBigDecimal(18, r.triageCostUsd() == null ? BigDecimal.ZERO : r.triageCostUsd());
                return ps;
            }, kh);
        } catch (DuplicateKeyException e) {
            throw new ConflictException("triage for trace " + r.traceId() + " version " + r.triageVersion() + " already exists");
        }
        Map<String, Object> keys = kh.getKeys();
        return ((Number) keys.get("id")).longValue();
    }

    private String json(Object o) {
        try {
            return om.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("invalid JSON payload", e);
        }
    }
}

package dev.agentobs.ingest;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Types;
import java.util.Arrays;
import java.util.List;

/** 批量写 obs.spans；ON CONFLICT DO NOTHING，span 不可变所以不做 UPDATE。 */
@Repository
public class SpanRepository {

    private static final String INSERT = """
            INSERT INTO obs.spans (trace_id, span_id, parent_span_id, project_id, service_name, name, kind,
              start_time, end_time, status_code, status_message, job_id, stage, model,
              input_tokens, output_tokens, cost_usd, attempt, attributes, events)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb)
            ON CONFLICT (trace_id, span_id) DO NOTHING
            """;

    private final JdbcTemplate jdbc;

    public SpanRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** @return 实际插入的行数（重复行不计） */
    public int insertAll(List<SpanRecord> spans) {
        if (spans.isEmpty()) return 0;
        int[][] counts = jdbc.batchUpdate(INSERT, spans, 500, (ps, s) -> {
            ps.setString(1, s.traceId());
            ps.setString(2, s.spanId());
            ps.setString(3, s.parentSpanId());
            ps.setString(4, s.projectId());
            ps.setString(5, s.serviceName());
            ps.setString(6, s.name());
            ps.setString(7, s.kind());
            ps.setObject(8, s.startTime());
            ps.setObject(9, s.endTime());
            ps.setString(10, s.statusCode());
            ps.setString(11, s.statusMessage());
            ps.setString(12, s.jobId());
            ps.setString(13, s.stage());
            ps.setString(14, s.model());
            ps.setObject(15, s.inputTokens(), Types.INTEGER);
            ps.setObject(16, s.outputTokens(), Types.INTEGER);
            ps.setBigDecimal(17, s.costUsd());
            ps.setObject(18, s.attempt(), Types.INTEGER);
            ps.setString(19, s.attributesJson());
            ps.setString(20, s.eventsJson());
        });
        return Arrays.stream(counts).flatMapToInt(Arrays::stream).map(c -> Math.max(c, 0)).sum();
    }

    public int deleteOlderThan(int days, int batchSize) {
        return jdbc.update("""
                DELETE FROM obs.spans WHERE ctid IN (
                  SELECT ctid FROM obs.spans WHERE start_time < now() - make_interval(days => ?) LIMIT ?
                )""", days, batchSize);
    }
}

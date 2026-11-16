package dev.agentobs.query;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.agentobs.query.dto.MetricsSummary;
import dev.agentobs.query.dto.PageResponse;
import dev.agentobs.query.dto.ReviewDto;
import dev.agentobs.query.dto.SpanDto;
import dev.agentobs.query.dto.TraceDetail;
import dev.agentobs.query.dto.TraceListItem;
import dev.agentobs.query.dto.TraceSummaryDto;
import dev.agentobs.query.dto.TriageDto;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 公开查询（SPEC 7.1 / 7.2）：手写 SQL，所有列表先按 project_id + 时间过滤。 */
@Service
public class TraceQueryService {

    public static final int MAX_RANGE_DAYS = 31;
    public static final int MAX_PAGE_SIZE = 100;

    private static final Set<String> SORTS = Set.of(
            "severity", "start_time", "duration", "cost", "s1_confidence", "s2_confidence");

    private final JdbcTemplate jdbc;
    private final ObjectMapper om;

    public TraceQueryService(JdbcTemplate jdbc, ObjectMapper om) {
        this.jdbc = jdbc;
        this.om = om;
    }

    public record ListFilter(
            String projectId, OffsetDateTime from, OffsetDateTime to,
            String status, String s1Label, String route, Double confMin, Double confMax,
            String stage, String reviewStatus, String sort, boolean desc, int page, int size) {}

    public static void validateRange(OffsetDateTime from, OffsetDateTime to) {
        if (from == null || to == null) throw new BadRequestException("from and to are required");
        if (!to.isAfter(from)) throw new BadRequestException("to must be after from");
        if (Duration.between(from, to).toDays() > MAX_RANGE_DAYS) {
            throw new BadRequestException("time range must be <= " + MAX_RANGE_DAYS + " days");
        }
    }

    public PageResponse<TraceListItem> list(ListFilter f) {
        validateRange(f.from(), f.to());
        if (f.projectId() == null || f.projectId().isBlank()) throw new BadRequestException("project_id is required");
        if (f.size() < 1 || f.size() > MAX_PAGE_SIZE) throw new BadRequestException("size must be 1.." + MAX_PAGE_SIZE);
        if (f.page() < 0) throw new BadRequestException("page must be >= 0");
        String sort = f.sort() == null ? "severity" : f.sort();
        if (!SORTS.contains(sort)) throw new BadRequestException("unknown sort " + sort);

        StringBuilder where = new StringBuilder("""
                WITH roots AS (
                  SELECT s.trace_id, s.project_id, s.job_id, s.start_time, s.end_time, s.duration_ms, s.status_code
                  FROM obs.spans s
                  WHERE s.parent_span_id IS NULL AND s.project_id = ? AND s.start_time >= ? AND s.start_time < ?
                ),
                latest AS (
                  SELECT DISTINCT ON (t.trace_id) t.*
                  FROM obs.trace_triage t
                  WHERE t.project_id = ? AND t.created_at >= ?::timestamptz - interval '1 day'
                  ORDER BY t.trace_id, t.created_at DESC
                ),
                agg AS (
                  SELECT a.trace_id, count(*) AS span_count,
                         count(*) FILTER (WHERE a.status_code = 'ERROR') AS error_count,
                         sum(a.input_tokens) AS input_tokens, sum(a.output_tokens) AS output_tokens,
                         sum(a.cost_usd) AS cost_usd, max(a.attempt) AS max_attempt
                  FROM obs.spans a JOIN roots r ON r.trace_id = a.trace_id
                  GROUP BY a.trace_id
                )
                SELECT r.*, agg.span_count, agg.error_count, agg.input_tokens, agg.output_tokens, agg.cost_usd, agg.max_attempt,
                       l.id AS triage_id, l.rules_fired, l.s1_label, l.s1_confidence, l.route, l.s2_root_cause, l.s2_confidence,
                       l.review_status, l.s1_answers,
                       COALESCE((l.s1_answers -> 'severity' ->> 'answer')::int, 0) AS severity,
                       l.s1_answers -> 'suspect_stage' ->> 'answer' AS suspect_stage
                FROM roots r
                JOIN agg ON agg.trace_id = r.trace_id
                LEFT JOIN latest l ON l.trace_id = r.trace_id
                WHERE 1=1
                """);
        List<Object> args = new ArrayList<>(List.of(f.projectId(), f.from(), f.to(), f.projectId(), f.from()));
        if (f.status() != null) { where.append(" AND r.status_code = ?"); args.add(f.status()); }
        if (f.s1Label() != null) { where.append(" AND l.s1_label = ?"); args.add(f.s1Label()); }
        if (f.route() != null) { where.append(" AND l.route = ?"); args.add(f.route()); }
        if (f.confMin() != null) { where.append(" AND l.s1_confidence >= ?"); args.add(f.confMin()); }
        if (f.confMax() != null) { where.append(" AND l.s1_confidence <= ?"); args.add(f.confMax()); }
        if (f.stage() != null) { where.append(" AND l.s1_answers -> 'suspect_stage' ->> 'answer' = ?"); args.add(f.stage()); }
        if (f.reviewStatus() != null) { where.append(" AND COALESCE(l.review_status, 'none') = ?"); args.add(f.reviewStatus()); }

        String countSql = "SELECT count(*) FROM (" + where + ") c";
        long total = jdbc.queryForObject(countSql, Long.class, args.toArray());

        String order = switch (sort) {
            case "start_time" -> "r.start_time";
            case "duration" -> "r.duration_ms";
            case "cost" -> "agg.cost_usd";
            case "s1_confidence" -> "l.s1_confidence";
            case "s2_confidence" -> "l.s2_confidence";
            default -> "severity";
        };
        String dir = f.desc() ? "DESC NULLS LAST" : "ASC NULLS LAST";
        String sql = where + " ORDER BY " + order + " " + dir + ", r.start_time DESC LIMIT ? OFFSET ?";
        args.add(f.size());
        args.add((long) f.page() * f.size());
        List<TraceListItem> items = jdbc.query(sql, LIST_ROW, args.toArray());
        return new PageResponse<>(items, f.page(), f.size(), total);
    }

    private final RowMapper<TraceListItem> LIST_ROW = (rs, i) -> new TraceListItem(
            rs.getString("trace_id"), rs.getString("project_id"), rs.getString("job_id"),
            odt(rs, "start_time"), odt(rs, "end_time"), rs.getDouble("duration_ms"), rs.getString("status_code"),
            rs.getInt("span_count"), rs.getInt("error_count"),
            nullableLong(rs, "input_tokens"), nullableLong(rs, "output_tokens"), rs.getBigDecimal("cost_usd"),
            nullableInt(rs, "max_attempt"),
            nullableLong(rs, "triage_id"), arr(rs, "rules_fired"), rs.getString("s1_label"), nullableDouble(rs, "s1_confidence"),
            rs.getString("route"), rs.getString("s2_root_cause"), nullableDouble(rs, "s2_confidence"),
            rs.getString("review_status") == null ? "none" : rs.getString("review_status"),
            rs.getInt("severity"), rs.getString("suspect_stage"));

    public TraceDetail detail(String traceId) {
        TraceSummaryDto summary = summary(traceId);
        List<SpanDto> spans = spansInTreeOrder(traceId);
        TriageDto triage = latestTriage(traceId);
        List<ReviewDto> reviews = triage == null ? List.of() : reviewsFor(triage.id());
        return new TraceDetail(summary, spans, triage, reviews);
    }

    public TraceSummaryDto summary(String traceId) {
        List<TraceSummaryDto> rows = jdbc.query(
                "SELECT * FROM obs.trace_summary WHERE trace_id = ?", (rs, i) -> new TraceSummaryDto(
                        rs.getString("trace_id"), rs.getString("project_id"), rs.getString("job_id"),
                        odt(rs, "start_time"), odt(rs, "end_time"), rs.getBoolean("has_root"), rs.getString("root_status"),
                        rs.getInt("span_count"), rs.getInt("error_count"),
                        nullableLong(rs, "input_tokens"), nullableLong(rs, "output_tokens"), rs.getBigDecimal("cost_usd"),
                        nullableInt(rs, "max_attempt")), traceId);
        if (rows.isEmpty()) throw new NotFoundException("trace " + traceId + " not found");
        return rows.get(0);
    }

    /** 按树顺序（DFS，兄弟按 start_time）返回 span，并给出 depth。 */
    public List<SpanDto> spansInTreeOrder(String traceId) {
        List<SpanDto> flat = jdbc.query("""
                SELECT trace_id, span_id, parent_span_id, name, kind, start_time, end_time, duration_ms,
                       status_code, status_message, stage, model, input_tokens, output_tokens, cost_usd, attempt,
                       attributes::text AS attributes, events::text AS events
                FROM obs.spans WHERE trace_id = ? ORDER BY start_time, span_id
                """, (rs, i) -> new SpanDto(
                rs.getString("span_id"), rs.getString("parent_span_id"), rs.getString("name"), rs.getString("kind"),
                odt(rs, "start_time"), odt(rs, "end_time"), rs.getDouble("duration_ms"),
                rs.getString("status_code"), rs.getString("status_message"), rs.getString("stage"), rs.getString("model"),
                nullableInt(rs, "input_tokens"), nullableInt(rs, "output_tokens"), rs.getBigDecimal("cost_usd"),
                nullableInt(rs, "attempt"), parseJson(rs.getString("attributes")), parseJsonList(rs.getString("events")), 0),
                traceId);
        Map<String, List<SpanDto>> children = new HashMap<>();
        Set<String> ids = new java.util.HashSet<>();
        for (SpanDto s : flat) ids.add(s.spanId());
        List<SpanDto> roots = new ArrayList<>();
        for (SpanDto s : flat) {
            if (s.parentSpanId() == null || !ids.contains(s.parentSpanId())) roots.add(s);
            else children.computeIfAbsent(s.parentSpanId(), k -> new ArrayList<>()).add(s);
        }
        List<SpanDto> out = new ArrayList<>(flat.size());
        for (SpanDto r : roots) dfs(r, 0, children, out);
        return out;
    }

    private void dfs(SpanDto s, int depth, Map<String, List<SpanDto>> children, List<SpanDto> out) {
        out.add(s.withDepth(depth));
        for (SpanDto c : children.getOrDefault(s.spanId(), List.of())) dfs(c, depth + 1, children, out);
    }

    public TriageDto latestTriage(String traceId) {
        List<TriageDto> rows = jdbc.query(
                "SELECT *, state::text AS state_json, s1_answers::text AS s1_json, s2_evidence::text AS s2_json "
                        + "FROM obs.trace_triage WHERE trace_id = ? ORDER BY created_at DESC LIMIT 1",
                TRIAGE_ROW, traceId);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public final RowMapper<TriageDto> TRIAGE_ROW = (rs, i) -> new TriageDto(
            rs.getLong("id"), rs.getString("trace_id"), rs.getString("project_id"), rs.getString("job_id"),
            rs.getString("triage_version"), arr(rs, "rules_fired"), parseJson(rs.getString("state_json")),
            rs.getString("s1_backend"), parseJson(rs.getString("s1_json")), rs.getString("s1_label"),
            nullableDouble(rs, "s1_confidence"), rs.getString("route"), rs.getString("s2_model"),
            rs.getString("s2_root_cause"), nullableDouble(rs, "s2_confidence"), parseJsonList(rs.getString("s2_json")),
            rs.getString("s2_summary"), rs.getString("review_status"), rs.getBigDecimal("triage_cost_usd"),
            odt(rs, "created_at"));

    public List<ReviewDto> reviewsFor(long triageId) {
        return jdbc.query("SELECT * FROM obs.reviews WHERE triage_id = ? ORDER BY created_at",
                (rs, i) -> new ReviewDto(rs.getLong("id"), rs.getLong("triage_id"), rs.getString("reviewer"),
                        rs.getString("human_label"), rs.getString("human_root_cause"), rs.getString("note"),
                        odt(rs, "created_at")), triageId);
    }

    public MetricsSummary metricsSummary(String projectId, OffsetDateTime from, OffsetDateTime to) {
        validateRange(from, to);
        Map<String, Object> m = jdbc.queryForMap("""
                WITH roots AS (
                  SELECT trace_id, status_code, duration_ms
                  FROM obs.spans WHERE parent_span_id IS NULL AND project_id = ? AND start_time >= ? AND start_time < ?
                ),
                cost AS (
                  SELECT r.trace_id, sum(s.cost_usd) AS cost_usd
                  FROM roots r JOIN obs.spans s ON s.trace_id = r.trace_id GROUP BY r.trace_id
                ),
                tri AS (
                  SELECT DISTINCT ON (t.trace_id) t.route, t.review_status
                  FROM obs.trace_triage t JOIN roots r ON r.trace_id = t.trace_id
                  ORDER BY t.trace_id, t.created_at DESC
                )
                SELECT (SELECT count(*) FROM roots) AS jobs,
                       (SELECT count(*) FROM roots WHERE status_code = 'OK') AS succeeded,
                       (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) FROM roots) AS p95_ms,
                       (SELECT avg(cost_usd) FROM cost) AS avg_cost,
                       (SELECT count(*) FROM tri) AS triaged,
                       (SELECT count(*) FROM tri WHERE route = 'escalate') AS escalated,
                       (SELECT count(*) FROM obs.trace_triage WHERE project_id = ? AND review_status = 'pending') AS pending_reviews
                """, projectId, from, to, projectId);
        long jobs = ((Number) m.get("jobs")).longValue();
        long succeeded = ((Number) m.get("succeeded")).longValue();
        long triaged = ((Number) m.get("triaged")).longValue();
        long escalated = ((Number) m.get("escalated")).longValue();
        Object p95 = m.get("p95_ms");
        Object avg = m.get("avg_cost");
        return new MetricsSummary(
                jobs, succeeded,
                jobs == 0 ? null : (double) succeeded / jobs,
                p95 == null ? null : ((Number) p95).doubleValue(),
                avg == null ? null : ((BigDecimal) avg).doubleValue(),
                triaged == 0 ? null : (double) escalated / triaged,
                ((Number) m.get("pending_reviews")).longValue());
    }

    // ---------- helpers ----------

    static OffsetDateTime odt(ResultSet rs, String col) throws SQLException {
        return rs.getObject(col, OffsetDateTime.class);
    }

    static Long nullableLong(ResultSet rs, String col) throws SQLException {
        long v = rs.getLong(col);
        return rs.wasNull() ? null : v;
    }

    static Integer nullableInt(ResultSet rs, String col) throws SQLException {
        int v = rs.getInt(col);
        return rs.wasNull() ? null : v;
    }

    static Double nullableDouble(ResultSet rs, String col) throws SQLException {
        double v = rs.getDouble(col);
        return rs.wasNull() ? null : v;
    }

    static List<String> arr(ResultSet rs, String col) throws SQLException {
        java.sql.Array a = rs.getArray(col);
        if (a == null) return List.of();
        return Arrays.asList((String[]) a.getArray());
    }

    Map<String, Object> parseJson(String s) {
        if (s == null) return null;
        try {
            return om.readValue(s, new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return Map.of("_raw", s);
        }
    }

    List<Map<String, Object>> parseJsonList(String s) {
        if (s == null) return null;
        try {
            return om.readValue(s, new TypeReference<List<Map<String, Object>>>() {});
        } catch (Exception e) {
            return List.of();
        }
    }
}

package dev.agentobs.triage;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 基线：该项目最近 window 条成功 trace 里，每个 stage 的中位耗时、中位成本、样本数，
 * 以及 stage 上 agentobs.metric.* 的中位数（供 S1 判断 score 之类的分布偏离）。
 */
@Service
public class BaselineService {

    public static final int MIN_SAMPLES = 20;

    private final JdbcTemplate jdbc;

    public BaselineService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public record StageBaseline(String stage, Double medianDurationMs, Double medianCostUsd, long samples,
                                Map<String, Double> medianMetrics) {}

    public record Baselines(String projectId, int window, long traces, Map<String, StageBaseline> stages) {}

    public Baselines compute(String projectId, int window) {
        int w = Math.max(1, Math.min(window, 2000));
        List<Map<String, Object>> rows = jdbc.queryForList("""
                WITH recent AS (
                  SELECT trace_id FROM obs.spans
                  WHERE parent_span_id IS NULL AND project_id = ? AND status_code = 'OK'
                  ORDER BY start_time DESC LIMIT ?
                ),
                stage_spans AS (
                  SELECT s.trace_id, s.stage, s.duration_ms, s.attributes
                  FROM obs.spans s JOIN recent r ON r.trace_id = s.trace_id
                  WHERE s.stage IS NOT NULL AND s.name LIKE 'stage %'
                ),
                stage_cost AS (
                  SELECT s.trace_id, s.stage, sum(s.cost_usd) AS cost_usd
                  FROM obs.spans s JOIN recent r ON r.trace_id = s.trace_id
                  WHERE s.stage IS NOT NULL GROUP BY s.trace_id, s.stage
                )
                SELECT ss.stage,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY ss.duration_ms) AS median_duration_ms,
                       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY sc.cost_usd) FROM stage_cost sc WHERE sc.stage = ss.stage) AS median_cost_usd,
                       count(*) AS samples
                FROM stage_spans ss GROUP BY ss.stage
                """, projectId, w);
        List<Map<String, Object>> metricRows = jdbc.queryForList("""
                WITH recent AS (
                  SELECT trace_id FROM obs.spans
                  WHERE parent_span_id IS NULL AND project_id = ? AND status_code = 'OK'
                  ORDER BY start_time DESC LIMIT ?
                )
                SELECT s.stage, kv.key AS metric,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY (kv.value)::text::double precision) AS median
                FROM obs.spans s JOIN recent r ON r.trace_id = s.trace_id,
                     jsonb_each(s.attributes) kv
                WHERE s.stage IS NOT NULL AND s.name LIKE 'stage %'
                  AND kv.key LIKE 'agentobs.metric.%' AND jsonb_typeof(kv.value) = 'number'
                GROUP BY s.stage, kv.key
                """, projectId, w);
        Map<String, Map<String, Double>> metrics = new LinkedHashMap<>();
        for (Map<String, Object> m : metricRows) {
            String stage = (String) m.get("stage");
            String key = ((String) m.get("metric")).substring("agentobs.metric.".length());
            metrics.computeIfAbsent(stage, k -> new LinkedHashMap<>()).put(key, toDouble(m.get("median")));
        }
        Map<String, StageBaseline> stages = new LinkedHashMap<>();
        long traces = 0;
        for (Map<String, Object> r : rows) {
            String stage = (String) r.get("stage");
            long samples = ((Number) r.get("samples")).longValue();
            traces = Math.max(traces, samples);
            stages.put(stage, new StageBaseline(stage, toDouble(r.get("median_duration_ms")),
                    toDouble(r.get("median_cost_usd")), samples, metrics.getOrDefault(stage, Map.of())));
        }
        return new Baselines(projectId, w, traces, stages);
    }

    private static Double toDouble(Object o) {
        return o == null ? null : ((Number) o).doubleValue();
    }
}

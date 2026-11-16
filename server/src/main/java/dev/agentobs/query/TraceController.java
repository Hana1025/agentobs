package dev.agentobs.query;

import dev.agentobs.query.dto.MetricsSummary;
import dev.agentobs.query.dto.PageResponse;
import dev.agentobs.query.dto.TraceDetail;
import dev.agentobs.query.dto.TraceListItem;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.util.Map;

@RestController
@RequestMapping("/api")
@Tag(name = "traces")
public class TraceController {

    private final TraceQueryService service;

    public TraceController(TraceQueryService service) {
        this.service = service;
    }

    @GetMapping("/traces")
    @Operation(operationId = "listTraces", summary = "trace 列表（时间范围必填，跨度 ≤ 31 天）")
    public PageResponse<TraceListItem> list(
            @RequestParam("project_id") String projectId,
            @RequestParam("from") @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime from,
            @RequestParam("to") @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime to,
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "s1_label", required = false) String s1Label,
            @RequestParam(value = "route", required = false) String route,
            @RequestParam(value = "conf_min", required = false) Double confMin,
            @RequestParam(value = "conf_max", required = false) Double confMax,
            @RequestParam(value = "stage", required = false) String stage,
            @RequestParam(value = "review_status", required = false) String reviewStatus,
            @RequestParam(value = "sort", required = false, defaultValue = "severity") String sort,
            @RequestParam(value = "desc", required = false, defaultValue = "true") boolean desc,
            @RequestParam(value = "page", required = false, defaultValue = "0") int page,
            @RequestParam(value = "size", required = false, defaultValue = "50") int size) {
        return service.list(new TraceQueryService.ListFilter(projectId, from, to, blank(status), blank(s1Label),
                blank(route), confMin, confMax, blank(stage), blank(reviewStatus), sort, desc, page, size));
    }

    @GetMapping("/traces/{traceId}")
    @Operation(operationId = "getTrace", summary = "trace 详情：summary、树顺序 span、最新 triage、复核记录")
    public TraceDetail detail(@PathVariable String traceId) {
        return service.detail(traceId.toLowerCase());
    }

    @GetMapping("/metrics/summary")
    @Operation(operationId = "getMetricsSummary", summary = "成功率、P95 耗时、平均单 job 成本、升级率、待复核数")
    public MetricsSummary metrics(
            @RequestParam("project_id") String projectId,
            @RequestParam("from") @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime from,
            @RequestParam("to") @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime to) {
        return service.metricsSummary(projectId, from, to);
    }

    @GetMapping("/me")
    @Operation(operationId = "getMe", summary = "当前用户名（来自 X-Forwarded-User，没有时 anonymous）")
    public Map<String, String> me(HttpServletRequest req) {
        return Map.of("user", currentUser(req));
    }

    public static String currentUser(HttpServletRequest req) {
        String u = req.getHeader("X-Forwarded-User");
        return (u == null || u.isBlank()) ? "anonymous" : u.trim();
    }

    private static String blank(String s) {
        return (s == null || s.isBlank()) ? null : s;
    }
}

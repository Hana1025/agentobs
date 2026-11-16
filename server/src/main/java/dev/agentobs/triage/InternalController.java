package dev.agentobs.triage;

import dev.agentobs.query.TraceQueryService;
import dev.agentobs.query.dto.SpanDto;
import dev.agentobs.query.dto.TraceSummaryDto;
import io.swagger.v3.oas.annotations.Hidden;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/** 内部接口：只对 triage-worker 和 eval 脚本，校验 X-Agentobs-Token（内部 token），不经 nginx 暴露。 */
@RestController
@RequestMapping("/api/internal")
@Hidden
public class InternalController {

    public record InternalTrace(TraceSummaryDto summary, List<SpanDto> spans) {}

    private final TraceQueryService query;
    private final TriageRepository triage;
    private final BaselineService baselines;

    public InternalController(TraceQueryService query, TriageRepository triage, BaselineService baselines) {
        this.query = query;
        this.triage = triage;
        this.baselines = baselines;
    }

    @GetMapping("/traces/{traceId}")
    public InternalTrace trace(@PathVariable String traceId) {
        String id = traceId.toLowerCase();
        return new InternalTrace(query.summary(id), query.spansInTreeOrder(id));
    }

    @GetMapping("/baselines")
    public BaselineService.Baselines baselines(
            @RequestParam("project_id") String projectId,
            @RequestParam(value = "window", defaultValue = "200") int window) {
        return baselines.compute(projectId, window);
    }

    @PostMapping("/triage")
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, Object> triage(@RequestBody @Valid TriageRequest req) {
        long id = triage.insert(req);
        return Map.of("id", id);
    }
}

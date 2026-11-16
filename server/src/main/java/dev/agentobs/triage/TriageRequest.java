package dev.agentobs.triage;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/** POST /api/internal/triage 请求体（snake_case，与 worker 的 pydantic 模型一致）。 */
public record TriageRequest(
        @JsonProperty("trace_id") @NotBlank @Pattern(regexp = "[0-9a-f]{32}") String traceId,
        @JsonProperty("project_id") @NotBlank String projectId,
        @JsonProperty("job_id") String jobId,
        @JsonProperty("triage_version") @NotBlank String triageVersion,
        @JsonProperty("rules_fired") List<String> rulesFired,
        @JsonProperty("state") @NotNull Map<String, Object> state,
        @JsonProperty("s1_backend") String s1Backend,
        @JsonProperty("s1_answers") Map<String, Object> s1Answers,
        @JsonProperty("s1_label") String s1Label,
        @JsonProperty("s1_confidence") Double s1Confidence,
        @JsonProperty("route") @NotBlank @Pattern(regexp = "pass|alert|escalate") String route,
        @JsonProperty("s2_model") String s2Model,
        @JsonProperty("s2_root_cause") String s2RootCause,
        @JsonProperty("s2_confidence") Double s2Confidence,
        @JsonProperty("s2_evidence") List<Map<String, Object>> s2Evidence,
        @JsonProperty("s2_summary") String s2Summary,
        @JsonProperty("review_status") @Pattern(regexp = "none|pending|reviewed") String reviewStatus,
        @JsonProperty("triage_cost_usd") BigDecimal triageCostUsd) {
}

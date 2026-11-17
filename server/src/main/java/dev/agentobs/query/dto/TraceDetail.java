package dev.agentobs.query.dto;

import jakarta.validation.constraints.NotNull;

import java.util.List;

public record TraceDetail(@NotNull TraceSummaryDto summary, @NotNull List<SpanDto> spans, TriageDto triage,
                          @NotNull List<ReviewDto> reviews) {
}

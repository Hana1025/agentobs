package dev.agentobs.query.dto;

import java.util.List;

public record TraceDetail(TraceSummaryDto summary, List<SpanDto> spans, TriageDto triage, List<ReviewDto> reviews) {
}

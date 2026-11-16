package dev.agentobs.query.dto;

import java.time.OffsetDateTime;

public record ReviewDto(long id, long triageId, String reviewer, String humanLabel, String humanRootCause,
                        String note, OffsetDateTime createdAt) {
}

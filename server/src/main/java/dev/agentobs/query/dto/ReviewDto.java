package dev.agentobs.query.dto;

import jakarta.validation.constraints.NotNull;

import java.time.OffsetDateTime;

public record ReviewDto(long id, long triageId, @NotNull String reviewer, @NotNull String humanLabel,
                        String humanRootCause, String note, @NotNull OffsetDateTime createdAt) {
}

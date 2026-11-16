package dev.agentobs.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "agentobs")
public record AgentobsProperties(
        String ingestToken,
        String internalToken,
        int retentionDays,
        int retentionBatchSize) {

    public AgentobsProperties {
        if (retentionDays <= 0) retentionDays = 90;
        if (retentionBatchSize <= 0) retentionBatchSize = 5000;
    }
}

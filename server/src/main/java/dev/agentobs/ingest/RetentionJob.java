package dev.agentobs.ingest;

import dev.agentobs.config.AgentobsProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** 每天删除超过 retention-days 的 spans，分批删。triage / reviews / eval_cases 不删。 */
@Component
public class RetentionJob {

    private static final Logger log = LoggerFactory.getLogger(RetentionJob.class);

    private final SpanRepository repo;
    private final AgentobsProperties props;

    public RetentionJob(SpanRepository repo, AgentobsProperties props) {
        this.repo = repo;
        this.props = props;
    }

    @Scheduled(cron = "${agentobs.retention-cron:0 30 3 * * *}")
    public void run() {
        int total = 0;
        int n;
        do {
            n = repo.deleteOlderThan(props.retentionDays(), props.retentionBatchSize());
            total += n;
        } while (n == props.retentionBatchSize());
        if (total > 0) log.info("retention: deleted {} spans older than {} days", total, props.retentionDays());
    }
}

package dev.agentobs.review;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicLong;

/** agentobs_review_backlog：待复核数，每 60 秒刷新一次。 */
@Component
public class ReviewBacklogGauge {

    private final JdbcTemplate jdbc;
    private final AtomicLong backlog = new AtomicLong();

    public ReviewBacklogGauge(JdbcTemplate jdbc, MeterRegistry registry) {
        this.jdbc = jdbc;
        Gauge.builder("agentobs_review_backlog", backlog, AtomicLong::doubleValue)
                .description("triage rows waiting for human review").register(registry);
    }

    @Scheduled(fixedDelay = 60_000, initialDelay = 5_000)
    public void refresh() {
        try {
            Long n = jdbc.queryForObject(
                    "SELECT count(*) FROM obs.trace_triage WHERE review_status = 'pending'", Long.class);
            backlog.set(n == null ? 0 : n);
        } catch (Exception ignored) {
            // 数据库暂时不可用时保留上一次的值
        }
    }
}

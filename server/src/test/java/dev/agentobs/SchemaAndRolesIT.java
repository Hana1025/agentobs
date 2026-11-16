package dev.agentobs;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** M1 验收：Flyway 建出表和视图；pgmq 队列存在；三个账号权限符合 SPEC 4.3。 */
class SchemaAndRolesIT extends PostgresTestBase {

    @Autowired JdbcTemplate jdbc;

    @Test
    void flywayCreatesObsTablesAndView() {
        List<String> tables = jdbc.queryForList(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'obs' ORDER BY 1",
                String.class);
        assertThat(tables).contains("spans", "trace_triage", "reviews", "eval_cases", "eval_runs", "trace_summary");
    }

    @Test
    void pgmqQueuesExist() {
        List<String> queues = jdbc.queryForList("SELECT queue_name FROM pgmq.meta ORDER BY 1", String.class);
        assertThat(queues).containsExactly("job_finished", "job_finished_dlq");
    }

    @Test
    void pipelineRoleCanOnlySend() throws SQLException {
        try (Connection c = connectAs("agentobs_pipeline", PIPELINE_PW); Statement st = c.createStatement()) {
            st.execute("SELECT pgmq.send('job_finished', '{\"v\":1}'::jsonb)");
            assertThatThrownBy(() -> st.execute("SELECT * FROM obs.spans LIMIT 1"))
                    .hasMessageContaining("permission denied");
            assertThatThrownBy(() -> st.execute("SELECT * FROM pgmq.read('job_finished', 1, 1)"))
                    .hasMessageContaining("permission denied");
        }
    }

    @Test
    void triageRoleCanReadArchiveButNotObs() throws SQLException {
        try (Connection c = connectAs("agentobs_triage", TRIAGE_PW); Statement st = c.createStatement()) {
            st.execute("SELECT pgmq.send('job_finished_dlq', '{\"v\":1}'::jsonb)");
            st.execute("SELECT * FROM pgmq.read('job_finished', 1, 1)");
            st.execute("SELECT pgmq.archive('job_finished_dlq', 1)");
            assertThatThrownBy(() -> st.execute("SELECT * FROM obs.spans LIMIT 1"))
                    .hasMessageContaining("permission denied");
        }
    }

    private static Connection connectAs(String user, String pw) throws SQLException {
        return DriverManager.getConnection(POSTGRES.getJdbcUrl(), user, pw);
    }
}

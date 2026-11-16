package dev.agentobs;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.images.builder.ImageFromDockerfile;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.nio.file.Path;

/**
 * 集成测试基类：用 deploy/postgres 的 Dockerfile 构建带 pgmq 的镜像，
 * 并跑 init SQL（建账号、队列、obs schema），再由 Flyway 建表。
 */
@Testcontainers
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
public abstract class PostgresTestBase {

    static final String SERVER_PW = "server-test";
    static final String PIPELINE_PW = "pipeline-test";
    static final String TRIAGE_PW = "triage-test";

    @SuppressWarnings("resource")
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>(
            DockerImageName.parse(buildImage()).asCompatibleSubstituteFor("postgres"))
            .withDatabaseName("agentobs")
            .withUsername("postgres")
            .withPassword("postgres")
            .withEnv("AGENTOBS_SERVER_DB_PASSWORD", SERVER_PW)
            .withEnv("AGENTOBS_PIPELINE_DB_PASSWORD", PIPELINE_PW)
            .withEnv("AGENTOBS_TRIAGE_DB_PASSWORD", TRIAGE_PW);

    static {
        POSTGRES.start();
    }

    private static String buildImage() {
        Path deployDir = Path.of("..", "deploy", "postgres").toAbsolutePath().normalize();
        return new ImageFromDockerfile("agentobs/postgres-test", false)
                .withFileFromPath(".", deployDir)
                .get();
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        r.add("spring.datasource.username", () -> "agentobs_server");
        r.add("spring.datasource.password", () -> SERVER_PW);
        r.add("agentobs.ingest-token", () -> "ingest-test-token");
        r.add("agentobs.internal-token", () -> "internal-test-token");
    }
}

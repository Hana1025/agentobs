plugins {
    java
    id("org.springframework.boot") version "3.4.2"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "dev.agentobs"
version = "0.1.0"

java {
    toolchain { languageVersion.set(JavaLanguageVersion.of(21)) }
}

repositories { mavenCentral() }

// Spring Boot 3.4.2 的 BOM 带 Testcontainers 1.20.x，连不上 Docker 29（要求 API ≥ 1.44）；1.21.4 修复了这一点
extra["testcontainers.version"] = "1.21.4"

val otelProtoVersion = "1.5.0-alpha"
val springdocVersion = "2.8.4"

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("io.micrometer:micrometer-registry-prometheus")
    implementation("org.flywaydb:flyway-core")
    implementation("org.flywaydb:flyway-database-postgresql")
    implementation("org.springdoc:springdoc-openapi-starter-webmvc-ui:$springdocVersion")
    implementation("io.opentelemetry.proto:opentelemetry-proto:$otelProtoVersion")
    implementation("com.google.protobuf:protobuf-java-util:4.29.3")
    runtimeOnly("org.postgresql:postgresql")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.springframework.boot:spring-boot-testcontainers")
    testImplementation("org.testcontainers:junit-jupiter")
    testImplementation("org.testcontainers:postgresql")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.withType<Test> {
    useJUnitPlatform()
    // Testcontainers 需要 Docker；CI 里有，本地没有时用 -PskipIT 跳过
    if (project.hasProperty("skipIT")) {
        exclude("**/*IT.class")
    }
}

tasks.withType<JavaCompile> {
    options.compilerArgs.add("-parameters")
}

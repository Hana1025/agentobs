# 用官方 gradle 镜像构建，运行时只带 JRE
FROM gradle:8.12.1-jdk21 AS build
WORKDIR /src
COPY build.gradle.kts settings.gradle.kts ./
RUN gradle --no-daemon dependencies > /dev/null 2>&1 || true
COPY src ./src
RUN gradle --no-daemon bootJar -x test

FROM eclipse-temurin:21.0.6_7-jre-alpine
RUN addgroup -S app && adduser -S app -G app
WORKDIR /app
COPY --from=build /src/build/libs/*.jar app.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

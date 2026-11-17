# 官方 postgres:17 + pgmq（按 pgmq 官方 INSTALLATION 从源码安装）
# pgmq 1.6 是纯 SQL 扩展：make install 只产出 pgmq.control 和 pgmq--*.sql，没有 .so
FROM postgres:17.6-bookworm AS build
ARG PGMQ_VERSION=1.6.1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://github.com/pgmq/pgmq/archive/refs/tags/v${PGMQ_VERSION}.tar.gz" -o /tmp/pgmq.tar.gz \
    && mkdir -p /tmp/pgmq && tar -xzf /tmp/pgmq.tar.gz -C /tmp/pgmq --strip-components=1 \
    && cd /tmp/pgmq/pgmq-extension && make && make install \
    && mkdir -p /out && cp /usr/share/postgresql/17/extension/pgmq* /out/

FROM postgres:17.6-bookworm
COPY --from=build /out/ /usr/share/postgresql/17/extension/
COPY init/ /docker-entrypoint-initdb.d/

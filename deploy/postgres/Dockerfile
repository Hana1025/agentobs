# 官方 postgres:17 + pgmq（按 pgmq 官方 INSTALLATION 从源码编译安装）
FROM postgres:17.6-bookworm AS build
ARG PGMQ_VERSION=1.6.1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://github.com/pgmq/pgmq/archive/refs/tags/v${PGMQ_VERSION}.tar.gz" -o /tmp/pgmq.tar.gz \
    && mkdir -p /tmp/pgmq && tar -xzf /tmp/pgmq.tar.gz -C /tmp/pgmq --strip-components=1 \
    && cd /tmp/pgmq/pgmq-extension && make && make install

FROM postgres:17.6-bookworm
COPY --from=build /usr/lib/postgresql/17/lib/pgmq.so /usr/lib/postgresql/17/lib/
COPY --from=build /usr/share/postgresql/17/extension/pgmq* /usr/share/postgresql/17/extension/
COPY init/ /docker-entrypoint-initdb.d/

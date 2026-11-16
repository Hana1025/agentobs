#!/bin/bash
# 由官方镜像 entrypoint 在首次初始化时执行；密码来自环境变量，不写进 SQL 文件。
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v server_pw="$AGENTOBS_SERVER_DB_PASSWORD" \
  -v pipeline_pw="$AGENTOBS_PIPELINE_DB_PASSWORD" \
  -v triage_pw="$AGENTOBS_TRIAGE_DB_PASSWORD" <<SQL
CREATE ROLE agentobs_server   LOGIN PASSWORD :'server_pw';
CREATE ROLE agentobs_pipeline LOGIN PASSWORD :'pipeline_pw';
CREATE ROLE agentobs_triage   LOGIN PASSWORD :'triage_pw';
SQL

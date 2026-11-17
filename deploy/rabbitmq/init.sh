#!/bin/sh
# 通过 RabbitMQ 管理 API 创建账号、权限和队列拓扑（与 agentobs/queue/rabbitmq.py 的 topology() 一致）。
# 由 compose 的 rabbitmq-init 一次性服务执行；密码来自环境变量，不写进文件。
set -eu

API="http://rabbitmq:15672/api"
AUTH="${RABBITMQ_ADMIN_USER}:${RABBITMQ_ADMIN_PASSWORD}"
VHOST="%2F"
Q="job_finished"
DLQ="job_finished_dlq"

put() {
  curl -fsS -u "$AUTH" -H "content-type: application/json" -X PUT "$API/$1" -d "$2" > /dev/null
  echo "ok  $1"
}

# 队列：主队列 reject 后由原生 DLX 进 DLQ；retry 队列按消息 TTL 到期后送回主队列
put "queues/$VHOST/$DLQ"       '{"durable":true,"arguments":{"x-queue-type":"quorum"}}'
put "queues/$VHOST/$Q"         "{\"durable\":true,\"arguments\":{\"x-queue-type\":\"quorum\",\"x-dead-letter-exchange\":\"\",\"x-dead-letter-routing-key\":\"$DLQ\",\"x-delivery-limit\":20}}"
put "queues/$VHOST/$Q.retry"   "{\"durable\":true,\"arguments\":{\"x-queue-type\":\"quorum\",\"x-dead-letter-exchange\":\"\",\"x-dead-letter-routing-key\":\"$Q\"}}"

# 账号（对应 SPEC 4.3）：pipeline 只能往默认交换机发布；triage 能读三个队列、往默认交换机发布
put "users/agentobs_pipeline" "{\"password\":\"${AGENTOBS_PIPELINE_MQ_PASSWORD}\",\"tags\":\"\"}"
put "users/agentobs_triage"   "{\"password\":\"${AGENTOBS_TRIAGE_MQ_PASSWORD}\",\"tags\":\"\"}"
put "permissions/$VHOST/agentobs_pipeline" '{"configure":"^$","write":"^amq\\.default$","read":"^$"}'
put "permissions/$VHOST/agentobs_triage"   '{"configure":"^$","write":"^amq\\.default$","read":"^job_finished(_dlq|\\.retry)?$"}'

echo "rabbitmq topology ready"

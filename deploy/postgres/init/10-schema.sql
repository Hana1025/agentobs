-- obs schema 归 server 所有，表由 Flyway 创建（spring.flyway.schemas=obs）
CREATE SCHEMA IF NOT EXISTS obs AUTHORIZATION agentobs_server;
REVOKE ALL ON SCHEMA obs FROM PUBLIC;

-- pgmq 队列
CREATE EXTENSION IF NOT EXISTS pgmq;
SELECT pgmq.create('job_finished');
SELECT pgmq.create('job_finished_dlq');

-- 4.3 账号权限
-- pipeline：只能往 job_finished 发
GRANT USAGE ON SCHEMA pgmq TO agentobs_pipeline;
GRANT EXECUTE ON FUNCTION pgmq.send(text, jsonb, integer) TO agentobs_pipeline;
GRANT EXECUTE ON FUNCTION pgmq.send(text, jsonb, timestamptz) TO agentobs_pipeline;
GRANT INSERT ON TABLE pgmq.q_job_finished TO agentobs_pipeline;
GRANT USAGE ON SEQUENCE pgmq.q_job_finished_msg_id_seq TO agentobs_pipeline;
GRANT SELECT ON TABLE pgmq.meta TO agentobs_pipeline;

-- triage：两个队列 read / archive / send
GRANT USAGE ON SCHEMA pgmq TO agentobs_triage;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pgmq TO agentobs_triage;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE pgmq.q_job_finished, pgmq.q_job_finished_dlq TO agentobs_triage;
GRANT INSERT ON TABLE pgmq.a_job_finished, pgmq.a_job_finished_dlq TO agentobs_triage;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pgmq TO agentobs_triage;
GRANT SELECT ON TABLE pgmq.meta TO agentobs_triage;

-- 两个队列账号都不能碰 obs
REVOKE ALL ON SCHEMA obs FROM agentobs_pipeline, agentobs_triage;

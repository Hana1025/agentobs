-- SPEC 4.1：观测表。schema 由 deploy/postgres 初始化 SQL 创建并归 agentobs_server 所有。

CREATE TABLE obs.spans (
  trace_id        char(32)    NOT NULL,
  span_id         char(16)    NOT NULL,
  parent_span_id  char(16),
  project_id      text        NOT NULL,
  service_name    text        NOT NULL,
  name            text        NOT NULL,
  kind            text,
  start_time      timestamptz NOT NULL,
  end_time        timestamptz NOT NULL,
  duration_ms     double precision GENERATED ALWAYS AS
                    (EXTRACT(EPOCH FROM (end_time - start_time)) * 1000) STORED,
  status_code     text        NOT NULL,          -- UNSET | OK | ERROR
  status_message  text,
  job_id          text,
  stage           text,
  model           text,
  input_tokens    integer,
  output_tokens   integer,
  cost_usd        numeric(12,6),
  attempt         integer,
  attributes      jsonb       NOT NULL DEFAULT '{}',
  events          jsonb       NOT NULL DEFAULT '[]',
  ingested_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX spans_project_start_idx ON obs.spans (project_id, start_time DESC);
CREATE INDEX spans_job_idx ON obs.spans (job_id);
CREATE INDEX spans_root_idx ON obs.spans (trace_id) WHERE parent_span_id IS NULL;

CREATE VIEW obs.trace_summary AS
SELECT s.trace_id,
       max(s.project_id)                                   AS project_id,
       max(s.job_id)                                       AS job_id,
       min(s.start_time)                                   AS start_time,
       max(s.end_time)                                     AS end_time,
       bool_or(s.parent_span_id IS NULL)                   AS has_root,
       max(s.status_code) FILTER (WHERE s.parent_span_id IS NULL) AS root_status,
       count(*)                                            AS span_count,
       count(*) FILTER (WHERE s.status_code = 'ERROR')     AS error_count,
       sum(s.input_tokens)                                 AS input_tokens,
       sum(s.output_tokens)                                AS output_tokens,
       sum(s.cost_usd)                                     AS cost_usd,
       max(s.attempt)                                      AS max_attempt
FROM obs.spans s
GROUP BY s.trace_id;

CREATE TABLE obs.trace_triage (
  id               bigserial PRIMARY KEY,
  trace_id         char(32)  NOT NULL,
  project_id       text      NOT NULL,
  job_id           text,
  triage_version   text      NOT NULL,
  rules_fired      text[]    NOT NULL DEFAULT '{}',
  state            jsonb     NOT NULL,
  s1_backend       text,
  s1_answers       jsonb,
  s1_label         text,
  s1_confidence    double precision,
  route            text      NOT NULL,
  s2_model         text,
  s2_root_cause    text,
  s2_confidence    double precision,
  s2_evidence      jsonb,
  s2_summary       text,
  review_status    text      NOT NULL DEFAULT 'none',
  triage_cost_usd  numeric(12,6) NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (trace_id, triage_version)
);
CREATE INDEX trace_triage_project_created_idx ON obs.trace_triage (project_id, created_at DESC);
CREATE INDEX trace_triage_pending_idx ON obs.trace_triage (review_status) WHERE review_status = 'pending';

CREATE TABLE obs.reviews (
  id               bigserial PRIMARY KEY,
  triage_id        bigint NOT NULL REFERENCES obs.trace_triage(id),
  reviewer         text   NOT NULL,
  human_label      text   NOT NULL,
  human_root_cause text,
  note             text,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE obs.eval_cases (
  id                  bigserial PRIMARY KEY,
  trace_id            char(32),
  source              text  NOT NULL,          -- injected | human_review
  fault_type          text,
  expected_label      text  NOT NULL,
  expected_stage      text,
  expected_root_cause text,
  state_snapshot      jsonb NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX eval_cases_trace_source_idx ON obs.eval_cases (trace_id, source) WHERE trace_id IS NOT NULL;

CREATE TABLE obs.eval_runs (
  id              bigserial PRIMARY KEY,
  backend         text  NOT NULL,
  triage_version  text  NOT NULL,
  dataset_size    integer NOT NULL,
  metrics         jsonb NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);

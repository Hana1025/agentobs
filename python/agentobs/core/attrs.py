"""Span 属性名常量（SPEC 5.2）。

GenAI 语义约定仍处于 Development 状态；所有属性名集中在这里，升级时只改此文件。
"""

# resource
SERVICE_NAME = "service.name"
PROJECT_ID = "agentobs.project_id"

# 所有 span
JOB_ID = "agentobs.job.id"
STAGE = "agentobs.stage"

# 模型调用 span（OpenTelemetry GenAI semconv）
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# agentobs 自有
ATTEMPT = "agentobs.attempt"
RETRY_REASON = "agentobs.retry_reason"
COST_USD = "agentobs.cost_usd"
OUTPUT_CHARS = "agentobs.output.chars"
OUTPUT_CONTROL_CHARS = "agentobs.output.control_chars"
OUTPUT_PARSE_OK = "agentobs.output.parse_ok"
METRIC_PREFIX = "agentobs.metric."
CONTENT_INPUT = "agentobs.content.input"
CONTENT_OUTPUT = "agentobs.content.output"
EVAL_PREFIX = "agentobs.eval."  # 仅 demo 故障注入写标准答案

# 重试原因枚举
REASON_TIMEOUT = "timeout"
REASON_RATE_LIMITED = "rate_limited"
REASON_SERVER_ERROR = "server_error"
REASON_PARSE_ERROR = "parse_error"
REASON_CONNECTION = "connection"
RETRY_REASONS = (
    REASON_TIMEOUT,
    REASON_RATE_LIMITED,
    REASON_SERVER_ERROR,
    REASON_PARSE_ERROR,
    REASON_CONNECTION,
)

CONTENT_MAX_CHARS = 8000

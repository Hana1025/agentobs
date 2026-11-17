from agentobs.queue.port import (
    JOB_FINISHED,
    JOB_FINISHED_DLQ,
    InMemoryQueue,
    Message,
    QueuePort,
    dead_letter,
    queue_from_url,
)

__all__ = [
    "JOB_FINISHED",
    "JOB_FINISHED_DLQ",
    "InMemoryQueue",
    "Message",
    "PgmqQueue",
    "QueuePort",
    "RabbitMQQueue",
    "dead_letter",
    "queue_from_url",
]


def __getattr__(name: str):  # 延迟导入，避免 SDK 用户必须装 psycopg / pika
    if name == "PgmqQueue":
        from agentobs.queue.pgmq import PgmqQueue

        return PgmqQueue
    if name == "RabbitMQQueue":
        from agentobs.queue.rabbitmq import RabbitMQQueue

        return RabbitMQQueue
    raise AttributeError(name)

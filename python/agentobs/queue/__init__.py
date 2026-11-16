from agentobs.queue.port import JOB_FINISHED, JOB_FINISHED_DLQ, InMemoryQueue, Message, QueuePort

__all__ = ["JOB_FINISHED", "JOB_FINISHED_DLQ", "InMemoryQueue", "Message", "QueuePort", "PgmqQueue"]


def __getattr__(name: str):  # 延迟导入，避免 SDK 用户必须装 psycopg
    if name == "PgmqQueue":
        from agentobs.queue.pgmq import PgmqQueue

        return PgmqQueue
    raise AttributeError(name)

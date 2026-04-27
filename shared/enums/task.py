from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class TaskType(str, Enum):
    PUBLISH = "publish"
    REPLY = "reply"


class ExecutionMode(str, Enum):
    SAAS = "saas"
    LEGACY_BRIDGE = "legacy_bridge"

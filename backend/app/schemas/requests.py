"""API 入参契约（Pydantic）。"""
from pydantic import BaseModel


class ConnectionIn(BaseModel):
    name: str
    url: str
    note: str = ""


class HistoryTurn(BaseModel):
    question: str
    sql: str = ""
    summary: str = ""
    tables: list[str] = []


class AskIn(BaseModel):
    connection_id: str
    question: str
    conversation_id: str | None = None
    history: list[HistoryTurn] = []   # 兼容旧客户端；有会话时以服务端落库历史为准


class MetricIn(BaseModel):
    connection_id: str
    name: str
    aliases: str = ""
    formula: str
    table_name: str = ""
    note: str = ""


class FeedbackIn(BaseModel):
    log_id: str
    rating: str
    comment: str = ""

"""会话记忆接口：列会话、看全部轮次、删会话。"""
from fastapi import APIRouter

from ...services import conversation_service

router = APIRouter(prefix="/api", tags=["conversations"])


@router.get("/conversations")
def list_conversations(connection_id: str):
    return conversation_service.list_conversations(connection_id)


@router.get("/conversations/{conv_id}/turns")
def turns(conv_id: str, limit: int = 0, offset: int = 0):
    """limit=0 表示返回该会话的全部轮次（记忆不截断）。"""
    return conversation_service.turns(conv_id, limit or None, max(offset, 0))


@router.delete("/conversations/{conv_id}")
def remove(conv_id: str):
    conversation_service.remove(conv_id)
    return {"ok": True}

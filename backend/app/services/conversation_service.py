"""会话记忆服务：会话与会话轮次完整落库，按数据源隔离，供流水线取上下文。"""
from ..agent import memory as memory_engine
from ..core import config, deps
from ..core.errors import AppError, NotFoundError


def ensure(conn_id, conversation_id=None):
    """取当前会话；没有就新建。会话不属于该数据源时直接拒绝，避免串库复用历史。"""
    if not conversation_id:
        return deps.store.create_conversation(conn_id)
    conv = deps.store.get_conversation(conversation_id)
    if not conv:
        raise NotFoundError("会话不存在")
    if conv["conn_id"] != conn_id:
        raise AppError("该会话属于另一个数据源，不能跨库复用上下文", 400)
    return conversation_id


def history(conv_id):
    """完整历史（按轮次升序），不截断轮数；提示词侧再做预算压缩。"""
    return deps.store.list_turns(conv_id)


def memory(conv_id, conn_id="", dialect="sqlite"):
    """取结构化状态卡；老会话没有状态卡时从完整历史即时重建，之后随轮次增量更新。"""
    state = deps.store.get_memory(conv_id)
    if state:
        return state
    return memory_engine.rebuild_state(
        deps.store.list_turns(conv_id), conn_id, dialect,
        topics_limit=config.MEMORY_TOPICS,
    )


def record(conv_id, conn_id, question, result, dialect="sqlite"):
    """整轮回答原样落库；同时把 SQL 槽位增量并入状态卡，原文始终保留。"""
    previous = deps.store.get_memory(conv_id)
    if not previous:
        previous = memory_engine.rebuild_state(
            deps.store.list_turns(conv_id), conn_id, dialect,
            topics_limit=config.MEMORY_TOPICS,
        )
    turn = deps.store.add_turn(
        conv_id, conn_id, question,
        result.get("sql", ""), result.get("summary", ""),
        result.get("used_tables", []), result.get("row_count", 0),
        result.get("elapsed_ms", 0), result.get("error", ""),
        payload=result,
    )
    facts = memory_engine.build_facts(
        turn["seq"], question, result.get("sql", ""), result.get("summary", ""),
        result.get("used_tables", []), result.get("metrics_used", []), dialect,
    )
    state = memory_engine.merge_state(
        previous, facts, conn_id, topics_limit=config.MEMORY_TOPICS
    )
    deps.store.save_memory(conv_id, conn_id, state)
    return turn


def list_conversations(conn_id):
    return deps.store.list_conversations(conn_id)


def turns(conv_id, limit=None, offset=0):
    conv = deps.store.get_conversation(conv_id)
    if not conv:
        raise NotFoundError("会话不存在")
    items = deps.store.list_turns(conv_id, limit, offset)
    return {"conversation": conv, "total": deps.store.count_turns(conv_id), "turns": items}


def remove(conv_id):
    if not deps.store.get_conversation(conv_id):
        raise NotFoundError("会话不存在")
    deps.store.delete_conversation(conv_id)

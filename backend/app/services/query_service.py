"""问数业务：编排流水线、落会话记忆、落查询日志、收反馈。"""
from ..agent.pipeline import answer
from ..core import config, deps
from ..core.errors import NotFoundError
from . import connection_service, conversation_service


def ask(conn_id, question, conversation_id=None, history=None):
    engine, _ = connection_service.get_engine(conn_id)
    catalog, _ = deps.store.get_catalog(conn_id)
    if not catalog:
        raise NotFoundError("尚未读取该数据源结构")

    conv_id = conversation_service.ensure(conn_id, conversation_id)
    stored = conversation_service.history(conv_id)
    turns = stored if stored else (history or [])
    dialect = catalog.get("sqlglot_dialect") or "sqlite"
    memory = conversation_service.memory(conv_id, conn_id, dialect)

    metrics = deps.store.list_metrics(conn_id)
    result = answer(question, catalog, metrics, engine, deps.llm,
                    max_rows=config.MAX_ROWS, history=turns, memory=memory)
    result["conversation_id"] = conv_id

    # 模型调用失败会悄悄退回规则引擎，这里把原因显式带给前端，避免“配了 Key 却像没配”
    for step in result.get("trace", []):
        if step.get("node") == "SQL 生成" and step.get("status") == "warn":
            result["warning"] = step.get("detail", "模型未生效，已回退规则引擎")
            break

    log_id = deps.store.log_query(
        conn_id, question, result.get("sql", ""),
        "error" if result.get("error") else "ok",
        result.get("row_count", 0), result.get("elapsed_ms", 0),
        result.get("error", ""), result.get("trace", []),
        result.get("limit_applied", False),
    )
    result["log_id"] = log_id
    # 先有 log_id 再落会话，回放里能直接用 log_id 重跑/导出
    conversation_service.record(conv_id, conn_id, question, result, dialect=dialect)
    deps.store.touch_connection(conn_id)
    return result


def add_feedback(log_id, rating, comment=""):
    deps.store.add_feedback(log_id, rating, comment)


def list_logs(conn_id=None, limit=30):
    return deps.store.list_logs(conn_id, min(max(limit, 1), 200))

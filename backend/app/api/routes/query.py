"""问数、反馈、导出与查询日志接口。"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ...core import config
from ...schemas.requests import AskIn, FeedbackIn
from ...services import export_service, query_service

router = APIRouter(prefix="/api", tags=["query"])


@router.post("/ask")
def ask(body: AskIn):
    return query_service.ask(body.connection_id, body.question, body.conversation_id, body.history)


@router.post("/feedback")
def feedback(body: FeedbackIn):
    query_service.add_feedback(body.log_id, body.rating, body.comment)
    return {"ok": True}


@router.get("/logs")
def logs(conn_id: str = None, limit: int = 30):
    return query_service.list_logs(conn_id, limit)


@router.get("/export/{log_id}")
def export(log_id: str, connection_id: str):
    """把某次查询的完整结果（不受 200 行展示上限限制）流式导出成 CSV。"""
    engine, sql, _ = export_service.build(connection_id, log_id)
    return StreamingResponse(
        export_service.csv_stream(engine, sql, config.EXPORT_MAX_ROWS),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="query_{log_id}.csv"'},
    )

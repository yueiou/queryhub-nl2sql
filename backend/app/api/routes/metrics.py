"""指标口径接口。"""
from fastapi import APIRouter

from ...schemas.requests import MetricIn
from ...services import metric_service

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/{conn_id}")
def list_metrics(conn_id: str):
    return metric_service.list_metrics(conn_id)


@router.post("")
def add_metric(body: MetricIn):
    mid = metric_service.add_metric(
        body.connection_id, body.name, body.aliases, body.formula, body.table_name, body.note
    )
    return {"id": mid}


@router.delete("/{metric_id}")
def delete_metric(metric_id: str):
    metric_service.delete_metric(metric_id)
    return {"ok": True}

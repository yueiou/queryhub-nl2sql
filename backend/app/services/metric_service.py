"""指标口径业务：定义、查询、删除。"""
from ..core import deps
from ..core.errors import AppError


def list_metrics(conn_id):
    return deps.store.list_metrics(conn_id)


def add_metric(conn_id, name, aliases, formula, table_name, note=""):
    if not (formula or "").strip():
        raise AppError("指标公式不能为空")
    if not (name or "").strip():
        raise AppError("指标名不能为空")
    return deps.store.add_metric(conn_id, name.strip(), aliases, formula.strip(), table_name.strip(), note)


def delete_metric(metric_id):
    deps.store.delete_metric(metric_id)

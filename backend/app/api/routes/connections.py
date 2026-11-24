"""数据源接口。"""
from fastapi import APIRouter

from ...schemas.requests import ConnectionIn
from ...services import connection_service

router = APIRouter(prefix="/api/connections", tags=["connections"])


@router.get("")
def list_connections():
    return connection_service.list_connections()


@router.post("")
def create_connection(body: ConnectionIn):
    return connection_service.create(body.name, body.url, body.note)


@router.delete("/{conn_id}")
def delete_connection(conn_id: str):
    connection_service.delete(conn_id)
    return {"ok": True}


@router.post("/{conn_id}/refresh")
def refresh(conn_id: str):
    return connection_service.refresh(conn_id)


@router.get("/{conn_id}/schema")
def schema(conn_id: str):
    return connection_service.get_schema(conn_id)

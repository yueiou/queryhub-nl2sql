"""数据源业务：接入、测试、结构刷新、删除。"""
from ..core import config, deps
from ..core.errors import AppError, NotFoundError
from ..db import engine as engine_factory
from ..db.introspect import introspect
from ..schemas.catalog import queryable_tables


def _mask(url):
    if "@" in url:
        head, tail = url.split("@", 1)
        scheme = head.split("://")[0]
        return f"{scheme}://***@{tail}"
    return url


def list_connections():
    out = []
    for c in deps.store.list_connections():
        cat, refreshed = deps.store.get_catalog(c["id"])
        out.append({
            "id": c["id"], "name": c["name"], "dialect": c["dialect"], "url_masked": _mask(c["url"]),
            "table_count": len(queryable_tables(cat)) if cat else 0,
            "refreshed_at": refreshed, "created_at": c["created_at"],
        })
    return out


def create(name, url, note=""):
    try:
        resolved = engine_factory.resolve_url(url, config.DATA_DIR)
        engine = engine_factory.make_engine(resolved)
        info = engine_factory.test_connection(engine)
        catalog = introspect(engine)
    except Exception as e:
        raise AppError(f"连接失败：{e}")
    if not queryable_tables(catalog):
        raise AppError("连接成功，但没有读到业务表")
    cid = deps.store.add_connection(name.strip() or info["dialect"], resolved, info["dialect"], note)
    deps.store.save_catalog(cid, catalog)
    deps.engines.put(cid, engine)
    return {"id": cid, "dialect": info["dialect"], "table_count": len(queryable_tables(catalog))}


def get_engine(conn_id):
    conn = deps.store.get_connection(conn_id)
    if not conn:
        raise NotFoundError("数据源不存在")
    try:
        return deps.engines.get(conn_id, conn["url"]), conn
    except Exception as e:
        raise AppError(f"无法创建连接：{e}")


def delete(conn_id):
    deps.store.delete_connection(conn_id)
    deps.engines.drop(conn_id)


def refresh(conn_id):
    engine, _ = get_engine(conn_id)
    try:
        catalog = introspect(engine)
    except Exception as e:
        raise AppError(f"读取结构失败：{e}")
    deps.store.save_catalog(conn_id, catalog)
    return {"table_count": len(queryable_tables(catalog)), "dialect": catalog["dialect"]}


def get_schema(conn_id):
    catalog, refreshed = deps.store.get_catalog(conn_id)
    if not catalog:
        raise NotFoundError("尚未读取该数据源结构")
    return {"catalog": catalog, "refreshed_at": refreshed}

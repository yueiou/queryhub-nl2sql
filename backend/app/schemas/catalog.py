"""数据契约：catalog 结构与检索辅助。"""
from typing import Any, Dict, List

Catalog = Dict[str, Any]
Table = Dict[str, Any]


def queryable_tables(catalog: Catalog) -> List[Table]:
    """过滤语义注记表等系统表，返回可对外查询的业务表。"""
    return [t for t in catalog.get("tables", []) if not t["name"].startswith("_")]


def table_names(catalog: Catalog) -> List[str]:
    return [t["name"] for t in queryable_tables(catalog)]


def table_map(catalog: Catalog) -> Dict[str, Table]:
    return {t["name"]: t for t in queryable_tables(catalog)}


def describe_catalog(catalog: Catalog, limit: int = 15) -> str:
    """给“有哪些表”这类问题用的目录摘要。"""
    tables = queryable_tables(catalog)
    lines = [f"当前数据源共 {len(tables)} 张业务表："]
    for t in tables[:limit]:
        desc = t.get("comment") or "、".join(c["name"] for c in t["columns"][:4])
        lines.append(f"- {t['name']}：{desc}")
    if len(tables) > limit:
        lines.append(f"- …… 其余 {len(tables) - limit} 张表可在左侧结构树查看")
    return "\n".join(lines)


def column_label(column: Table) -> str:
    return f"{column['name']} {column.get('comment') or ''}".strip()

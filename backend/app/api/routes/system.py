"""系统级接口：首页、健康检查。"""
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ...core import config, deps
from ...core.errors import AppError

router = APIRouter(tags=["system"])


@router.get("/")
def index():
    """生产入口：Vite 构建产物，源码在 frontend/。"""
    path = os.path.join(config.FRONTEND_DIST, "index.html")
    if not os.path.exists(path):
        raise AppError("前端尚未构建，请先在 frontend 目录执行 npm install && npm run build", 404)
    return FileResponse(path)


@router.get("/api/health")
def health():
    missing = config.llm_missing()
    return {
        "ok": True,
        "llm_available": deps.llm.available,
        "llm_model": deps.llm.model if deps.llm.available else None,
        "llm_base_url": config.LLM_BASE_URL,
        "llm_missing": missing,
        "max_rows": config.MAX_ROWS,
        "data_dir": config.DATA_DIR,
    }


@router.get("/api/llm/test")
def llm_test():
    """真发一次最小请求，把失败原因原样带回，别让用户猜。"""
    if not deps.llm.available:
        return {"ok": False, "error": "配置不全，缺少：" + "、".join(config.llm_missing())}
    return deps.llm.probe()

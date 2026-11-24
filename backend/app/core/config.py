"""全局配置：路径、模型端点、查询上限。

目录约定：backend/ 放服务代码与数据，frontend/ 放 Vue 源码与构建产物。
模型配置优先级：真实环境变量 > backend/.env > 空。
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 三项必须齐全才算可用；缺任何一项都会退回规则兜底。
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
FRONTEND_DIST = os.path.join(PROJECT_ROOT, "frontend", "dist")

ENV_FILE = os.path.join(BASE_DIR, ".env")


def _load_dotenv(path):
    """极简 .env 读取，不引入额外依赖；已存在的环境变量优先，不被文件覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)


_load_dotenv(ENV_FILE)

DATA_DIR = os.environ.get("NL2SQL_DATA_DIR") or os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

STORE_PATH = os.path.join(DATA_DIR, "platform.db")


def _first(*names):
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


# 任意 OpenAI 兼容端点：通义千问兼容模式 / DeepSeek / vLLM / Ollama 均可
LLM_BASE_URL = _first("LLM_BASE_URL", "OPENAI_BASE_URL").rstrip("/")

_api_key = _first("LLM_API_KEY", "OPENAI_API_KEY")
if not _api_key and "minimax" in LLM_BASE_URL.lower():
    _api_key = _first("MINIMAX_API_KEY")
LLM_API_KEY = _api_key

LLM_MODEL = _first("LLM_MODEL", "OPENAI_MODEL")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))

MAX_ROWS = int(os.environ.get("NL2SQL_MAX_ROWS", "200"))
# 导出上限：页面展示只给 MAX_ROWS 行，导出 CSV 可以拿全表；这个值防止把百万行一次性拉走
EXPORT_MAX_ROWS = int(os.environ.get("NL2SQL_EXPORT_MAX_ROWS", "200000"))

# 历史注入预算：近期逐轮给全的轮数 + 状态卡/相关早期轮次的字符与条数上限
CONTEXT_RECENT = int(os.environ.get("NL2SQL_CONTEXT_RECENT", "4"))
MEMORY_STATE_CHARS = int(os.environ.get("NL2SQL_MEMORY_STATE_CHARS", "1200"))
MEMORY_OLD_LIMIT = int(os.environ.get("NL2SQL_MEMORY_OLD_LIMIT", "6"))
MEMORY_TOPICS = int(os.environ.get("NL2SQL_MEMORY_TOPICS", "12"))

# 模式召回：auto=小库全量、大库按相关度取 TOP_K；all=强制全量；recall=永远只取 TOP_K
SCHEMA_MODE = (os.environ.get("NL2SQL_SCHEMA_MODE") or "auto").strip().lower()
TOP_K = int(os.environ.get("NL2SQL_TOP_K", "8"))
FULL_SCHEMA_MAX = int(os.environ.get("NL2SQL_FULL_SCHEMA_MAX", "1000"))
MAX_SCHEMA_TABLES = int(os.environ.get("NL2SQL_MAX_SCHEMA_TABLES", "1000"))
# 只做提示用：DDL 超过这个字符数时在轨迹里提醒“模型上下文可能不够”，不会截断
SCHEMA_WARN_CHARS = int(os.environ.get("NL2SQL_SCHEMA_WARN_CHARS", "40000"))


def llm_missing():
    """返回缺失的配置项名称列表，用于启动提示和健康检查。"""
    missing = []
    if not LLM_BASE_URL:
        missing.append("LLM_BASE_URL")
    if not LLM_API_KEY:
        missing.append("LLM_API_KEY")
    if not LLM_MODEL:
        missing.append("LLM_MODEL")
    return missing

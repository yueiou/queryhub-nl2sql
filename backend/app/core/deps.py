"""共享单例：元数据仓储、模型客户端、引擎缓存。"""
from ..db.engine import EngineRegistry
from ..db.metadata import MetadataStore
from ..llm.client import LLMClient
from . import config

store = MetadataStore(config.STORE_PATH)
llm = LLMClient(config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL, config.LLM_TIMEOUT)
engines = EngineRegistry()

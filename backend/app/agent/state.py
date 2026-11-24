"""问数流程的状态对象与节点轨迹。"""
from dataclasses import asdict, dataclass, field


def add_step(trace, node, status, detail, ms=0):
    trace.append({"node": node, "status": status, "detail": detail, "ms": ms})


def _empty_chart():
    return {"type": "table", "x": None, "y": []}


@dataclass
class QueryState:
    question: str
    answer_type: str = "sql"
    sql: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart: dict = field(default_factory=_empty_chart)
    summary: str = ""
    analysis: str = ""
    used_tables: list = field(default_factory=list)
    metrics_used: list = field(default_factory=list)
    limit_applied: bool = False
    trace: list = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""
    log_id: str = ""
    context_used: bool = False
    context_turns: int = 0

    def apply_result(self, data):
        self.columns = data["columns"]
        self.rows = data["rows"]
        self.row_count = data["row_count"]
        self.truncated = data["truncated"]

    def to_dict(self):
        return asdict(self)

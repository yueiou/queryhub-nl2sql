"""模型客户端：OpenAI 兼容协议，返回内容并解析出 SQL。"""
import json
import re
import time

import httpx


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url, api_key, model, timeout=60):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or "qwen-plus"
        self.timeout = timeout

    @property
    def available(self):
        return bool(self.base_url and self.api_key and self.model)

    def _post(self, messages, temperature=0, max_tokens=1000, timeout=None):
        if not self.available:
            raise LLMError("未配置齐全：需要 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 三项")
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=timeout or self.timeout)
        except Exception as e:
            raise LLMError(f"连不上 {self.base_url}：{e}")
        if resp.status_code >= 400:
            hint = ""
            if resp.status_code == 401:
                hint = "（Key 无效或未生效）"
            elif resp.status_code == 404:
                hint = f"（端点或模型名不对：model={self.model}）"
            raise LLMError(f"模型返回 {resp.status_code}{hint}：{resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise LLMError(f"响应不是标准的 chat/completions 格式：{e}")

    def chat(self, messages, temperature=0, max_tokens=1000):
        return self._post(messages, temperature, max_tokens)

    def probe(self, timeout=15):
        """发一次最小请求，真的验证连通性，而不是只看配置是否非空。"""
        t0 = time.perf_counter()
        try:
            content = self._post(
                [{"role": "user", "content": "只回复 ok"}],
                temperature=0, max_tokens=8, timeout=timeout,
            )
            return {"ok": True, "model": self.model, "base_url": self.base_url,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "sample": content[:50]}
        except LLMError as e:
            return {"ok": False, "model": self.model, "base_url": self.base_url,
                    "latency_ms": int((time.perf_counter() - t0) * 1000), "error": str(e)}


def extract_sql(text):
    """从模型回复中取 SQL 与说明，兼容 JSON / 代码块 / 裸 SQL 三种返回。"""
    if not text:
        return "", ""
    raw = text.strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and obj.get("sql"):
                return str(obj["sql"]).strip(), str(obj.get("analysis") or "")
        except Exception:
            pass
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", raw, re.I)
    if m:
        return m.group(1).strip(), ""
    m = re.search(r"((?:WITH|SELECT)[\s\S]*)", raw, re.I)
    if m:
        return m.group(1).strip(), raw[:m.start()].strip()
    return raw, ""

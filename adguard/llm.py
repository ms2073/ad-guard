"""The one place that talks to OpenGateway (/chat/completions, strict json_schema)."""
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).parent.parent
BASE_URL = "https://apis.opengateway.ai/v1"
MODEL = os.environ.get("ADGUARD_MODEL", "z-ai/glm-5.3-flash-ultrafast")
PROMPTS = Path(__file__).parent / "prompts"
RULES = ROOT / "rules.md"

T = TypeVar("T", bound=BaseModel)
_lock = threading.Lock()
usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}


class ModelFailure(Exception):
    """No usable answer. Callers treat it as 'needs a human', never as 'pass'."""

    def __init__(self, kind: Literal["refused", "api_error", "invalid_output"], detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def _api_key() -> str:
    key = os.environ.get("OPENGATEWAY_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENGATEWAY_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise RuntimeError("Set OPENGATEWAY_API_KEY (env var or .env in the project root)")
    return key


def _post(path: str, body: dict) -> dict:
    headers = {"Authorization": f"Bearer {_api_key()}"}
    for attempt in range(3):
        try:
            r = httpx.post(f"{BASE_URL}{path}", json=body, headers=headers, timeout=120)
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


def _strict(schema: dict) -> dict:
    """Inline $refs and make every object strict, so the provider's JSON-schema mode accepts it."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            props = {name: walk(v) for name, v in node.get("properties", {}).items()}  # field names kept as-is
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default", "properties")}
            if props:
                node["properties"] = props
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            return node
        if isinstance(node, list):
            return [walk(n) for n in node]
        return node

    return walk(schema)


def load_prompt(path: Path) -> str:
    return path.read_text().replace("{{RULES}}", RULES.read_text())


def call(system: str, user: str, schema: type[T], check: Callable[[T], str | None] = lambda _: None) -> T:
    """One retry if the answer doesn't parse or `check` finds a problem with it; then ModelFailure."""
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": _strict(schema.model_json_schema())}},
    }
    problem = ""
    for _ in range(2):
        try:
            data = _post("/chat/completions", body)
        except (httpx.HTTPError, RuntimeError) as e:
            raise ModelFailure("api_error", str(e)[:300]) from e
        try:
            choice = data["choices"][0]
            u = data.get("usage") or {}
            with _lock:
                usage["calls"] += 1
                usage["input_tokens"] += u.get("prompt_tokens", 0)
                usage["output_tokens"] += u.get("completion_tokens", 0)
            if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
                raise ModelFailure("refused", str(choice["message"].get("refusal") or "content_filter")[:300])
            out = schema.model_validate(json.loads(choice["message"]["content"] or ""))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as e:
            problem = f"unparseable {schema.__name__}: {str(e)[:200]}"
            continue
        if problem := check(out) or "":
            continue
        return out
    raise ModelFailure("invalid_output", f"{MODEL} twice: {problem}")

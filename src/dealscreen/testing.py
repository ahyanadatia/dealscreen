"""Offline harness for the Anthropic Messages API.

`ScriptedAnthropic` plugs into the *real* `anthropic` SDK through an httpx mock transport, so the
SDK's own request serialisation and response parsing run unchanged. Each request is checked
against the Messages API contract this project depends on (role alternation, every `tool_use`
answered by a matching `tool_result`, well-formed tool definitions) before a scripted response is
returned. Used by the test suite and by `dealscreen demo`.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import anthropic
import httpx2 as httpx  # the anthropic SDK's own HTTP stack


class ContractError(AssertionError):
    """A request violated the Messages API contract."""


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def tool_use_block(tool_id: str, name: str, input_: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": input_}


def message(
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "tool_use",
    model: str = "claude-sonnet-4-5",
    msg_id: str = "msg_scripted",
    input_tokens: int = 1200,
    output_tokens: int = 300,
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def check_request(body: dict[str, Any]) -> None:
    for key in ("model", "max_tokens", "messages"):
        if key not in body:
            raise ContractError(f"missing '{key}'")
    for tool in body.get("tools", []):
        if "type" in tool and tool["type"] != "custom":
            continue  # server tool, e.g. web_search_20250305
        if not tool.get("name") or not isinstance(tool.get("input_schema"), dict):
            raise ContractError(f"bad tool definition: {tool}")
        if tool["input_schema"].get("type") != "object":
            raise ContractError(f"tool {tool['name']} input_schema must be an object schema")
        if "$ref" in json.dumps(tool["input_schema"]):
            raise ContractError(f"tool {tool['name']} schema has unresolved $ref")
    msgs = body["messages"]
    if not msgs or msgs[0]["role"] != "user":
        raise ContractError("first message must be from the user")
    for prev, cur in itertools.pairwise(msgs):
        if prev["role"] == cur["role"]:
            raise ContractError("roles must alternate")
        if prev["role"] == "assistant" and isinstance(prev["content"], list):
            wanted = {b["id"] for b in prev["content"] if b.get("type") == "tool_use"}
            got: set[str] = set()
            if isinstance(cur["content"], list):
                got = {b["tool_use_id"] for b in cur["content"] if b.get("type") == "tool_result"}
            if wanted != got:
                raise ContractError(f"tool_use ids {wanted} not matched by tool_results {got}")
    choice = body.get("tool_choice")
    if choice and choice.get("type") == "tool":
        names = {t.get("name") for t in body.get("tools", [])}
        if choice.get("name") not in names:
            raise ContractError("tool_choice names an undefined tool")


class ScriptedAnthropic:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/messages":
            return httpx.Response(
                404,
                json={
                    "type": "error",
                    "error": {"type": "not_found_error", "message": "unexpected path"},
                },
            )
        body = json.loads(request.content)
        check_request(body)
        self.requests.append(body)
        self.headers.append(request.headers)
        if not self.responses:
            raise ContractError("no scripted responses left")
        return httpx.Response(200, json=self.responses.pop(0))

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="sk-ant-test-not-a-real-key",
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            max_retries=0,
        )

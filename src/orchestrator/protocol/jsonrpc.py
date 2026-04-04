"""JSON-RPC 2.0 message types for Codex app-server protocol."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

_id_counter = itertools.count(1)


def next_id() -> int:
    return next(_id_counter)


@dataclass
class JSONRPCError:
    code: int
    message: str
    data: Any = None


@dataclass
class JSONRPCRequest:
    method: str
    params: dict[str, Any]
    id: int | str = field(default_factory=next_id)

    def to_json(self) -> str:
        return json.dumps(
            {"jsonrpc": "2.0", "id": self.id, "method": self.method, "params": self.params}
        )


@dataclass
class JSONRPCResponse:
    id: int | str
    result: dict[str, Any] | None = None
    error: JSONRPCError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class JSONRPCNotification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"jsonrpc": "2.0", "method": self.method, "params": self.params})


def make_request(method: str, params: dict[str, Any] | None = None) -> JSONRPCRequest:
    return JSONRPCRequest(method=method, params=params or {})


def make_notification(method: str, params: dict[str, Any] | None = None) -> JSONRPCNotification:
    return JSONRPCNotification(method=method, params=params or {})


def parse_message(
    data: str | dict,
) -> JSONRPCRequest | JSONRPCResponse | JSONRPCNotification:
    if isinstance(data, str):
        msg = json.loads(data)
    else:
        msg = data

    has_id = "id" in msg
    has_method = "method" in msg
    has_result = "result" in msg
    has_error = "error" in msg

    # Response
    if has_id and (has_result or has_error):
        error = None
        if has_error:
            err = msg["error"]
            error = JSONRPCError(
                code=err.get("code", -1),
                message=err.get("message", "Unknown error"),
                data=err.get("data"),
            )
        return JSONRPCResponse(id=msg["id"], result=msg.get("result"), error=error)

    # Request (has id + method)
    if has_id and has_method:
        return JSONRPCRequest(
            id=msg["id"], method=msg["method"], params=msg.get("params", {})
        )

    # Notification (method only, no id)
    if has_method:
        return JSONRPCNotification(method=msg["method"], params=msg.get("params", {}))

    raise ValueError(f"Cannot parse JSON-RPC message: {msg}")

# -*- coding: utf-8 -*-
"""echo —— 仓库第一个登记的 MCP stdio server（纯标准库，无任何第三方依赖）。

**为什么存在**：`config/mcp.json` 的 `echo` 条目与 `config/agents_roster.yaml`
的 `mcp_echo`（MCP 回声工）都把 `args[0]` 指向本文件，`agent-runtime/test-mcp.ts`
的「echo 的 args 指向真实脚本且文件存在」断言（M4 复审 MINOR-1 补的盲区守卫）
因此长期红。语义与测试侧夹具 `agent-runtime/fixtures/mcp-echo-server.mjs` 对齐
（2026-10-30 补齐当日核对）：同样两个工具，`echo` 带 `readOnlyHint: true`，
`mutate` 带 `readOnlyHint: false`——后者的写副作用由 durable 的确认链拦截
（`mcp.json` 的 `approval: "mutating"` 就是这条闸门的登记处）。

**为什么是 Python**：`mcp.json` 的 `command` 走 PATH 解析（本机落到 Anaconda
解释器），roster 注释写明了口径——echo_server 必须纯标准库，两种解释器都跑得动。
将来给依赖第三方包的 server 时，这条口径要重新审视。

协议面（JSON-RPC 2.0 over stdio，一行一条）：
- `initialize` → 回 protocolVersion / capabilities.tools / serverInfo
- `ping` → 空结果
- `tools/list` → 上面两个工具
- `tools/call` → `echo:<text>` 或 `mutated`；未知工具回 -32602
- 通知（无 id，如 `notifications/initialized`）→ 静默吞掉，绝不回包
- 其他带 id 的方法 → -32601

坏 JSON 行同样静默跳过：MCP 客户端与日志可能共用管道，服务端无权制造噪声。
"""
from __future__ import annotations

import json
import sys

PROTOCOL_FALLBACK = "2025-11-25"

TOOLS = [
    {
        "name": "echo",
        "description": "Echo a string",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "mutate",
        "description": "A mutating tool",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False},
    },
]


def _reply(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(request_id, result: dict) -> None:
    _reply({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id, code: int, message: str) -> None:
    _reply({"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}})


def _handle(message: dict) -> None:
    request_id = message.get("id")
    if request_id is None:
        return  # 通知：无 id 不回包，回了就是协议违规
    method = message.get("method", "")
    params = message.get("params") or {}
    if method == "initialize":
        protocol = params.get("protocolVersion") or PROTOCOL_FALLBACK
        _result(request_id, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo_server", "version": "1.0.0"},
        })
        return
    if method == "ping":
        _result(request_id, {})
        return
    if method == "tools/list":
        _result(request_id, {"tools": TOOLS})
        return
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            _result(request_id, {"content": [
                {"type": "text", "text": f"echo:{str(args.get('text', ''))}"}]})
            return
        if name == "mutate":
            _result(request_id, {"content": [{"type": "text", "text": "mutated"}],
                                 "isError": False})
            return
        _error(request_id, -32602, f"unknown tool {name}")
        return
    _error(request_id, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        _handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

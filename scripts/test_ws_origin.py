"""`/ws` 握手同源校验的离线回归：python scripts/test_ws_origin.py

沿用 test_voice_turns 的 AST 隔离装载（load_ws / FakeSocket）：执行的是
server/app.py 里真实的 websocket_endpoint，只把外部依赖换成假实现，不 bootstrap 真 app。

规则（架构审查 P1 后定稿）：只比 netloc，不比 scheme（TLS 终止反代下 scope 是 ws、
Origin 是 https）；畸形 Origin 按拒绝处理、不能抛 500；无 Origin 或空串按白名单放行。

覆盖：
  - 跨源页面（Origin 的 netloc 与 Host 不同）：accept 之前 close(1008)，且写一条可读日志；
  - 同源页面：放行；https Origin + TLS 终止反代（scope=ws）：放行；
  - Origin 为 "null" 或畸形（如 "http://["）：拒绝；
  - 无 Origin 头 / Origin 空串（本地脚本、直连测试）：白名单放行，不打断现有客户端。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


class OriginSocket(FakeSocket):
    """在 FakeSocket 上补握手关心的东西：headers / url / accept 与 close 记录。"""

    def __init__(self, *, origin: str | None = None, host: str = "testserver",
                 url_scheme: str = "ws") -> None:
        super().__init__()
        self.headers: dict[str, str] = {"host": host}
        if origin is not None:
            self.headers["origin"] = origin
        self.url = SimpleNamespace(scheme=url_scheme)
        self.accepted = False
        self.close_frames: list[tuple[int, str | None]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_frames.append((code, reason))


class WebSocketOriginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.env = load_ws()
        # app.py 的 WS 接入注册表：隔离环境没有真 app，补一个空集合即可。
        self.env["_connections"] = set()

    def endpoint(self, **kwargs):
        ws = OriginSocket(**kwargs)
        task = asyncio.create_task(self.env["websocket_endpoint"](ws))
        return ws, task

    async def assert_accepted(self, ws: OriginSocket, task: asyncio.Task) -> None:
        for _ in range(100):
            if ws.accepted:
                break
            await asyncio.sleep(0)
        self.assertTrue(ws.accepted, "同源/无 Origin 的连接应被接受")
        ws.inbox.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 2)

    async def assert_rejected(self, ws: OriginSocket, task: asyncio.Task,
                              expected_origin: str) -> None:
        with self.assertLogs("server.app", level="WARNING") as logs:
            await asyncio.wait_for(task, 2)
        self.assertFalse(ws.accepted, "跨源连接必须在 accept 之前被拒")
        self.assertTrue(ws.close_frames, "必须发关闭帧")
        code, reason = ws.close_frames[-1]
        self.assertEqual(code, 1008)
        self.assertEqual(reason, "cross_origin_denied")
        self.assertIn("websocket origin rejected", logs.output[0])
        self.assertIn(expected_origin, logs.output[0], "日志要带上被拒的来源，便于排查")

    async def test_cross_origin_page_rejected_before_accept(self) -> None:
        ws, task = self.endpoint(origin="http://evil.example", host="127.0.0.1:8000")
        await self.assert_rejected(ws, task, "evil.example")

    async def test_same_site_but_different_port_rejected(self) -> None:
        # vite 代理场景是 5188 -> 8000，但 Host 必须保持一致（changeOrigin:false）；
        # 若页面 Origin 与 Host 端口不一致，一律按跨源拒。
        ws, task = self.endpoint(origin="http://127.0.0.1:5173", host="127.0.0.1:8000")
        await self.assert_rejected(ws, task, "127.0.0.1:5173")

    async def test_host_case_insensitive_same_origin_accepted(self) -> None:
        # Host 与 Origin 的 netloc 统一小写比较（HOST 语法本身不区分大小写）。
        ws, task = self.endpoint(origin="http://TestServer", host="testserver")
        await self.assert_accepted(ws, task)

    async def test_tls_terminated_reverse_proxy_origin_accepted(self) -> None:
        # TLS 在反代终止：scope 里是 ws，浏览器 Origin 是 https —— 只比 netloc 所以放行。
        ws, task = self.endpoint(origin="https://testserver", host="testserver", url_scheme="ws")
        await self.assert_accepted(ws, task)

    async def test_same_origin_page_accepted(self) -> None:
        ws, task = self.endpoint(origin="http://testserver", host="testserver")
        await self.assert_accepted(ws, task)

    async def test_vite_proxy_style_same_origin_accepted(self) -> None:
        # 复刻 pixel-world 的链路：浏览器在 5188，vite 原样转发 Host 与 Origin。
        ws, task = self.endpoint(origin="http://127.0.0.1:5188", host="127.0.0.1:5188")
        await self.assert_accepted(ws, task)

    async def test_https_page_on_wss_accepted(self) -> None:
        ws, task = self.endpoint(origin="https://testserver", host="testserver", url_scheme="wss")
        await self.assert_accepted(ws, task)

    async def test_null_origin_rejected(self) -> None:
        # sandboxed iframe / file:// 页面会发 Origin: null，netloc 为空，按跨源拒。
        ws, task = self.endpoint(origin="null", host="testserver")
        await self.assert_rejected(ws, task, "null")

    async def test_malformed_origin_rejected_without_crashing(self) -> None:
        # urlsplit("http://[") 会抛 ValueError；必须被吞掉并按拒绝处理，不能变成 500。
        ws, task = self.endpoint(origin="http://[", host="testserver")
        await self.assert_rejected(ws, task, "http://[")

    async def test_empty_origin_treated_as_missing(self) -> None:
        # 空串 Origin 视作"没有 Origin"：按白名单放行，不当成跨源。
        ws, task = self.endpoint(origin="", host="testserver")
        await self.assert_accepted(ws, task)

    async def test_non_browser_client_without_origin_accepted(self) -> None:
        ws, task = self.endpoint(host="127.0.0.1:8000")
        await self.assert_accepted(ws, task)


# ---------------------------------------------------------------- live 探测（QA 复验用）


def _probe(url: str, origin: str | None) -> dict:
    """对运行中的实例做一次握手探测，返回 {opened, frame|status|close_code|error}。"""
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    from websockets.asyncio.client import connect

    async def run() -> dict:
        kwargs: dict = {"open_timeout": 5}
        if origin is not None:
            kwargs["origin"] = origin
        try:
            async with connect(url, **kwargs) as ws:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=5)
                    return {"opened": True, "frame": frame}
                except ConnectionClosed as closed:
                    return {"opened": True, "close_code": closed.code}
        except InvalidStatus as exc:
            return {"opened": False, "status": exc.response.status_code}
        except Exception as exc:  # noqa: BLE001 - 探测脚本把异常当结果
            return {"opened": False, "error": repr(exc)}

    return asyncio.run(run())


def live_check(url: str) -> int:
    """对一个已启动的实例验证握手策略。

    用法：python scripts/test_ws_origin.py --live ws://127.0.0.1:8001/ws

    验收口径（架构审查 P1 后）：同源 ready / 跨源 403 / 无 Origin ready /
    Origin:null 403 / 畸形 Origin 403。
    """
    from urllib.parse import urlsplit

    def rejected(result: dict) -> bool:
        return ((result.get("opened") is False and result.get("status") == 403)
                or (result.get("opened") is True and result.get("close_code") == 1008))

    def ready(result: dict) -> bool:
        return result.get("opened") is True and "ready" in str(result.get("frame"))

    parsed = urlsplit(url)
    page_origin = f"{'https' if parsed.scheme == 'wss' else 'http'}://{parsed.netloc}"
    cases = [
        ("同源页面握手放行并收到 ready", _probe(url, page_origin), ready),
        ("跨源页面握手被拒（403 或 close 1008）", _probe(url, "http://evil.example"), rejected),
        ("无 Origin 的本地客户端放行", _probe(url, None), ready),
        ("Origin:null 被拒", _probe(url, "null"), rejected),
        ("畸形 Origin（http://[）被拒且不报 500", _probe(url, "http://["), rejected),
    ]
    passed = 0
    for label, result, ok in cases:
        if ok(result):
            passed += 1
            print(f"  ok   {label}")
        else:
            print(f"  FAIL {label} -> {result}")
    print(f"\n=== {passed}/{len(cases)} live 探测通过（{url}）===")
    return 0 if passed == len(cases) else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="离线单测；加 --live 可对运行中实例做握手探测")
    parser.add_argument("--live", metavar="WS_URL", help="例如 ws://127.0.0.1:8001/ws")
    args, rest = parser.parse_known_args()
    if args.live:
        return live_check(args.live)
    unittest.main(argv=[sys.argv[0], *rest])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

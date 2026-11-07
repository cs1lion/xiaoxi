"""WebSocket 握手同源校验的离线回归：python scripts/test_ws_origin.py

沿用 test_voice_turns 的 AST 隔离装载（load_ws / FakeSocket）：执行的是
server/app.py 里真实的端点函数（判据 _ws_handshake_allowed + websocket_endpoint），
只把外部依赖换成假实现，不 bootstrap 真 app。

规则（架构审查 P1 后定稿）：只比 netloc，不比 scheme（TLS 终止反代下 scope 是 ws、
Origin 是 https）；畸形 Origin 按拒绝处理、不能抛 500；无 Origin 或空串按白名单放行。

覆盖：
  - /ws 全量行为矩阵（跨源/同源/端口不符/null/畸形/无 Origin/TLS 反代）；
  - B-1（2026-10-31 评审）：另外三条端点（/ws/proactive、/ws/interpret、
    /ws/interpret-stream）共用同一判据——跨源在产生任何副作用之前 close(1008)；
    proactive 的"副作用"用 store.start_session 被调计数钉住（改前它 accept 即调用，
    跨源连接也会落进会话存储），interpret 两条的守卫在 serve_* 的懒导入之前，
    隔离环境里以 ImportError 证明"放行后才走到函数体"。

无 Origin 头 / Origin 空串（本地脚本、直连测试）一律白名单放行，不打断现有客户端。
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
                 url_scheme: str = "ws", url_path: str = "/ws") -> None:
        super().__init__()
        self.headers: dict[str, str] = {"host": host}
        if origin is not None:
            self.headers["origin"] = origin
        self.url = SimpleNamespace(scheme=url_scheme, path=url_path)
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

    # ---- B-3（2026-11-05）：无 Origin 不再是直通门，改按 Host 白名单 ----

    async def test_no_origin_unlisted_host_rejected(self) -> None:
        """DNS rebinding 残余路径：无 Origin + 非本机 Host 必须在 accept 前拒。"""
        ws, task = self.endpoint(host="evil.example:8000")
        with self.assertLogs("server.app", level="WARNING") as logs:
            await asyncio.wait_for(task, 2)
        self.assertFalse(ws.accepted, "未登记的 Host 不能在无 Origin 时直通")
        self.assertTrue(ws.close_frames, "必须发关闭帧")
        code, reason = ws.close_frames[-1]
        self.assertEqual(code, 1008)
        self.assertEqual(reason, "host_not_allowed")
        self.assertIn("evil.example", logs.output[0], "日志要带上被拒的 Host")

    async def test_no_origin_host_allowlist_opt_in(self) -> None:
        """生产额外域名走 WS_ALLOWED_HOSTS 显式放行，不必改代码。"""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"WS_ALLOWED_HOSTS": "evil.example, other.internal"}):
            ws, task = self.endpoint(host="evil.example:8000")
            await self.assert_accepted(ws, task)


class SharedGuardEndpointTests(unittest.IsolatedAsyncioTestCase):
    """B-1（2026-10-31 评审）：另外三条 WS 端点共用 _ws_handshake_allowed 的回归。

    /ws/proactive 改前完全没有握手校验：跨源页面能直接握上手、accept 后就调
    store.start_session（会话存储落库），再靠 hello/away_seconds 操纵 idle 判定。
    两个 interpret 端点的 accept 在 serve_* 函数体内部，所以守卫必须挡在懒导入
    之前——用"放行路径死在隔离环境的相对导入上、拒绝路径零 close 之外副作用"来钉。
    """

    async def asyncSetUp(self) -> None:
        self.env = load_ws()
        self.env["_connections"] = set()

    def _store_spy(self):
        calls: list = []

        class S:
            def start_session(self, session_id):
                calls.append(session_id)

            def end_session(self, session_id):
                calls.append(("end", session_id))

        return S(), calls

    async def assert_cross_origin_refused(self, endpoint: str, path: str) -> None:
        ws = OriginSocket(origin="http://evil.example", host="127.0.0.1:8000",
                          url_path=path)
        store, calls = self._store_spy()
        self.env["store"] = store
        task = asyncio.create_task(self.env[endpoint](ws))
        with self.assertLogs("server.app", level="WARNING") as logs:
            await asyncio.wait_for(task, 2)
        self.assertFalse(ws.accepted, f"{path}：跨源连接必须在 accept 之前被拒")
        self.assertEqual(ws.close_frames, [(1008, "cross_origin_denied")],
                         f"{path}：必须恰好发一条 1008 关闭帧")
        self.assertEqual(calls, [],
                         f"{path}：拒绝必须发生在会话存储副作用之前")
        self.assertIn("websocket origin rejected", logs.output[0])
        self.assertIn(path, logs.output[0], "日志要带上被拒的端点路径，便于排查")

    async def test_proactive_socket_cross_origin_refused(self) -> None:
        await self.assert_cross_origin_refused("proactive_socket", "/ws/proactive")

    async def test_interpret_socket_cross_origin_refused(self) -> None:
        await self.assert_cross_origin_refused("interpretation_socket",
                                               "/ws/interpret")

    async def test_interpret_stream_socket_cross_origin_refused(self) -> None:
        await self.assert_cross_origin_refused("stream_interpretation_socket",
                                               "/ws/interpret-stream")

    async def test_proactive_socket_no_origin_accepted_and_session_registered(self) -> None:
        # 白名单不破：本地脚本（无 Origin）仍能握手，且放行后照常注册会话。
        ws = OriginSocket(host="127.0.0.1:8000", url_path="/ws/proactive")
        store, calls = self._store_spy()
        self.env["store"] = store
        task = asyncio.create_task(self.env["proactive_socket"](ws))
        for _ in range(100):
            if ws.accepted:
                break
            await asyncio.sleep(0)
        self.assertTrue(ws.accepted, "无 Origin 的本地客户端应被接受")
        ws.inbox.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 2)
        self.assertEqual(len(calls), 2, f"start/end 各一次：{calls!r}")
        self.assertEqual(calls[1], ("end", calls[0]))


class GuardPlacementTests(unittest.IsolatedAsyncioTestCase):
    """守卫必须在 serve_* 之前：放行路径进函数体、拒绝路径不进。"""

    async def asyncSetUp(self) -> None:
        self.env = load_ws()
        self.env["_connections"] = set()

    async def run_to_body(self, endpoint: str) -> tuple[OriginSocket, BaseException | None]:
        # 隔离装载没有包上下文，serve_* 的相对懒导入必失败（ImportError，或在
        # globals 缺 __name__ 时 KeyError）——失败点本身即"守卫已跨过、函数体
        # 在执行"的证据；守卫若误拒，任务会干净返回并带 1008 关闭帧。
        ws = OriginSocket(origin="http://127.0.0.1:5173", host="127.0.0.1:5173",
                          url_path="/ws/interpret")
        task = asyncio.create_task(self.env[endpoint](ws))
        err: BaseException | None = None
        try:
            await asyncio.wait_for(task, 2)
        except (ImportError, KeyError) as exc:
            err = exc
        return ws, err

    async def test_interpret_guard_admits_same_origin_before_lazy_import(self) -> None:
        ws, err = await self.run_to_body("interpretation_socket")
        self.assertIsInstance(err, (ImportError, KeyError),
                              "同源放行必须走到 serve_interpretation 的导入")
        self.assertEqual(ws.close_frames, [], "放行路径不得有任何关闭帧")

    async def test_interpret_stream_guard_admits_same_origin(self) -> None:
        ws, err = await self.run_to_body("stream_interpretation_socket")
        self.assertIsInstance(err, (ImportError, KeyError))
        self.assertEqual(ws.close_frames, [])

    async def test_interpret_guard_refuses_cross_origin_before_lazy_import(self) -> None:
        ws = OriginSocket(origin="http://evil.example", host="127.0.0.1:8000",
                          url_path="/ws/interpret")
        task = asyncio.create_task(self.env["interpretation_socket"](ws))
        with self.assertLogs("server.app", level="WARNING"):
            await asyncio.wait_for(task, 2)
        # 拒在导入之前：serve_interpretation 从未被尝试装载。
        self.assertEqual(ws.close_frames, [(1008, "cross_origin_denied")])


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

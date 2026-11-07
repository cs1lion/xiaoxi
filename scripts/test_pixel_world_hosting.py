"""Hosting contract: the built pixel world is mounted at /world on the same origin.

This test uses a temporary root and a minimal FastAPI app, so it does not import
the production app, touch data, or require a browser. The real server calls the
same `mount_pixel_world()` helper.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 下面 check_voice_companion_is_the_front_door 会导入生产 app；与 test_core.py
# 同款隔离，避免导入链碰到真实记忆库。
os.environ["DB_PATH"] = ":memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.static import mount_pixel_world, pixel_world_index, world_entry_response  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pixel-world-hosting-") as tmp:
        root = Path(tmp)
        dist = root / "pixel-world" / "dist"
        assets = dist / "assets"
        assets.mkdir(parents=True)
        (dist / "index.html").write_text(
            '<!doctype html><script src="/world/assets/app.js"></script>',
            encoding="utf-8",
        )
        (assets / "app.js").write_text("console.log('world');", encoding="utf-8")

        app = FastAPI()
        check("构建产物存在时挂载 /world", mount_pixel_world(app, root) is True)
        client = TestClient(app)
        index = client.get("/world/")
        check("GET /world/ 返回页面", index.status_code == 200, str(index.status_code))
        check("页面从 /world/ 提供", "world/assets/app.js" in index.text)
        asset = client.get("/world/assets/app.js")
        check("静态资源从 /world/assets 提供", asset.status_code == 200, str(asset.status_code))
        check("静态资源内容正确", asset.text == "console.log('world');")

        empty_root = root / "no-build"
        empty_app = FastAPI()
        check(
            "没有 dist 时不挂载（测试/开发不炸）",
            mount_pixel_world(empty_app, empty_root) is False,
        )
        missing = TestClient(empty_app).get("/world/")
        check("没有 dist 时 /world/ 不存在", missing.status_code == 404, str(missing.status_code))

        check_world_entry_payload(root, dist)
        check_voice_companion_is_the_front_door()

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


def check_world_entry_payload(root: Path, dist: Path) -> None:
    """世界产物仍是 `/world` 的可用载荷，但不再是产品前门。

    2026-11-05：原 check_world_is_the_front_door 守的是 2026-10-24 的入口反转，
    分家后 `/` 已改回语音陪伴页。这里**不再断言「/ 是世界页」**（那是过期决策），
    但保留对 mount / pixel_world_index / world_entry_response 真实行为的覆盖，
    避免挂载与产物路径回归。`/` 本身由下面那条驱动生产路由的测试验证。
    """
    check("世界产物路径仍被识别", pixel_world_index(root) == dist / "index.html")
    page = world_entry_response(root).body.decode("utf-8")
    check("世界载荷仍含 /world 资源", "world/assets/app.js" in page, page[:120])

    empty_root = root / "no-build"
    check("没有产物时不假装有世界", pixel_world_index(empty_root) is None)
    notice = world_entry_response(empty_root)
    check("没有产物时仍返回可读页面", notice.status_code == 200, str(notice.status_code))
    check("没有产物时给出构建命令", "build:world" in notice.body.decode("utf-8"))
    check("没有产物时指向 /chat", "/chat" in notice.body.decode("utf-8"))


def check_voice_companion_is_the_front_door() -> None:
    """真正驱动生产路由：`/` 与 `/chat` 都必须返回语音陪伴页。

    只改 helper 断言会退化成「为了让测试绿而改测试」。这里用 TestClient 打真实
    app：无论本机有没有 dist 产物，`/` 都得拿到 web/index.html（含 sidePanel），
    不能是世界页，也不能是「世界还没建好」说明页。分家后新 clone 无 dist，
    这条正是防止那个场景把产品前门变成一张报错说明页的钉子。
    """
    from fastapi.testclient import TestClient
    from server.app import app

    client = TestClient(app)
    for path in ("/", "/chat"):
        resp = client.get(path)
        check(f"GET {path} 返回 200", resp.status_code == 200, str(resp.status_code))
        check(f"GET {path} 是语音陪伴页", "sidePanel" in resp.text, resp.text[:120])
        check(f"GET {path} 不是世界说明页", "世界还没建好" not in resp.text)


if __name__ == "__main__":
    raise SystemExit(main())

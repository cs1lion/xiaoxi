"""Hosting contract: the built pixel world is mounted at /world on the same origin.

This test uses a temporary root and a minimal FastAPI app, so it does not import
the production app, touch data, or require a browser. The real server calls the
same `mount_pixel_world()` helper.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

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

        check_world_is_the_front_door(root, dist)

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


def check_world_is_the_front_door(root: Path, dist: Path) -> None:
    """`/` 的载荷就是世界的首页，不是聊天页。

    这一组断言守的是「入口反转」本身：路由挂错、产物路径写错、或者有人把 `/` 改
    回聊天页，都会在这里红，而不是等到用户打开产品才发现。
    """
    check("/ 的载荷来自世界产物", pixel_world_index(root) == dist / "index.html")
    page = world_entry_response(root).body.decode("utf-8")
    check("/ 返回世界页面", "world/assets/app.js" in page, page[:120])
    check("/ 不是聊天页", "sidePanel" not in page)

    empty_root = root / "no-build"
    check("没有产物时不假装有世界", pixel_world_index(empty_root) is None)
    notice = world_entry_response(empty_root)
    check("没有产物时仍返回可读页面", notice.status_code == 200, str(notice.status_code))
    check("没有产物时给出构建命令", "build:world" in notice.body.decode("utf-8"))
    check("没有产物时指向 /chat", "/chat" in notice.body.decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

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

from server.static import mount_pixel_world  # noqa: E402

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

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

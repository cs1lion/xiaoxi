from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


class AppStaticFiles(StaticFiles):
    """Refresh application code together while retaining large asset caching."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if Path(path).suffix.lower() in {".html", ".js", ".mjs", ".css"}:
            response.headers["Cache-Control"] = "no-store"
        return response


def mount_pixel_world(app: FastAPI, root: Path) -> bool:
    """Mount the built pixel world at /world when the artifact exists.

    The build must happen before the server starts (deployment builds
    `pixel-world/dist` with `npm run build:world`). Keeping the mount
    conditional means tests/dev without an artifact still import the app;
    production simply gets the route when dist is present.
    """
    dist = root / "pixel-world" / "dist"
    if not (dist / "index.html").is_file():
        return False
    app.mount("/world", AppStaticFiles(directory=str(dist), html=True), name="pixel-world")
    return True


def pixel_world_index(root: Path) -> Path | None:
    """The built world's entry document, or None when nothing was built.

    `/` serves this same file so the world is the product's front door. The page
    addresses its own assets with absolute `/world/...` URLs, so it only works
    when `mount_pixel_world` succeeded — the two are decided by one condition,
    which is why both ask this function instead of testing the path themselves.
    """
    index = root / "pixel-world" / "dist" / "index.html"
    return index if index.is_file() else None


WORLD_NOT_BUILT_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>世界还没建好</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0c0f0d;
color:#e8e2d2;font:14px/1.8 ui-monospace,monospace}main{max-width:38rem;padding:2rem}
code{color:#e3a13c}a{color:#e3a13c}</style></head>
<body><main>
<h1>世界还没建好</h1>
<p>像素世界的构建产物不在，页面无从加载。先构建，再重启服务。</p>
<p><code>npm run build:world</code>（在 <code>pixel-world/</code> 下）</p>
<p>旧的聊天界面仍然可用：<a href="/chat">/chat</a></p>
</main></body></html>
"""


def world_entry_response(root: Path) -> HTMLResponse:
    """`/` 的载荷：世界页面本身，或一张说明为什么没有它的静态页。

    缺产物时仍回 200：页面能正常渲染，说明里给了构建命令和 /chat 的出路。
    回 5xx 会让浏览器与反向代理把它当成故障页，人反而看不到那句该跑的构建命令。
    """
    index = pixel_world_index(root)
    if index is None:
        return HTMLResponse(WORLD_NOT_BUILT_PAGE, headers={"Cache-Control": "no-store"})
    return HTMLResponse(index.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})

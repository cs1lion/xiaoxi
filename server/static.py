from pathlib import Path

from fastapi import FastAPI
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

from pathlib import Path

from fastapi.staticfiles import StaticFiles


class AppStaticFiles(StaticFiles):
    """Refresh application code together while retaining large asset caching."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if Path(path).suffix.lower() in {".html", ".js", ".mjs", ".css"}:
            response.headers["Cache-Control"] = "no-store"
        return response

"""Static cache contract with synthetic files; no application bootstrap or live server."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


_SOURCE = Path(__file__).resolve().parents[1] / "server" / "static.py"
_SPEC = importlib.util.spec_from_file_location("_aqi_static_cache_fixture", _SOURCE)
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
AppStaticFiles = _MODULE.AppStaticFiles


class StaticCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="aqi-static-cache-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.resources = {
            "index.html": b"<!doctype html><title>Synthetic page</title>",
            "app.js": b"export const build = 'synthetic-old';",
            "module.mjs": b"export const fixture = true;",
            "style.css": b"body { color: #333; }",
            "avatar.vrm": b"synthetic-model" * 4096,
            "sample.wav": b"synthetic-audio" * 4096,
            "texture.png": b"synthetic-texture" * 4096,
        }
        for name, body in self.resources.items():
            (self.root / name).write_bytes(body)
        app = FastAPI()
        app.mount("/static", AppStaticFiles(directory=str(self.root)))
        app.mount("/baseline", StaticFiles(directory=str(self.root)))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_code_resources_get_and_head_disable_storage_without_altering_file_delivery(self):
        for name in ("index.html", "app.js", "module.mjs", "style.css"):
            for method in ("GET", "HEAD"):
                with self.subTest(name=name, method=method):
                    response = self.client.request(method, "/static/" + name)
                    baseline = self.client.request(method, "/baseline/" + name)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual(response.content, baseline.content)
                    self.assertEqual(response.content, self.resources[name] if method == "GET" else b"")
                    for header in ("etag", "last-modified", "content-type", "content-length"):
                        self.assertEqual(response.headers.get(header), baseline.headers.get(header))

    def test_version_query_preserves_code_delivery_and_no_store(self):
        response = self.client.get("/static/app.js?v=20261013-neutral-1")
        plain = self.client.get("/static/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.resources["app.js"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["etag"], plain.headers["etag"])

    def test_code_conditional_not_modified_responses_also_disable_storage(self):
        for name in ("index.html", "app.js", "module.mjs", "style.css"):
            first = self.client.get("/static/" + name)
            for condition, original in (("if-none-match", "etag"), ("if-modified-since", "last-modified")):
                for method in ("GET", "HEAD"):
                    with self.subTest(name=name, condition=condition, method=method):
                        response = self.client.request(method, "/static/" + name,
                                                       headers={condition: first.headers[original]})
                        self.assertEqual(response.status_code, 304)
                        self.assertEqual(response.content, b"")
                        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_large_assets_keep_default_cache_range_and_head_behavior(self):
        for name in ("avatar.vrm", "sample.wav", "texture.png"):
            for method, headers in (("GET", {}), ("HEAD", {}), ("GET", {"range": "bytes=8-23"})):
                with self.subTest(name=name, method=method, headers=headers):
                    response = self.client.request(method, "/static/" + name, headers=headers)
                    baseline = self.client.request(method, "/baseline/" + name, headers=headers)
                    self.assertEqual(response.status_code, baseline.status_code)
                    self.assertEqual(response.status_code, 206 if headers else 200)
                    self.assertEqual(response.content, baseline.content)
                    self.assertNotEqual(response.headers.get("cache-control"), "no-store")
                    for header in ("cache-control", "etag", "last-modified", "accept-ranges",
                                   "content-range", "content-type", "content-length"):
                        self.assertEqual(response.headers.get(header), baseline.headers.get(header))

    def test_large_asset_conditional_responses_keep_default_cache_behavior(self):
        for name in ("avatar.vrm", "sample.wav", "texture.png"):
            first = self.client.get("/static/" + name)
            for condition, original in (("if-none-match", "etag"), ("if-modified-since", "last-modified")):
                with self.subTest(name=name, condition=condition):
                    headers = {condition: first.headers[original]}
                    response = self.client.get("/static/" + name, headers=headers)
                    baseline = self.client.get("/baseline/" + name, headers=headers)
                    self.assertEqual(response.status_code, 304)
                    self.assertEqual(response.content, b"")
                    self.assertNotEqual(response.headers.get("cache-control"), "no-store")
                    self.assertEqual(dict(response.headers), dict(baseline.headers))

    def test_changed_code_revalidates_old_etag_to_current_bytes(self):
        first = self.client.get("/static/app.js")
        current = b"export const build = 'synthetic-new-with-a-different-size';"
        (self.root / "app.js").write_bytes(current)
        response = self.client.get("/static/app.js", headers={"if-none-match": first.headers["etag"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, current)
        self.assertNotEqual(response.headers["etag"], first.headers["etag"])
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()

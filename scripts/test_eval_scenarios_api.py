"""§诊断入口 /api/eval/scenarios 的离线契约：python scripts/test_eval_scenarios_api.py

端点此前读 `eval/scenarios.json`——该文件从未入库，所以恒返回空。
现行评测集的单一真相源是 `evals/cases/*.yaml`（`scripts/run_evals.py` 同源），
本测试钉住三件事：

  1. 目录里的每个用例都出现在返回里，id/name 非空且唯一；
  2. 字段形状与 ops 面板读取的字段对齐（name / user / checks）；
  3. 个别 YAML 坏掉时只进 invalid 清单，不拖垮整个目录，也不抛异常。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.eval_catalog import CASES_DIR, load_catalog  # noqa: E402


class EvalCatalogTests(unittest.TestCase):
    def test_every_case_file_is_listed_with_a_nonempty_unique_id(self):
        payload = load_catalog()
        files = sorted(CASES_DIR.glob("*.yaml"))
        self.assertGreater(len(files), 0, "evals/cases 为空，目录会真空绿")
        self.assertEqual(payload["count"], len(files))
        self.assertEqual(payload["invalid"], [])
        ids = [item["id"] for item in payload["items"]]
        self.assertEqual(len(ids), len(set(ids)), "场景 id 有重复")
        for item in payload["items"]:
            self.assertTrue(item["id"], item)
            self.assertTrue(item["name"], item)

    def test_item_shape_matches_ops_panel(self):
        payload = load_catalog()
        for item in payload["items"]:
            self.assertIsInstance(item["name"], str)
            self.assertIsInstance(item["user"], str)
            # 前端按 Object.keys(checks).length 计数，列表与字典都能数。
            self.assertIsInstance(item["checks"], list)
            self.assertIn(item["layer"], ("offline", "live", ""))
            self.assertIsInstance(item["tags"], list)

    def test_broken_yaml_is_reported_without_killing_the_catalog(self):
        with tempfile.TemporaryDirectory(prefix="eval-catalog-") as directory:
            root = Path(directory)
            (root / "good.yaml").write_text(
                "id: good\ntitle: 好用例\nlayer: offline\nturns:\n  - 你好\n"
                "checks:\n  - target: prompt\n    type: contains\n    value: x\n",
                encoding="utf-8")
            (root / "bad.yaml").write_text("id: [unclosed\n", encoding="utf-8")
            (root / "list.yaml").write_text("- not-a-mapping\n", encoding="utf-8")
            payload = load_catalog(root)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["id"], "good")
        self.assertEqual(payload["items"][0]["user"], "你好")
        self.assertEqual(sorted(payload["invalid"]), ["bad.yaml", "list.yaml"])

    def test_missing_directory_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory(prefix="eval-catalog-") as directory:
            payload = load_catalog(Path(directory) / "missing")
        self.assertEqual(payload, {"items": [], "count": 0, "invalid": []})


if __name__ == "__main__":
    unittest.main()

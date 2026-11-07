# -*- coding: utf-8 -*-
"""记忆召回改述测量 · 门禁回归（2026-11-06）。

守的是「这份标注集与测量器本身有效」，**不是召回率数字**：
不设任何 recall 地板线（那等于变相抬高 scripts/test_memory_retrieval_quality.py:125
的 RECALL_FLOOR），只验判别力、确定性、可归因性与四条硬约束。

Run: .venv/Scripts/python.exe -B scripts/test_memory_recall_paraphrase.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# issue #39：import 任何 server singleton 之前先隔离。
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""

import measure_memory_recall_paraphrase as measure_mod  # noqa: E402

SEED = ROOT / "evals" / "memory_recall_seed.tsv"
CASES = ROOT / "evals" / "memory_recall_paraphrase.tsv"
QUALITY = ROOT / "scripts" / "test_memory_retrieval_quality.py"


def code_only(source: str) -> str:
    """AST 去掉所有 docstring 后重建代码，供「静态契约」类检查使用。

    纯文本 `assertNotIn("data/memory.db", source)` 会误报：测量器的 docstring
    里那句「**严禁**从 data/memory.db 复制」本意是警告，不是引用。2026-11-06
    首跑实测被它判红两次，所以改成只看真正可执行的节点。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    node.body.pop(0)
                    if not node.body:
                        node.body.append(ast.Pass())
    return ast.unparse(tree)


def _temp_with(text: str, name: str, tmp: Path) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


class AnnotationTests(unittest.TestCase):
    def test_shape_and_bands(self) -> None:
        seeds = measure_mod.read_tsv(SEED, 2)
        cases = measure_mod.read_tsv(CASES, 4)
        self.assertEqual(len(seeds), 8, "种子记忆条数应与质量脚本 CORPUS 一致")
        keys = {parts[0] for _n, parts in seeds}
        bands: dict[str, int] = {}
        for _n, parts in cases:
            self.assertIn(parts[2], keys, f"标注集引用了不存在的 key：{parts[2]}")
            self.assertIn(parts[3], measure_mod.BANDS)
            bands[parts[3]] = bands.get(parts[3], 0) + 1
        for band in measure_mod.BANDS:
            self.assertGreaterEqual(bands.get(band, 0), 4, f"band {band} 样本过少")
        self.assertEqual(sum(bands.values()), 32)

    def test_seed_matches_quality_corpus(self) -> None:
        """docs/MEMORY_RESEARCH_NEXT.md:158：对照实验必须使用同一记忆集合。"""
        source = QUALITY.read_text(encoding="utf-8-sig")
        for _n, (key, value) in [(n, p) for n, p in measure_mod.read_tsv(SEED, 2)]:
            self.assertIn(key, source, f"种子 key 在质量脚本 CORPUS 里找不到：{key}")
            self.assertIn(value, source, f"种子 value 与质量脚本 CORPUS 漂移：{key}")

    def test_synthetic_only_declaration(self) -> None:
        """口径声明必须在数据文件里，防止后来者直接粘真实对话。"""
        for path in (SEED, CASES):
            head = path.read_text(encoding="utf-8-sig")
            self.assertIn("不含任何真实用户对话", head)


class MeasurementTests(unittest.TestCase):
    def test_literal_band_all_hits(self) -> None:
        """字面档全命中是**标注集合法性**检验，不是召回地板线。"""
        result = measure_mod.measure()
        self.assertEqual(result["bands"]["literal"]["recall"], 1.0)

    def test_deterministic(self) -> None:
        first = measure_mod.measure()
        second = measure_mod.measure()
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(first["bands"], second["bands"])

    def test_misses_are_attributable(self) -> None:
        """8/8 漏检应能归因到「零共享 2-gram」，这才是词法门的机制证据。"""
        result = measure_mod.measure()
        misses = [row for row in result["rows"] if not row["hit"]]
        self.assertGreater(len(misses), 0, "标注集恒绿——没有抓到任何漏检，等于没在测")
        for row in misses:
            self.assertIn("shared_bigrams", row)
            self.assertGreaterEqual(row["shared_bigrams"], 0)
        self.assertGreater(result["totals"]["zero_shared_miss"], 0,
                           "没有任何零共享漏检，机制归因链断了")

    def test_band_is_not_a_floor(self) -> None:
        """band 是构造手法标签，不是难度刻度；不许把数字写成单调结论。"""
        result = measure_mod.measure()
        recalls = [result["bands"][b]["recall"] for b in measure_mod.BANDS]
        self.assertEqual(len(recalls), len(measure_mod.BANDS))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in recalls))
        source = Path(measure_mod.__file__).read_text(encoding="utf-8-sig")
        self.assertIn("不是难度刻度", source, "必须把 band 口径写进输出，别让人误读")
        tree = ast.parse(code_only(source))
        floors = [node for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and "FLOOR" in target.id.upper()
                          for target in node.targets)]
        self.assertEqual(floors, [], "测量器不得定义任何地板线常量")


class DiscriminationTests(unittest.TestCase):
    def test_unknown_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="recall-ann-") as tmp:
            text = CASES.read_text(encoding="utf-8-sig")
            broken = _temp_with(text.replace("饮食:饮品偏好\tliteral",
                                             "不存在的键\tliteral"), "cases.tsv", Path(tmp))
            with self.assertRaises(ValueError):
                measure_mod.measure(SEED, broken)

    def test_unknown_band_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="recall-ann-") as tmp:
            text = CASES.read_text(encoding="utf-8-sig")
            broken = _temp_with(text.replace("\tliteral\n", "\tunknown_band\n"),
                                "cases.tsv", Path(tmp))
            with self.assertRaises(ValueError):
                measure_mod.measure(SEED, broken)

    def test_malformed_row_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="recall-ann-") as tmp:
            broken = _temp_with("只有一列\t没有其余三列\n", "cases.tsv", Path(tmp))
            with self.assertRaises(ValueError):
                measure_mod.measure(SEED, broken)


class ConstraintTests(unittest.TestCase):
    def test_no_real_db_and_no_writes(self) -> None:
        source = Path(measure_mod.__file__).read_text(encoding="utf-8-sig")
        code = code_only(source)
        self.assertNotIn("data/memory.db", code)
        for forbidden in ("write_text", "write_bytes", "shutil.rmtree"):
            self.assertNotIn(forbidden, code, f"测量器不应写文件：{forbidden}")
        self.assertIn(":memory:", code)

    def test_recall_floor_untouched(self) -> None:
        source = QUALITY.read_text(encoding="utf-8-sig")
        self.assertIn("RECALL_FLOOR = 0.75", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

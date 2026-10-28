"""本轮新增能力的防回归测试：零网络、零 Key、CI 可跑。

覆盖三个「手工探针验过、但没有自动化守护」的能力——探针写在 tmp/ 下不进 CI，
将来任何改动破坏它们都不会有人知道，这是假绿的另一种形态：

1. R21 的「开发瞬态 vs 真实回归」判定（evals_health.still_failing_cases /
   recovered_cases）：门禁退出码的正确性全押在这两个函数上；
2. R20 的被测对象标识追踪（evals_health 的 no_subject_n）：走
   load_snapshots() + analyze() 的真实路径；
3. R27 的 live 成本预估（run_evals.estimate_live_cost）：--dry-run 与
   --max-calls 两个参数的正确性全押在它身上。
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent))

# 先 import run_evals：它会在 import 时把 DB_PATH 钉成 :memory:。
import run_evals  # noqa: E402
import evals_health  # noqa: E402
import evals_report  # noqa: E402
from evals import checks  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _track(*, failed: bool, last_ok: bool, seen: int = 3,
           last_date: str = "2026-10-27") -> dict:
    """构造 analyze() 里 offline_track 的单条记录。"""
    return {"failed_dates": ["2026-10-27"] if failed else [],
            "last_ok": last_ok, "last_date": last_date, "seen": seen}


class HealthLayerGuardTests(unittest.TestCase):
    """R21：窗口内失效过 ≠ 该判红；只有「最近一次仍失败」才是真实回归。"""

    def test_still_failing_detects_current_failure(self):
        track = {"regressed": _track(failed=True, last_ok=False)}
        self.assertEqual(evals_health.still_failing_cases(track), ["regressed"])

    def test_recovered_is_not_still_failing(self):
        """失效过但已转绿 → 不是真实回归。这是 R21 修的那个坑。"""
        track = {"recovered": _track(failed=True, last_ok=True)}
        self.assertEqual(evals_health.still_failing_cases(track), [])
        self.assertEqual(evals_health.recovered_cases(track), ["recovered"])

    def test_never_failed_is_neither(self):
        track = {"healthy": _track(failed=False, last_ok=True)}
        self.assertEqual(evals_health.still_failing_cases(track), [])
        self.assertEqual(evals_health.recovered_cases(track), [])

    def test_mixed_cases_split_correctly(self):
        track = {
            "a-regressed": _track(failed=True, last_ok=False, seen=2),
            "b-recovered": _track(failed=True, last_ok=True, seen=3),
            "c-healthy": _track(failed=False, last_ok=True, seen=4),
        }
        self.assertEqual(evals_health.still_failing_cases(track), ["a-regressed"])
        self.assertEqual(evals_health.recovered_cases(track), ["b-recovered"])

    def test_the_two_verdicts_are_complementary(self):
        """核心不变量：任一用例只能落在「仍在失效」「已转绿」「两者都不」之一。

        若两个函数用了不一致的判据，同一条用例可能同时被判成两者——门禁会
        一边红一边说"已转绿不判红」，那是自相矛盾。
        """
        track = {
            "x": _track(failed=True, last_ok=False),
            "y": _track(failed=True, last_ok=True),
            "z": _track(failed=False, last_ok=True),
        }
        still = set(evals_health.still_failing_cases(track))
        recovered = set(evals_health.recovered_cases(track))
        self.assertEqual(still & recovered, set(), "同一条用例不能既是回归又已转绿")
        self.assertEqual(still | recovered, {"x", "y"})

    def test_empty_and_none_track(self):
        self.assertEqual(evals_health.still_failing_cases({}), [])
        self.assertEqual(evals_health.recovered_cases({}), [])
        self.assertEqual(evals_health.still_failing_cases(None), [])
        self.assertEqual(evals_health.recovered_cases(None), [])


class SubjectTrackingTests(unittest.TestCase):
    """R20：被测对象标识追踪，走 load_snapshots + analyze 的真实路径。"""

    def _run_analyze(self, snapshots: list[tuple[str, dict | None]]) -> dict:
        """把 (文件名, subject) 写进临时目录后跑真实链路。subject=None 表示缺字段。"""
        with tempfile.TemporaryDirectory(prefix="neurosama-r31-") as directory:
            root = Path(directory)
            for name, subject in snapshots:
                payload = {"at": name[:-5], "results": [
                    {"id": "probe-offline", "layer": "offline", "total_runs": 1,
                     "passed_runs": 1, "failed_runs": 0, "reviewed_runs": 0,
                     "passed": True, "runs": [{"passed": True}]}]}
                if subject is not None:
                    payload["subject"] = subject
                (root / name).write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            snapshots, _broken = evals_health.load_snapshots(root)
            return evals_health.analyze(snapshots, root / "no-cases-dir", NOW)

    def test_counts_snapshots_without_subject(self):
        res = self._run_analyze([
            ("20261026T120000Z.json", {"model": "m1"}),
            ("20261026T130000Z.json", None),          # 旧快照：无 subject
            ("20261027T120000Z.json", {"model": "m2"}),
        ])
        self.assertEqual(res["no_subject_n"], 1)

    def test_all_without_subject(self):
        res = self._run_analyze([
            ("20261026T120000Z.json", None),
            ("20261026T130000Z.json", None),
        ])
        self.assertEqual(res["no_subject_n"], 2)

    def test_all_with_subject(self):
        res = self._run_analyze([
            ("20261026T120000Z.json", {"model": "m1"}),
            ("20261026T130000Z.json", {"model": "m2"}),
        ])
        self.assertEqual(res["no_subject_n"], 0)

    def test_subject_survives_loading(self):
        """load_snapshots 必须把 subject 带出来——R20 的读取侧容错。"""
        with tempfile.TemporaryDirectory(prefix="neurosama-r31-") as directory:
            root = Path(directory)
            subject = {"provider": "openai", "base_url": "https://x/v1",
                       "model": "m", "judge": None, "git_head": "abc"}
            (root / "20261027T120000Z.json").write_text(json.dumps(
                {"at": "20261027T120000Z", "subject": subject,
                 "results": [{"id": "p", "layer": "offline", "total_runs": 1,
                              "passed_runs": 1, "failed_runs": 0,
                              "reviewed_runs": 0, "passed": True,
                              "runs": [{"passed": True}]}]},
                ensure_ascii=False), encoding="utf-8")
            snapshots, _ = evals_health.load_snapshots(root)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["subject"], subject)


class LiveCostEstimateTests(unittest.TestCase):
    """R27：--dry-run 与 --max-calls 的正确性全押在 estimate_live_cost 上。"""

    @staticmethod
    def _case(cid, *, layer="live", runs=None, turns=1, judges=0):
        case = {"id": cid, "layer": layer,
                "turns": [f"u{i}" for i in range(turns)],
                "checks": [{"value": "evals.checks:judge_hallucination"}
                           for _ in range(judges)]}
        if runs is not None:
            case["runs"] = runs
        return case

    def test_model_calls_are_runs_times_turns(self):
        est = run_evals.estimate_live_cost([self._case("a", runs=3, turns=2)])
        self.assertEqual(est["model_calls"], 6)
        self.assertEqual(est["turns"], 6)

    def test_judge_calls_are_runs_times_judge_checks(self):
        """裁判按「每次运行 1 次」计（_judge_with_aux 一次发完整 transcript）。"""
        est = run_evals.estimate_live_cost(
            [self._case("a", runs=3, turns=2, judges=1)])
        self.assertEqual(est["judge_calls"], 3)
        self.assertEqual(est["total_calls"], 9)

    def test_multiple_judge_checks_each_count(self):
        est = run_evals.estimate_live_cost(
            [self._case("a", runs=2, turns=1, judges=2)])
        self.assertEqual(est["judge_calls"], 4)
        self.assertEqual(est["total_calls"], 6)

    def test_offline_cases_cost_nothing(self):
        est = run_evals.estimate_live_cost(
            [self._case("o", layer="offline", runs=5, turns=6)])
        self.assertEqual(est["total_calls"], 0)
        self.assertEqual(est["turns"], 0)
        self.assertEqual(est["per_case"], [])

    def test_default_runs_is_three(self):
        est = run_evals.estimate_live_cost([self._case("d", turns=1)])
        self.assertEqual(est["model_calls"], 3)

    def test_empty_case_list(self):
        est = run_evals.estimate_live_cost([])
        self.assertEqual(est["total_calls"], 0)
        self.assertEqual(est["per_case"], [])
        self.assertEqual(est["tokens"], 0)

    def test_total_is_sum_of_parts(self):
        """自洽性不变量：不写死会随用例增减漂移的数字。"""
        cases = [self._case("a", runs=3, turns=2, judges=1),
                 self._case("b", runs=5, turns=6, judges=0),
                 self._case("c", layer="offline", runs=2, turns=3)]
        est = run_evals.estimate_live_cost(cases)
        self.assertEqual(est["total_calls"],
                         est["model_calls"] + est["judge_calls"])
        self.assertEqual(est["model_calls"],
                         sum(r["model_calls"] for r in est["per_case"]))

    def test_real_cases_layer_split(self):
        """对真实 evals/cases/ 跑一遍：只有 live 用例产生调用。"""
        est = run_evals.estimate_live_cost(run_evals.load_cases(None))
        self.assertGreater(est["model_calls"], 0, "真实语料里应有 live 用例")
        self.assertGreater(est["judge_calls"], 0, "真实语料里应有挂 judge 的 live 用例")
        self.assertEqual(est["total_calls"],
                         est["model_calls"] + est["judge_calls"])
        for row in est["per_case"]:
            self.assertEqual(row["layer"], "live", "per_case 只应含 live 用例")

    def test_print_estimate_does_not_touch_disk(self):
        """print_live_estimate 只打印，不写任何文件（--dry-run 依赖这一点）。"""
        with tempfile.TemporaryDirectory(prefix="neurosama-r31-") as directory:
            root = Path(directory)
            before = sorted(p.name for p in root.iterdir())
            est = run_evals.print_live_estimate(
                [self._case("a", runs=2, turns=1)])
            after = sorted(p.name for p in root.iterdir())
            self.assertEqual(before, after, "打印预估不应产生任何文件")
            self.assertEqual(est["total_calls"], 2)


class SubjectIdentityTests(unittest.TestCase):
    """R20：被测对象标识的写入侧与读取侧，四个函数一条链路。

    写入侧（subject_label）坏了 → 快照没有模型标识，跨周对比全报废；
    读取侧（subject_key / subject_desc / subject_changed_within）坏了 →
    **换模告警静默失效**：屏幕不再提示「换了被测对象」，读者会把换模型造成的
    100%→0% 曲线断点当成退化——那正是 R20 要防的核心误判。
    """

    FULL = {"provider": "openai", "base_url": "https://api.x/v1",
            "model": "step-3.7-flash", "judge": None,
            "git_head": "abcdef1234567890"}

    # ---- subject_key：身份键 ----

    def test_key_identical_when_all_three_fields_match(self):
        self.assertEqual(evals_report.subject_key(dict(self.FULL)),
                         evals_report.subject_key(dict(self.FULL)))

    def test_key_differs_when_model_differs(self):
        other = dict(self.FULL, model="step-4-flash")
        self.assertNotEqual(evals_report.subject_key(self.FULL),
                            evals_report.subject_key(other))

    def test_key_differs_when_provider_differs(self):
        other = dict(self.FULL, provider="anthropic")
        self.assertNotEqual(evals_report.subject_key(self.FULL),
                            evals_report.subject_key(other))

    def test_key_differs_when_base_url_differs(self):
        other = dict(self.FULL, base_url="https://api.y/v1")
        self.assertNotEqual(evals_report.subject_key(self.FULL),
                            evals_report.subject_key(other))

    def test_key_is_none_for_missing_or_empty_subject(self):
        """旧快照没有 subject 字段 → None，且不许抛（读取方一律容错）。"""
        self.assertIsNone(evals_report.subject_key(None))
        self.assertIsNone(evals_report.subject_key({}))

    # ---- subject_desc：人类可读描述 ----

    def test_desc_contains_model_and_base_url(self):
        text = evals_report.subject_desc(self.FULL)
        self.assertIn("step-3.7-flash", text)
        self.assertIn("https://api.x/v1", text)

    def test_desc_includes_judge_when_present(self):
        subject = dict(self.FULL, judge={"provider": "openai",
                                         "model": "step-5-preview"})
        self.assertIn("judge=openai/step-5-preview",
                      evals_report.subject_desc(subject))

    def test_desc_truncates_git_head_to_eight(self):
        text = evals_report.subject_desc(self.FULL)
        self.assertIn("HEAD abcdef12", text)
        self.assertNotIn("abcdef1234567890", text, "HEAD 应截断到 8 位")

    def test_desc_says_unknown_for_missing_subject(self):
        self.assertIn("未知", evals_report.subject_desc(None))

    def test_desc_never_leaks_credentials(self):
        """凭据红线：即使 subject 里混入 api_key，也不得出现在描述里。

        subject_desc 只读固定几个键，所以现在不会泄漏；这条断言防的是将来有人
        图省事改成「把整个 dict 倒出来」。
        """
        subject = dict(self.FULL, api_key="sk-SECRET-VALUE")
        text = evals_report.subject_desc(subject)
        self.assertNotIn("sk-SECRET-VALUE", text)
        self.assertNotIn("api_key", text)

    # ---- subject_changed_within：跨模型断点 ----

    def test_no_change_when_all_identical(self):
        snaps = [{"subject": dict(self.FULL)} for _ in range(3)]
        self.assertFalse(evals_report.subject_changed_within(snaps))

    def test_change_detected_when_model_swaps_midway(self):
        snaps = [{"subject": dict(self.FULL)},
                 {"subject": dict(self.FULL)},
                 {"subject": dict(self.FULL, model="step-4-flash")}]
        self.assertTrue(evals_report.subject_changed_within(snaps))

    def test_change_ignores_unknown_snapshots(self):
        """混有缺 subject 的旧快照时只按已知的比，且不许抛。"""
        snaps = [{"subject": None},
                 {"subject": dict(self.FULL)},
                 {"subject": dict(self.FULL)}]
        self.assertFalse(evals_report.subject_changed_within(snaps))

    def test_all_unknown_is_not_a_change(self):
        snaps = [{"subject": None}, {"subject": None}]
        self.assertFalse(evals_report.subject_changed_within(snaps))

    def test_empty_window_is_not_a_change(self):
        self.assertFalse(evals_report.subject_changed_within([]))

    # ---- subject_label：写入侧 ----

    @staticmethod
    def _settings(**overrides):
        base = dict(llm_provider="openai", llm_base_url="https://api.x/v1",
                    llm_model="step-3.7-flash",
                    llm_judge_provider="", llm_judge_base_url="",
                    llm_judge_api_key="", llm_judge_model="")
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_label_has_three_fields_and_no_credentials(self):
        """不联网、不读真实 .env：把 settings 换成桩。"""
        with patch.object(run_evals, "settings",
                          self._settings()):
            label = run_evals.subject_label()
        self.assertEqual(label["provider"], "openai")
        self.assertEqual(label["base_url"], "https://api.x/v1", "应去掉尾斜杠")
        self.assertEqual(label["model"], "step-3.7-flash")
        self.assertIsNone(label["judge"], "judge 未配置时应为 None")
        self.assertNotIn("api_key", label)
        self.assertNotIn("llm_api_key", label)

    def test_label_includes_judge_only_when_fully_configured(self):
        configured = dict(llm_judge_provider="openai",
                          llm_judge_base_url="https://api.x/v1",
                          llm_judge_api_key="k", llm_judge_model="judge-m")
        with patch.object(run_evals, "settings", self._settings(**configured)):
            label = run_evals.subject_label()
        self.assertEqual(label["judge"]["model"], "judge-m")
        self.assertNotIn("api_key", label["judge"])
        # 缺任一项都不该构造 judge（三项全非空才配）
        for missing in ("llm_judge_base_url", "llm_judge_api_key",
                        "llm_judge_model"):
            partial = dict(configured)
            partial[missing] = ""
            with patch.object(run_evals, "settings", self._settings(**partial)):
                self.assertIsNone(run_evals.subject_label()["judge"],
                                  f"缺 {missing} 时 judge 应为 None")

    def test_label_git_head_is_string_and_never_raises(self):
        """git HEAD 允许空串（非 git 环境），但必须是字符串且不崩。"""
        with patch.object(run_evals, "settings", self._settings()):
            label = run_evals.subject_label()
        self.assertIsInstance(label["git_head"], str)


class VerdictLogicTests(unittest.TestCase):
    """三个判定逻辑：safe_metrics / run_verdict / classify_trend。

    共同特征：坏了会**直接产生错误结论**，不是呈现问题——
    - safe_metrics 坏了 → 坏快照重新把 evals_report 门禁搞崩（报告层契约是
      「永不因内容失败」，它是唯一防线）；
    - run_verdict 坏了 → review 轮次重新被当成失败（R17 修的口径断裂复发）；
    - classify_trend 坏了 → 「最近退化」判错，而读者按它决定「查回归」还是
      「加 runs」——两者处置相反。
    """

    GOOD = {"id": "p", "layer": "offline", "total_runs": 2, "passed_runs": 2,
            "runs": [{"passed": True, "turn_meta": [{"completion_tokens": 10}]},
                     {"passed": True, "turn_meta": [{"completion_tokens": 20}]}]}

    # ---- safe_metrics：防崩包装（R16 的 B1） ----

    def test_safe_metrics_returns_dict_for_well_formed_case(self):
        m = evals_report.safe_metrics(dict(self.GOOD))
        self.assertIsNotNone(m)
        self.assertEqual(m["passed_runs"], 2)
        self.assertEqual(m["total_runs"], 2)
        self.assertEqual(m["tokens"], 30)

    def test_safe_metrics_returns_none_for_type_corrupt_cases(self):
        """四类「JSON 合法但字段类型不符」都必须返回 None 且不抛。"""
        for field, bad in (("total_runs", "abc"), ("passed_runs", "abc"),
                           ("runs", "abc")):
            case = dict(self.GOOD)
            case[field] = bad
            with self.subTest(field=field):
                self.assertIsNone(evals_report.safe_metrics(case))
        # turn_meta 里的 token 是字符串也算损坏
        case = dict(self.GOOD)
        case["runs"] = [{"passed": True,
                         "turn_meta": [{"completion_tokens": "x"}]}]
        self.assertIsNone(evals_report.safe_metrics(case))

    def test_safe_metrics_survives_empty_dict(self):
        self.assertIsNotNone(evals_report.safe_metrics({}))

    def test_safe_metrics_does_not_fabricate_zero_metrics(self):
        """返回 None 而非全 0 指标——伪造会把「数据损坏」显示成「没跑」。"""
        self.assertIsNone(evals_report.safe_metrics(dict(self.GOOD, total_runs="abc")))

    # ---- run_verdict：三态归一化（R17） ----

    def test_verdict_field_wins_when_present(self):
        for verdict in ("pass", "fail", "review"):
            with self.subTest(verdict=verdict):
                self.assertEqual(evals_health.run_verdict({"verdict": verdict}),
                                 verdict)

    def test_verdict_passthrough_for_unknown_strings(self):
        self.assertEqual(evals_health.run_verdict({"verdict": "weird"}), "weird")

    def test_missing_verdict_falls_back_to_passed(self):
        self.assertEqual(evals_health.run_verdict({"passed": True}), "pass")
        self.assertEqual(evals_health.run_verdict({"passed": False}), "fail")

    def test_empty_verdict_string_falls_back_to_passed(self):
        """空串是 falsy，`or` 语义会走 passed 反推——这个边界必须钉住。"""
        self.assertEqual(evals_health.run_verdict({"verdict": "", "passed": True}),
                         "pass")
        self.assertEqual(evals_health.run_verdict({"verdict": "", "passed": False}),
                         "fail")

    def test_missing_both_fields_is_fail_not_review(self):
        """verdict 与 passed 都缺 → 按 fail 算，不猜成 review。"""
        self.assertEqual(evals_health.run_verdict({}), "fail")

    # ---- 负向 check 在空文本上必须 fail-closed（R44） ----

    def test_negative_check_on_empty_text_fails_closed(self):
        """负向断言在空文本上恒真——必须判失败，不是通过。

        `value not in ""` 与 `re.findall(pattern, "")` 都恒真，所以不设防时
        空 replies / 空 prompt 会让负向 check 空转通过。R16 只在加载期禁了
        offline + target:reply，底层函数对这个洞没防；HEAD 版实测 ok=True。
        """
        for check in ({"type": "not_regex", "value": "我记得我们", "target": "reply"},
                      {"type": "not_contains", "value": "你以前说过", "target": "reply"}):
            for state in ({"replies": [], "prompt": ""},
                          {"replies": [""], "prompt": ""}):
                with self.subTest(check=check["type"], state=state):
                    ok, detail = asyncio.run(
                        run_evals._run_builtin_check(check, state))
                    self.assertFalse(ok, "空文本上的负向断言恒真，不许通过")
                    self.assertIn("为空", detail)

    def test_negative_check_still_works_on_real_text(self):
        """修了空文本分支，不能把正常负向断言误红。"""
        ok, _detail = asyncio.run(run_evals._run_builtin_check(
            {"type": "not_regex", "value": "我记得我们", "target": "reply"},
            {"replies": ["[base]我没有相关记忆"], "prompt": ""}))
        self.assertTrue(ok, "干净回复应通过负向断言")
        ok, detail = asyncio.run(run_evals._run_builtin_check(
            {"type": "not_regex", "value": "我记得我们", "target": "reply"},
            {"replies": ["[base]我记得我们上次一起去过"], "prompt": ""}))
        self.assertFalse(ok, "命中禁词应失败")
        self.assertIn("我记得我们", detail)

    # ---- classify_trend：五标签判定（R2/R12） ----

    def test_fewer_than_three_points_is_insufficient(self):
        self.assertEqual(evals_report.classify_trend([]), "数据不足")
        self.assertEqual(evals_report.classify_trend([True]), "数据不足")
        self.assertEqual(evals_report.classify_trend([True, False]), "数据不足")

    def test_all_pass_is_stable(self):
        self.assertEqual(evals_report.classify_trend([True] * 5), "长期稳定")

    def test_all_fail_is_persistent_failure(self):
        self.assertEqual(evals_report.classify_trend([False] * 5), "持续失效")

    def test_first_pass_last_fail_is_regression(self):
        self.assertEqual(evals_report.classify_trend([True, True, True, False]),
                         "最近退化")

    def test_oscillating_ending_in_fail_is_regression(self):
        """振荡形状也落「最近退化」：它确实当前失效，「查回归」动作没错。"""
        self.assertEqual(evals_report.classify_trend([True, False, True, False]),
                         "最近退化")

    def test_recovered_ending_in_pass_is_flaky_not_regression(self):
        """R12 明确记录的取舍：●●○●●（中间坏过、当前是好的）不判退化。"""
        self.assertEqual(evals_report.classify_trend([True, True, False, True, True]),
                         "一直抖")

    def test_first_fail_last_pass_is_flaky(self):
        self.assertEqual(evals_report.classify_trend([False, True, True]), "一直抖")


class TrendBuilderTests(unittest.TestCase):
    """R36：build_trend 的窗口切片 / xfail 排除 / 损坏与零轮次跳过 / subject_changed。

    它是趋势视图的数据源。这几处逻辑坏了都不会报错，只会让「最近退化」等判定
    静默失真——而读者按判定决定「查回归」还是「加 runs」，两者处置相反。
    """

    @staticmethod
    def _case(cid, *, layer="offline", xfail=False, total=1, passed=1):
        # total_runs / passed_runs 优先于 runs（case_metrics 的口径）；
        # total=0 时 runs 必须为空，否则 case_metrics 会回退成 len(runs)。
        return {"id": cid, "layer": layer, "xfail": xfail,
                "total_runs": total, "passed_runs": passed,
                "failed_runs": total - passed, "reviewed_runs": 0,
                "passed": passed == total,
                "runs": [{"passed": True}] * passed
                        + [{"passed": False}] * (total - passed)}

    @staticmethod
    def _snap(at, cases, subject=None):
        return {"file": f"{at}.json", "at": at, "subject": subject,
                "cases": {c["id"]: c for c in cases}}

    def _snaps(self, specs):
        """specs: [(at, [case...], subject), ...]，按时间正序。"""
        return [self._snap(at, cases, subject) for at, cases, subject in specs]

    # ---- 窗口切片 ----

    def test_window_keeps_only_last_n_non_xfail(self):
        n = evals_report.TREND_WINDOW
        specs = [(f"s{i}", [self._case("c")], None) for i in range(n + 3)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertEqual(len(trend["c"]["series"]), n)
        self.assertEqual(trend["c"]["pass_n"], n)
        self.assertEqual(trend["c"]["fail_n"], 0)
        self.assertEqual(trend["c"]["last_at"], f"s{n + 2}")

    # ---- xfail 排除 ----

    def test_xfail_excluded_from_series_but_counted(self):
        """xfail 不是通过也不是失败的证据：不进序列，但计入 xfail_n。"""
        specs = [("s0", [self._case("c", xfail=True)], None),
                 ("s1", [self._case("c")], None),
                 ("s2", [self._case("c", xfail=True)], None),
                 ("s3", [self._case("c")], None)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertEqual(trend["c"]["series"], [True, True])
        self.assertEqual(trend["c"]["xfail_n"], 2)
        # 2 个点不足判定 → 数据不足，不会因 xfail 被算成抖动
        self.assertEqual(trend["c"]["verdict"], "数据不足")

    # ---- 损坏跳过 ----

    def test_corrupt_occurrence_skipped(self):
        specs = [("s0", [self._case("c")], None),
                 ("s1", [dict(self._case("c"), total_runs="abc")], None),
                 ("s2", [self._case("c")], None)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertEqual(trend["c"]["series"], [True, True],
                         "字段损坏的那次不得计入序列")

    # ---- 零轮次跳过 ----

    def test_zero_run_occurrence_skipped(self):
        """total_runs=0 不计入——否则「没跑」被当成「失败」，凭空造出退化曲线。"""
        specs = [("s0", [self._case("c", total=0, passed=0)], None),
                 ("s1", [self._case("c")], None),
                 ("s2", [self._case("c")], None)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertEqual(trend["c"]["series"], [True, True])
        self.assertEqual(trend["c"]["fail_n"], 0)

    # ---- subject_changed（R20） ----

    def test_subject_changed_detected_within_window(self):
        s1 = {"provider": "p", "base_url": "u", "model": "m1"}
        s2 = {"provider": "p", "base_url": "u", "model": "m2"}
        specs = [("s0", [self._case("c")], s1), ("s1", [self._case("c")], s2)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertTrue(trend["c"]["subject_changed"])

    def test_subject_unchanged_within_window(self):
        s = {"provider": "p", "base_url": "u", "model": "m1"}
        specs = [("s0", [self._case("c")], s), ("s1", [self._case("c")], s)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertFalse(trend["c"]["subject_changed"])

    # ---- 其他不变量 ----

    def test_cases_are_independent(self):
        specs = [("s0", [self._case("a"), self._case("b", passed=0)], None)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertEqual(sorted(trend), ["a", "b"])
        self.assertEqual(trend["a"]["series"], [True])
        self.assertEqual(trend["b"]["series"], [False])

    def test_layer_uses_newest_known(self):
        """layer 取最近一次已知值（reversed + setdefault 的语义）。"""
        specs = [("s0", [self._case("c", layer="offline")], None),
                 ("s1", [self._case("c", layer="live")], None)]
        trend = evals_report.build_trend(self._snaps(specs))
        self.assertEqual(trend["c"]["layer"], "live")

    def test_pass_fail_counts_match_series(self):
        """自洽性：pass_n + fail_n == len(series)。"""
        specs = [("s0", [self._case("c")], None),
                 ("s1", [self._case("c", passed=0)], None),
                 ("s2", [self._case("c")], None)]
        entry = evals_report.build_trend(self._snaps(specs))["c"]
        self.assertEqual(entry["pass_n"] + entry["fail_n"], len(entry["series"]))

    def test_empty_snapshots(self):
        self.assertEqual(evals_report.build_trend([]), {})


class ChainIntegrationTests(unittest.TestCase):
    """集成契约：run_evals 写出的快照，evals_report 与 evals_health 必须读得懂。

    为什么需要：单测用的是**合成 snapshot dict**。若将来快照结构变更（R20 刚加过
    `subject` 字段）而合成数据没同步跟上，单测会继续绿、真实链却断了——写方改了
    结构、读方没跟上，这种漂移只有跑真实链路才抓得到。

    边界：RESULTS_DIR 指向临时目录，绝不往 evals/results/ 写合成快照（那会污染
    真实证据目录，让报告层把测试当成最新一次运行）；**不断言 main_async 的退出码**
    ——并发会话的 offline 用例可能正在开发中而失败，断言退出码会让本测试因别人的
    改动而红；这里只验「写出的结构」与「读得懂」这两件事。
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.results = Path(self.temporary.name) / "results"

    def _run_offline(self) -> str:
        """跑一次 offline 全量（不调模型、零 Key），快照落临时目录。返回 stdout。"""
        buffer = io.StringIO()
        with patch.object(run_evals, "RESULTS_DIR", self.results), \
                redirect_stdout(buffer):
            asyncio.run(run_evals.main_async(
                SimpleNamespace(live=False, case=None)))
        return buffer.getvalue()

    # ---- 写入侧契约（R20） ----

    def test_written_snapshots_carry_subject(self):
        self._run_offline()
        files = sorted(self.results.glob("*.json"))
        self.assertTrue(files, "offline 运行应至少落一份快照")
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("subject", data, f"{path.name} 缺 subject 字段")
            subject = data["subject"]
            for field in ("provider", "base_url", "model", "judge", "git_head"):
                self.assertIn(field, subject,
                              f"{path.name} 的 subject 缺 {field}")
            self.assertNotIn("api_key", subject, "subject 不得含凭据")

    # ---- 报告层读得懂 ----

    def test_report_layer_reads_real_snapshots(self):
        self._run_offline()
        files = sorted(self.results.glob("*.json"))
        snapshots, broken = evals_report.load_snapshots(self.results)
        self.assertEqual(broken, [], "真实快照不应被判为损坏")
        self.assertEqual(len(snapshots), len(files))
        payload = evals_report.build_json(snapshots, broken)
        self.assertEqual(payload["snapshot_count"], len(files))
        self.assertIn("cost", payload)
        # subject 应能被读取侧解析出身份键（None 表示解析失败）
        for snap in snapshots:
            self.assertIsNotNone(evals_report.subject_key(snap.get("subject")),
                                 "真实快照的 subject 应能解析出身份键")

    # ---- 健康度层读得懂 ----

    def test_health_layer_reads_real_snapshots(self):
        self._run_offline()
        snapshots, broken = evals_health.load_snapshots(self.results)
        self.assertEqual(broken, [])
        # 用当前时间而非固定 NOW：保证刚写的快照一定落在近期窗口内
        res = evals_health.analyze(snapshots, Path("no-such-cases-dir"),
                                   datetime.now(timezone.utc))
        self.assertEqual(res["no_subject_n"], 0,
                         "run_evals 刚写的快照都应带 subject")

    # ---- 趋势视图能在真实快照上产出 ----

    def test_trend_view_works_on_real_snapshots(self):
        """build_trend 必须能在真实快照上产出——合成数据可能已偏离真实结构。"""
        self._run_offline()
        snapshots, _ = evals_report.load_snapshots(self.results)
        trend = evals_report.build_trend(snapshots)
        self.assertTrue(trend, "真实快照应能产出趋势")
        for cid, entry in trend.items():
            self.assertIn("series", entry, f"{cid} 缺 series")
            self.assertIn("verdict", entry, f"{cid} 缺 verdict")
            self.assertIn("subject_changed", entry, f"{cid} 缺 subject_changed")
            self.assertIsInstance(entry["subject_changed"], bool)


class LoadTimeValidationTests(unittest.TestCase):
    """R47：三条加载期 fail-fast 必须有测试守护。

    为什么需要：`runs` 非正整数（R16）、offline 用 `target: reply`（R16）、
    `checks` 为空（R47）三条校验都只活在 main_async 里，此前**没有任何测试
    覆盖**——删掉其中任何一条，全部测试仍绿，而它防的恰是「一条断言都不跑
    就判 PASS」这类假绿。护栏自身没有护栏，是假绿的另一种形态。

    与 ChainIntegrationTests 的边界差异：那个类**不断言退出码**，因为并发会话
    的 offline 用例可能正在开发中而失败。这里靠**隔离输入**达到同样目的——
    CASES_DIR 指向只含本用例的临时目录，退出码只由本用例决定，与别人的改动
    无关。同理 RESULTS_DIR 也指向临时目录，绝不往 evals/results/ 写。
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cases = Path(self.temporary.name) / "cases"
        self.cases.mkdir()
        self.results = Path(self.temporary.name) / "results"

    def _write(self, body: str) -> None:
        (self.cases / "probe.yaml").write_text(body, encoding="utf-8")

    def _load(self) -> tuple[int, str]:
        """以隔离的 CASES_DIR / RESULTS_DIR 跑 main_async。返回 (退出码, stdout)。"""
        buffer = io.StringIO()
        with patch.object(run_evals, "CASES_DIR", self.cases), \
                patch.object(run_evals, "RESULTS_DIR", self.results), \
                redirect_stdout(buffer):
            rc = asyncio.run(run_evals.main_async(
                SimpleNamespace(live=False, case=None)))
        return rc, buffer.getvalue()

    # ---- R16 第一条：runs 不是正整数 ----

    def test_zero_runs_is_rejected(self):
        self._write("id: bad-runs\nlayer: offline\norigin: 探针\nruns: 0\n"
                    "turns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1, "runs=0 会让 0==0 判 PASS，必须 fail-fast")
        self.assertIn("不是正整数", out)

    def test_negative_runs_is_rejected(self):
        self._write("id: bad-neg\nlayer: offline\norigin: 探针\nruns: -2\n"
                    "turns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1)
        self.assertIn("不是正整数", out)

    # ---- R16 第二条：offline 不能用 target: reply ----

    def test_offline_target_reply_is_rejected(self):
        self._write("id: bad-target\nlayer: offline\norigin: 探针\nruns: 1\n"
                    "turns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: not_regex, value: 我记得我们, target: reply}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1, "offline 用 target: reply 会得到永远不命中/永远失败的用例")
        self.assertIn("不能用 target: reply", out)

    # ---- R47 第三条：checks 为空 ----

    def test_empty_checks_is_rejected(self):
        self._write("id: bad-checks\nlayer: offline\norigin: 探针\nruns: 2\n"
                    "turns: [问一句]\nsetup: {memories: []}\nchecks: []\n")
        rc, out = self._load()
        self.assertEqual(rc, 1, "checks 为空时 all([]) 恒真，必须 fail-fast")
        self.assertIn("checks 为空", out)

    def test_missing_checks_key_is_rejected(self):
        """缺 checks 与空 checks 同样形同虚设（case.get('checks') or [] 同为 []）。"""
        self._write("id: no-checks\nlayer: offline\norigin: 探针\nruns: 2\n"
                    "turns: [问一句]\nsetup: {memories: []}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1)
        self.assertIn("checks 为空", out)

    # ---- 反向：合法用例不得被误拦 ----

    def test_valid_offline_case_is_not_rejected(self):
        """三条校验都不得误伤合法用例，否则门禁会因误红而失去意义。"""
        self._write("id: good\nlayer: offline\norigin: 探针\nruns: 1\n"
                    "turns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 0, "合法 offline 用例应通过加载期校验并跑过")
        for msg in ("不是正整数", "不能用 target: reply", "checks 为空"):
            self.assertNotIn(msg, out, f"合法用例被 {msg} 误拦")

    # ---- R47：pass_rule 的 min_rate 阈值 ----

    def test_min_rate_zero_is_rejected(self):
        """min_rate:0 让 0/1>=0 恒真：断言全红仍判 PASS、退出码 0。"""
        self._write("id: mr0\nlayer: offline\norigin: 探针\nruns: 1\n"
                    "pass_rule: min_rate:0\nturns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 绝不出现XYZ, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1, "min_rate:0 会让必败 check 仍判 PASS，必须 fail-fast")
        self.assertIn("pass_rule 阈值非法", out)

    def test_min_rate_non_numeric_is_rejected(self):
        """min_rate:abc 会在 run_case 的 float() 崩，加载期先拦。"""
        self._write("id: mrx\nlayer: offline\norigin: 探针\nruns: 1\n"
                    "pass_rule: min_rate:abc\nturns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1)
        self.assertIn("pass_rule 阈值非法", out)

    def test_min_rate_above_one_is_rejected(self):
        """min_rate>1 永远无法满足，用例永远红，同样形同虚设。"""
        self._write("id: mrb\nlayer: offline\norigin: 探针\nruns: 1\n"
                    "pass_rule: min_rate:1.5\nturns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 1)
        self.assertIn("pass_rule 阈值非法", out)

    def test_valid_min_rate_is_not_rejected(self):
        """min_rate:0.8（真实用例 emotion-tag-retention-live 同款）必须放行。"""
        self._write("id: mrg\nlayer: offline\norigin: 探针\nruns: 1\n"
                    "pass_rule: min_rate:0.8\nturns: [问一句]\nsetup: {memories: []}\n"
                    "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
        rc, out = self._load()
        self.assertEqual(rc, 0, "合法 min_rate:0.8 应通过加载期校验并跑过")
        self.assertNotIn("pass_rule 阈值非法", out)

    # ---- R47：畸形 checks 形状（此前以裸 traceback 结束）----

    def test_malformed_checks_are_rejected(self):
        """checks 非 list / 含非 dict / 条目缺 type：加载期一句话拦下，不崩。"""
        for label, checks_yaml in [
            ("字符串", "checks: 我是字符串\n"),
            ("含 null", "checks: [null]\n"),
            ("缺 type", "checks:\n  - {value: x, target: prompt}\n"),
            ("dict", "checks: {a: 1}\n"),
        ]:
            with self.subTest(shape=label):
                self._write("id: mal\nlayer: offline\norigin: 探针\nruns: 1\n"
                            "turns: [问一句]\nsetup: {memories: []}\n" + checks_yaml)
                rc, out = self._load()
                self.assertEqual(rc, 1, f"畸形 checks（{label}）必须 fail-fast")
                self.assertIn("为空或形状损坏", out)

    # ---- R47：live 路径的空 checks（变异盲区：只拦 offline 会漏）----

    def test_live_empty_checks_is_rejected(self):
        """守卫必须同时覆盖 live 用例——只拦 offline 会让 live 空 checks 漏过。"""
        self._write("id: live-empty\nlayer: live\norigin: 探针\nruns: 1\n"
                    "turns: [问一句]\nsetup: {memories: []}\nchecks: []\n")
        rc, out = self._load()
        self.assertEqual(rc, 1, "live 用例空 checks 同样形同虚设，必须 fail-fast")
        self.assertIn("为空或形状损坏", out)

    # ---- R47：畸形 turns（此前 live 空 turns 会 0 次模型调用仍判过）----

    def test_malformed_turns_are_rejected(self):
        """turns 空/缺/非列表 = 一次对话都不发生：live 0 次模型调用、offline
        prompt 失去内容，与空 checks 同族。加载期一句话拦下，不跑到模型。"""
        for label, turns_yaml in [
            ("空 list", "turns: []\n"),
            ("缺", ""),
            ("字符串", "turns: 问一句\n"),
        ]:
            with self.subTest(shape=label):
                self._write("id: mal-turns\nlayer: offline\norigin: 探针\nruns: 1\n"
                            + turns_yaml
                            + "setup: {memories: []}\n"
                              "checks:\n  - {type: contains, value: 我在, target: prompt}\n")
                rc, out = self._load()
                self.assertEqual(rc, 1, f"畸形 turns（{label}）必须 fail-fast")
                self.assertIn("turns 为空或不是列表", out)

    def test_offline_target_reply_rejected_for_any_check_type(self):
        """target 守卫与 check 类型无关：not_contains/regex_any/custom 配
        target: reply 的 offline 用例同样要拦（只钉 not_regex 会被变异绕过）。"""
        for label, check_yaml in [
            ("not_contains", "  - {type: not_contains, value: 我记得我们, target: reply}\n"),
            ("regex_any", "  - {type: regex_any, value: 我在, target: reply}\n"),
            ("custom", "  - {type: custom, value: evals.checks:all_replies_start_with_valid_tag, target: reply}\n"),
        ]:
            with self.subTest(check=label):
                self._write("id: tgt\nlayer: offline\norigin: 探针\nruns: 1\n"
                            "turns: [问一句]\nsetup: {memories: []}\nchecks:\n"
                            + check_yaml)
                rc, out = self._load()
                self.assertEqual(rc, 1, f"offline + {label} + target:reply 必须 fail-fast")
                self.assertIn("不能用 target: reply", out)

    # ---- R54：seed 记忆良构 ----

    def _mem_case(self, memories_yaml: str) -> str:
        return ("id: mem\nlayer: offline\norigin: 探针\nruns: 1\nturns: [问一句]\n"
                + "setup:\n  memories:\n" + memories_yaml
                + "checks:\n  - {type: contains, value: 我在, target: prompt}\n")

    def test_seed_memory_wellformedness_is_enforced(self):
        """R54：缺/空 key、缺/空 value、非数值 fact_valid、非 dict 记忆都要 fail-fast。

        空 value（""）会令下游排序检查（current_before_history/future）真空转 PASS；
        缺 key/value 与非数值 fact_valid 会令 seed_memories 崩整轮——都在加载期拦。
        """
        for label, mem in [
            ("缺 key", "    - {value: v}\n"),
            ("缺 value", "    - {key: k}\n"),
            ("空串 value", '    - {key: k, value: ""}\n'),
            ("纯空格 value", '    - {key: k, value: "   "}\n'),
            ("非数值 fact_valid_until",
             '    - {key: k, value: v, fact_valid_until: "2024-12-31"}\n'),
            ("记忆是字符串", "    - 我不是dict\n"),
        ]:
            with self.subTest(shape=label):
                self._write(self._mem_case(mem))
                rc, out = self._load()
                self.assertEqual(rc, 1, f"畸形 seed 记忆（{label}）必须 fail-fast")
                self.assertIn("良构不达标", out)

    def test_wellformed_seed_memory_is_not_rejected(self):
        """R54 反向：合法 seed 记忆（含数值 fact_valid）不得被误拦。"""
        self._write(self._mem_case("    - {key: k, value: v}\n"))
        rc, out = self._load()
        self.assertEqual(rc, 0, "合法 seed 记忆应通过加载期校验")
        self.assertNotIn("良构不达标", out)
        # 数值 fact_valid 时间戳也应放行
        self._write(self._mem_case(
            "    - {key: k, value: v, fact_valid_until: 1735603199}\n"))
        rc, out = self._load()
        self.assertEqual(rc, 0, "数值 fact_valid 应放行")
        self.assertNotIn("良构不达标", out)


class ReviewQueueTests(unittest.TestCase):
    """R38：build_review_queue 的跨快照汇总、定位路径、窗口与降级。

    为什么需要：review 是「判定器说不清、需人工复核落盘原始回复」的欠账，
    但日常报告只看最新快照，而最新快照几乎总是纯 offline 跑——这个欠账因此
    在日常报告里完全不可见。本测试守住汇总逻辑不被改坏。
    """

    @staticmethod
    def _case(cid, layer, verdicts):
        runs = [{"verdict": v, "passed": v == "pass",
                 "turns": [{"user": "q", "reply": "r"}]} for v in verdicts]
        return {"id": cid, "layer": layer, "xfail": False,
                "total_runs": len(runs),
                "passed_runs": sum(1 for r in runs if r["passed"]),
                "failed_runs": sum(1 for r in runs if r["verdict"] == "fail"),
                "reviewed_runs": sum(1 for r in runs if r["verdict"] == "review"),
                "passed": all(r["passed"] for r in runs), "runs": runs}

    @staticmethod
    def _snap(at, cases):
        return {"file": f"{at}.json", "at": at, "subject": None,
                "cases": {c["id"]: c for c in cases}}

    def test_review_runs_are_collected_across_snapshots(self):
        snaps = [
            self._snap("20261025T100000Z",
                       [self._case("c", "live", ["pass", "review", "pass"])]),
            self._snap("20261026T100000Z",
                       [self._case("c", "live", ["review", "pass", "review"])]),
        ]
        queue = evals_report.build_review_queue(snaps)
        self.assertIn("c", queue)
        entry = queue["c"]
        self.assertEqual(entry["review_runs"], 3)
        self.assertEqual(entry["total_runs"], 6)
        self.assertEqual(entry["last_review_at"], "20261026T100000Z")
        self.assertEqual([loc["runs"] for loc in entry["locations"]], [[2], [1, 3]])
        # 欠账时长分诊（R40）：首次必须是窗口内最早那份，不能恒等于最近一次
        self.assertEqual(entry["first_review_at"], "20261025T100000Z")

    def test_non_review_cases_are_absent(self):
        snaps = [self._snap("s0", [self._case("c", "live", ["pass", "fail"])])]
        self.assertEqual(evals_report.build_review_queue(snaps), {})

    def test_empty_snapshots(self):
        self.assertEqual(evals_report.build_review_queue([]), {})

    def test_window_bounds_the_scan(self):
        """只扫最近 REVIEW_QUEUE_WINDOW 份——更早的欠账不进来。"""
        n = evals_report.REVIEW_QUEUE_WINDOW
        snaps = [self._snap(f"s{i}", [self._case("c", "live", ["review"])])
                 for i in range(n + 2)]
        queue = evals_report.build_review_queue(snaps)
        self.assertEqual(queue["c"]["review_runs"], n,
                         "超出窗口的旧快照不应计入")

    def test_corrupt_runs_do_not_crash(self):
        """runs 里混入非 dict 也不许抛（报告层契约：永不因内容失败）。"""
        case = self._case("c", "live", ["review"])
        case["runs"].append("not-a-dict")
        snaps = [self._snap("s0", [case])]
        entry = evals_report.build_review_queue(snaps)["c"]
        self.assertEqual(entry["review_runs"], 1)


class PersonaGuardTests(unittest.TestCase):
    """R42：persona 语气规格与状态机的配置层守卫（零 Key，不依赖模型）。

    守的是「规格被清空 / 改成空壳 / 状态机被改坏」这类**无显式报错**的回归：
    voice_style 被清空时模型只会慢慢变得像中性客服腔，从单条回复上看不出来。
    与 persona_negative_emotion_boundary 的分工：那条断言某条具体边界存在，
    这两条断言规格形状完整。
    """

    GOOD_STYLE = {"tone": "偏口语、偏懒", "sentence_length": "短句为主",
                  "banned": ["颜文字", "作为一个AI"]}
    GOOD_STATES = [{"name": "得意", "when": "用户采纳建议", "behave": "短促得意"},
                   {"name": "收敛", "when": "用户说别这样", "behave": "立刻停止调侃"}]

    @staticmethod
    def _run(fn, cfg):
        """把 persona_cfg 换成受控 dict 后跑判定函数（不碰真实 persona.yaml）。"""
        with patch("server.config.persona_cfg", lambda: cfg):
            return fn({}, {})

    def test_well_formed_persona_passes(self):
        cfg = {"voice_style": dict(self.GOOD_STYLE),
               "states": [dict(s) for s in self.GOOD_STATES]}
        ok, detail = self._run(checks.persona_voice_style_declared, cfg)
        self.assertTrue(ok, detail)
        self.assertIn("已声明语气规格", detail)
        ok, detail = self._run(checks.persona_states_well_formed, cfg)
        self.assertTrue(ok, detail)
        self.assertIn("状态机完整", detail)

    def test_voice_style_missing_or_empty_fails(self):
        for cfg in ({}, {"voice_style": None}, {"voice_style": {}},
                    {"voice_style": "not-a-dict"}):
            with self.subTest(cfg=cfg):
                ok, detail = self._run(checks.persona_voice_style_declared, cfg)
                self.assertFalse(ok)
                self.assertIn("voice_style", detail)

    def test_voice_style_missing_key_fields_fails(self):
        for field in ("tone", "sentence_length", "banned"):
            style = dict(self.GOOD_STYLE)
            style[field] = ""
            with self.subTest(field=field):
                ok, detail = self._run(checks.persona_voice_style_declared,
                                       {"voice_style": style})
                self.assertFalse(ok)
                self.assertIn(field, detail)

    def test_banned_must_be_a_list(self):
        style = dict(self.GOOD_STYLE, banned="颜文字、emoji")
        ok, detail = self._run(checks.persona_voice_style_declared,
                               {"voice_style": style})
        self.assertFalse(ok)
        self.assertIn("banned 应为列表", detail)

    def test_states_missing_or_empty_fails(self):
        for cfg in ({}, {"states": None}, {"states": []}, {"states": "x"}):
            with self.subTest(cfg=cfg):
                ok, detail = self._run(checks.persona_states_well_formed, cfg)
                self.assertFalse(ok)
                self.assertIn("states", detail)

    def test_states_missing_when_or_behave_fails(self):
        for field in ("when", "behave"):
            states = [dict(self.GOOD_STATES[0]), dict(self.GOOD_STATES[1])]
            states[0][field] = "   "  # 纯空白也算没填
            with self.subTest(field=field):
                ok, detail = self._run(checks.persona_states_well_formed,
                                       {"states": states})
                self.assertFalse(ok)
                self.assertIn(field, detail)

    def test_non_dict_state_entry_fails(self):
        states = [dict(self.GOOD_STATES[0]), "not-a-dict"]
        ok, detail = self._run(checks.persona_states_well_formed,
                               {"states": states})
        self.assertFalse(ok)
        self.assertIn("不是映射", detail)


class CharErrorRateTests(unittest.TestCase):
    """R43：中文字错率（CER）纯函数的边界与手算样例。

    这个函数是 ASR 字错率评测里**唯一可完全离线验证的一半**——它只保证
    「给定参考与假设文本时算得对」。ASR 的真实字错率要 live 音频语料 + 真实
    ASR 后端才能量，那部分刻意不做假实现（见 docs/TESTING.md 的口径说明）。
    """

    def test_hand_computed_sample(self):
        """末字替换：今天天气怎么样 → 今天天气怎么养 = 1/7。"""
        cer = checks.char_error_rate("今天天气怎么样", "今天天气怎么养")
        self.assertAlmostEqual(cer, 1 / 7, places=9)

    def test_both_empty_is_zero_not_full_error(self):
        """没有"应该识别出"的内容时不是全错。"""
        self.assertEqual(checks.char_error_rate("", ""), 0.0)

    def test_empty_reference_with_hypothesis_is_full_error(self):
        self.assertEqual(checks.char_error_rate("", "多出来的"), 1.0)

    def test_empty_hypothesis_is_full_error(self):
        self.assertEqual(checks.char_error_rate("今天天气", ""), 1.0)

    def test_identical_is_zero(self):
        self.assertEqual(checks.char_error_rate("完全一样", "完全一样"), 0.0)

    def test_english_is_also_character_based(self):
        """不按词：abc → abd 是一次替换，CER = 1/3。"""
        self.assertAlmostEqual(checks.char_error_rate("abc", "abd"), 1 / 3, places=9)

    def test_mass_deletion(self):
        self.assertAlmostEqual(checks.char_error_rate("今天天气怎么样", "天"),
                               6 / 7, places=9)

    def test_insertions_can_exceed_one(self):
        """上限不封顶：插入过多时 CER 可超过 1（调用方自行决定是否截断）。"""
        self.assertAlmostEqual(checks.char_error_rate("今天", "今天天气怎么样"),
                               5 / 2, places=9)

    def test_whitespace_is_significant(self):
        """不静默丢空白：ASR 输出多一个空格就是一次插入。

        若将来有人为了让数字好看而 strip，这条会红——那是在篡改指标。
        """
        self.assertAlmostEqual(
            checks.char_error_rate("今天天气", "今天 天气"), 1 / 4, places=9)

    def test_none_inputs_are_treated_as_empty(self):
        """None 不得抛（调用方可能从缺字段的 meta 里取到 None）。"""
        self.assertEqual(checks.char_error_rate(None, None), 0.0)
        self.assertEqual(checks.char_error_rate(None, "x"), 1.0)


class SnapshotRobustnessTests(unittest.TestCase):
    """R65：报告/健康层对**结构畸形**快照的韧性（R45/R16 只审了类型损坏字段）。

    契约：报告层「永不因内容失败、永远 exit 0」；健康层退出码只由真实 offline fail
    驱动，不被畸形快照歪曲。R65 实测九种结构畸形（snapshot 是 list / results 是 dict /
    results 含字符串·None / case 缺 runs / at 缺失·非字符串 / 顶层标量 / 空对象）在三层
    （load / analyze-render / 退出码）全部 graceful。本类锚定，防回归。
    """

    # load 层就该拦进 broken 的（结构不合法）
    REJECT = {
        "snapshot 是 list": "[1,2,3]",
        "results 是 dict": '{"at":"20261027T120000Z","results":{"a":1}}',
        "顶层是标量": "just a string",
        "空 JSON 对象": "{}",
    }
    # load 放行、但结构可疑，须由 analyze/render/退出码 graceful 处理
    SUSPECT = {
        "results 含字符串": '{"at":"20261027T120000Z","results":["我是字符串"]}',
        "results 含 None": '{"at":"20261027T120000Z","results":[null]}',
        "case 缺 total_runs": '{"at":"20261027T120000Z","results":[{"id":"x","layer":"offline","passed":true,"failed_runs":0}]}',
        "at 缺失": '{"results":[{"id":"x","layer":"offline","total_runs":1,"passed_runs":1,"failed_runs":0,"passed":true}]}',
        "at 非字符串": '{"at":12345,"results":[{"id":"x","layer":"offline","total_runs":1,"passed_runs":1,"failed_runs":0,"passed":true}]}',
    }

    def _write(self, directory: Path, content: str) -> Path:
        (directory / "s.json").write_text(content, encoding="utf-8")
        return directory

    def test_malformed_snapshots_land_in_broken_bucket(self):
        """结构不合法的快照必须进 broken 节，不崩、不计入统计。"""
        for label, content in self.REJECT.items():
            with self.subTest(shape=label):
                with tempfile.TemporaryDirectory(prefix="r65-") as d:
                    self._write(Path(d), content)
                    rep_snaps, rep_broken = evals_report.load_snapshots(Path(d))
                    h_snaps, h_broken = evals_health.load_snapshots(Path(d))
                    # broken 元素是描述字符串（含文件名与原因），不是 Path
                    self.assertTrue(rep_broken and all("s.json" in b for b in rep_broken),
                                    f"报告层应把 {label} 判损坏: {rep_broken}")
                    self.assertTrue(h_broken and all("s.json" in b for b in h_broken),
                                    f"健康层应把 {label} 判损坏: {h_broken}")
                    self.assertEqual(rep_snaps, [])
                    self.assertEqual(h_snaps, [])

    def test_suspect_snapshots_do_not_crash_analyze_or_render(self):
        """load 放行的可疑快照，analyze/render/build_json 不得崩。"""
        for label, content in self.SUSPECT.items():
            with self.subTest(shape=label):
                with tempfile.TemporaryDirectory(prefix="r65-") as d:
                    self._write(Path(d), content)
                    snaps, _broken = evals_report.load_snapshots(Path(d))
                    evals_report.build_trend(snaps)          # 不得抛
                    evals_report.render(snaps, _broken, Path(d))  # 不得抛
                    evals_report.build_json(snaps, _broken)  # 不得抛
                    h_snaps, _hb = evals_health.load_snapshots(Path(d))
                    evals_health.analyze(h_snaps, Path("no-cases"),
                                         datetime.now(timezone.utc))  # 不得抛

    def test_malformed_snapshots_do_not_distort_health_exit_code(self):
        """畸形快照不得伪造 exit 1，也不得掩盖真 offline 失效。"""
        import io
        from contextlib import redirect_stdout

        def run_health(content: str) -> int:
            with tempfile.TemporaryDirectory(prefix="r65-") as d:
                self._write(Path(d), content)
                buf = io.StringIO()
                with patch.object(sys, "argv",
                                  ["evals_health", "--results-dir", d,
                                   "--cases-dir", "no-such-cases"]), \
                        redirect_stdout(buf):
                    return evals_health.main()

        # 畸形快照 → exit 0（不伪造红）
        for label, content in self.SUSPECT.items():
            with self.subTest(shape=label):
                self.assertEqual(run_health(content), 0,
                                 f"畸形快照 {label} 不得让门禁伪造 exit 1")
        # 真 offline 失效 → exit 1（不被畸形掩盖）
        real_fail = ('{"at":"20261027T120000Z","results":[{"id":"bad","layer":"offline",'
                     '"total_runs":1,"passed_runs":0,"failed_runs":1,"passed":false,'
                     '"runs":[{"verdict":"fail","passed":false}]}]}')
        self.assertEqual(run_health(real_fail), 1, "真 offline 失效必须 exit 1")


class GateCoverageAnchorTests(unittest.TestCase):
    """R66：gate_coverage 元门禁的 fail-closed 与 LF 写入锚定。

    审计结论：元门禁 fail-closed（磁盘新增未登记→红、清单混入已登记→红），
    且 write_list 必须产 LF——Windows 文本模式会把 join 的 \\n 翻成 \\r\n，
    让清单每次 --write 都从 LF 漂成 CRLF、与 HEAD 制造整文件无意义 diff。
    """

    def test_write_list_emits_lf_not_crlf(self):
        """R66：write_list 必须产 LF，不得被 Windows 文本模式翻成 CRLF。"""
        import test_gate_coverage as tgc
        with tempfile.TemporaryDirectory(prefix="r66-") as d:
            target = Path(d) / "list.txt"
            with patch.object(tgc, "LIST_FILE", target):
                tgc.write_list(["test_probe_x.py"], 1)
            raw = target.read_bytes()
            self.assertNotIn(b"\r\n", raw, "write_list 不得产 CRLF（会污染清单行尾）")
            self.assertIn(b"\n", raw, "write_list 应产 LF")

    def test_gate_coverage_fails_closed_on_unregistered(self):
        """R66：磁盘新增未登记测试 → 门禁必须红（fail-closed，元门禁核心职责）。"""
        import test_gate_coverage as tgc
        probe = tgc.SCRIPTS_DIR / "test_r66_probe_unreg.py"
        probe.write_text("# probe\n", encoding="utf-8", newline="\n")
        try:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = tgc.main([])
            self.assertEqual(rc, 1, "新增未登记测试文件必须让 gate_coverage 变红")
            self.assertIn("未登记也不在清单里", buf.getvalue())
        finally:
            probe.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

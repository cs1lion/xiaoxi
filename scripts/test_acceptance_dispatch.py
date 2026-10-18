"""派单验收条件 + 有界自动重做的离线自检。

    .\\.venv\\Scripts\\python.exe scripts\\test_acceptance_dispatch.py

全部确定性：假适配器、临时目录、零网络、零真实模型调用。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

from server.assistant.adapters import register_adapter  # noqa: E402
from server.assistant.adapters.base import (BaseWorkerAdapter, artifact_event,  # noqa: E402
                                            make_event)
from server.assistant.evaluator import Evaluator, load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name} {detail}")


class AcceptanceAdapter(BaseWorkerAdapter):
    """按类级脚本逐次交付；每次驱动都会新建一个实例，prepare_context 留下密件。"""

    adapter_type = "acceptance_fake"
    instances: list["AcceptanceAdapter"] = []
    scripts: list = []

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        type(self).instances.append(self)
        self.prepared: list[dict] = []
        self.cancelled = 0

    async def prepare_context(self, commission_id: str, briefing_packet: dict) -> dict:
        self.prepared.append(dict(briefing_packet or {}))
        return {"commission_id": commission_id}

    async def execute(self, task_spec: dict):
        index = len(type(self).instances) - 1
        yield make_event("progress", step_idx=1, text="按验收条件干活")
        if not type(self).scripts:
            return
        script = type(self).scripts[min(index, len(type(self).scripts) - 1)]
        artifact = script() if callable(script) else script
        if artifact is not None:
            yield artifact_event(artifact)

    async def cancel(self) -> None:
        self.cancelled += 1


register_adapter("acceptance_fake", AcceptanceAdapter)


def artifact_for(path: Path, summary: str = "按验收条件交付完成。") -> dict:
    return {"summary": summary, "intercepted": "没有需要拦的。",
            "artifacts": [{"kind": "file", "ref": str(path), "preview": path.name}],
            "wired": True}


def writes(path: Path, text: str):
    def script():
        path.write_text(text, encoding="utf-8")
        return artifact_for(path)
    return script


def build_service(scripts, *, eval_commands=None) -> ExecutiveService:
    base = load_roster()
    base["agents"] = [{
        "id": "acc", "name": "验收工", "adapter": "acceptance_fake",
        "summary": "验收测试执行体",
        "strengths": ["验收", "acceptance"], "weaknesses": [], "cost_tier": "cheap",
        "max_concurrent": 2,
        "eval_commands": list(eval_commands or ["required_fields", "artifacts_exist",
                                                "acceptance_met"]),
    }]
    AcceptanceAdapter.scripts = list(scripts)
    AcceptanceAdapter.instances = []
    directory = Path(tempfile.mkdtemp(prefix="acceptance-dispatch-"))
    return ExecutiveService(directory, roster=base)


def run_delegate(scripts, acceptance, *, eval_commands=None):
    service = build_service(scripts, eval_commands=eval_commands)
    result = asyncio.run(service.delegate("按验收条件交付", acceptance=acceptance))
    commission = service.commission(result["commission_id"])
    instances = list(AcceptanceAdapter.instances)
    service.close()
    return result, commission, instances


def review(rules, artifact, commands=("required_fields", "artifacts_exist", "acceptance_met")):
    evaluator = Evaluator(roster=load_roster(), path=":memory:")
    packet = {"acceptance": list(rules), "budget": {"eval_commands": list(commands)}}
    return evaluator.review(commission={"id": "c_test", "retry_count": 0},
                            artifact=artifact, packet=packet)


def ids(verdict) -> list[str]:
    return [item["rule_id"] for item in verdict["findings"]]


def test_rule_kinds() -> None:
    print("[acceptance rule kinds]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-rules-"))
    note = work / "note.txt"
    note.write_text("OK\n", encoding="utf-8")
    good_json = work / "result.json"
    good_json.write_text('{"status":"ok","count":3}', encoding="utf-8")
    bad_json = work / "broken.json"
    bad_json.write_text("{not json", encoding="utf-8")
    artifact = artifact_for(note)

    check("artifact_exists 通过", review([{"kind": "artifact_exists"}], artifact)["passed"])
    check("artifact_name 通过", review([{"kind": "artifact_name", "name": "note.txt"}],
                                       artifact)["passed"])
    mismatch = review([{"kind": "artifact_name", "name": "other.txt"}], artifact)
    check("artifact_name 名字不符打回", "acceptance:artifact_name" in ids(mismatch))
    check("artifact_contains 通过", review([{"kind": "artifact_contains", "text": "OK"}],
                                           artifact)["passed"])
    miss = review([{"kind": "artifact_contains", "text": "MISSING"}], artifact)
    check("artifact_contains 内容不符打回", "acceptance:artifact_contains" in ids(miss))
    check("artifact_json 合法且 expect 命中",
          review([{"kind": "artifact_json", "name": "result.json",
                   "expect": {"status": "ok"}}], artifact_for(good_json))["passed"])
    invalid = review([{"kind": "artifact_json", "name": "broken.json"}], artifact_for(bad_json))
    check("artifact_json 非法 JSON 打回", "acceptance:artifact_json" in ids(invalid))
    wrong = review([{"kind": "artifact_json", "name": "result.json",
                     "expect": {"status": "nope"}}], artifact_for(good_json))
    check("artifact_json expect 不命中打回", "acceptance:artifact_json" in ids(wrong))
    check("artifact_regex 通过", review([{"kind": "artifact_regex", "pattern": "O."}],
                                        artifact)["passed"])
    regex_miss = review([{"kind": "artifact_regex", "pattern": "NOPE"}], artifact)
    check("artifact_regex 不匹配打回", "acceptance:artifact_regex" in ids(regex_miss))
    check("artifact_max_bytes 上限内通过",
          review([{"kind": "artifact_max_bytes", "bytes": 4096}], artifact)["passed"])
    too_big = review([{"kind": "artifact_max_bytes", "bytes": 2}], artifact)
    check("artifact_max_bytes 超限打回", "acceptance:artifact_max_bytes" in ids(too_big))

    unknown = review([{"kind": "no_such_rule"}], artifact)
    check("未知 kind 失败关闭", not unknown["passed"] and "acceptance:no_such_rule" in ids(unknown))
    missing_field = review([{"kind": "artifact_contains"}], artifact)
    check("缺字段失败关闭", "acceptance:artifact_contains" in ids(missing_field))
    traversal = review([{"kind": "artifact_contains", "text": "OK", "name": "../note.txt"}],
                       artifact)
    check("路径穿越失败关闭", "acceptance:artifact_contains" in ids(traversal))
    not_object = review(["just-a-string"], artifact)
    check("非对象规则失败关闭", "acceptance:invalid" in ids(not_object))

    skipped = review([{"kind": "no_such_rule"}], artifact,
                     commands=("required_fields", "artifacts_exist"))
    check("未列 acceptance_met 时不跑验收", skipped["passed"])
    check("没有规则时不跑验收", review([], artifact)["passed"])


def test_dispatch_acceptance_pass() -> None:
    print("[dispatch: acceptance pass]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-pass-"))
    rule = {"kind": "artifact_contains", "text": "OK", "name": "note.txt"}
    result, commission, instances = run_delegate([writes(work / "note.txt", "OK\n")], [rule])
    check("验收通过落到待验收", result["status"] == "ready_to_report", result["status"])
    check("只跑了一次", len(instances) == 1, str(len(instances)))
    check("规则原样进入密件", commission["packet"].get("acceptance") == [rule],
          str(commission["packet"].get("acceptance")))
    check("首次密件没有批注", instances[0].prepared[0].get("feedback") == "")


def test_dispatch_content_miss_retries() -> None:
    print("[dispatch: content miss]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-miss-"))
    rule = {"kind": "artifact_contains", "text": "OK", "name": "note.txt"}
    result, commission, instances = run_delegate([writes(work / "note.txt", "NO\n")], [rule])
    check("内容不符最终 blocked", result["status"] == "blocked", result["status"])
    check("finding 是验收规则", "acceptance:artifact_contains"
          in ids(commission.get("audit") or {}))
    check("重做次数与 MAX_RETRY 对齐", len(instances) == Evaluator.MAX_RETRY + 1,
          str(len(instances)))
    check("重试计数到上限", commission["retry_count"] == Evaluator.MAX_RETRY + 1,
          str(commission["retry_count"]))


def test_dispatch_json_rules() -> None:
    print("[dispatch: json rules]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-json-"))
    rule = {"kind": "artifact_json", "name": "result.json", "expect": {"status": "ok"}}
    passed_result, _commission, _instances = run_delegate(
        [writes(work / "result.json", '{"status":"ok","count":3}')], [rule])
    check("合法 JSON 通过", passed_result["status"] == "ready_to_report", passed_result["status"])
    failed_result, commission, _instances = run_delegate(
        [writes(work / "result.json", "{not json")], [rule])
    check("非法 JSON 打回", failed_result["status"] == "blocked", failed_result["status"])
    check("非法 JSON finding", "acceptance:artifact_json"
          in ids(commission.get("audit") or {}))


def test_dispatch_name_mismatch() -> None:
    print("[dispatch: name mismatch]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-name-"))
    result, commission, _instances = run_delegate(
        [writes(work / "note.txt", "OK\n")],
        [{"kind": "artifact_name", "name": "other.txt"}])
    check("名字不符最终 blocked", result["status"] == "blocked", result["status"])
    check("名字不符 finding", "acceptance:artifact_name"
          in ids(commission.get("audit") or {}))


def test_dispatch_unknown_rule() -> None:
    print("[dispatch: unknown rule]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-unknown-"))
    result, commission, _instances = run_delegate(
        [writes(work / "note.txt", "OK\n")], [{"kind": "no_such_rule"}])
    check("未知规则失败关闭", result["status"] == "blocked", result["status"])
    check("未知规则 finding", "acceptance:no_such_rule"
          in ids(commission.get("audit") or {}))


def test_no_acceptance_rules_skips() -> None:
    print("[dispatch: no acceptance rules]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-none-"))
    result, commission, _instances = run_delegate([writes(work / "note.txt", "OK\n")], None)
    check("没写验收条件照常放行", result["status"] == "ready_to_report", result["status"])
    check("密件 acceptance 为空", commission["packet"].get("acceptance") == [])
    check("初审没有验收 findings", not any(
        item.get("rule_id", "").startswith("acceptance:")
        for item in (commission.get("audit") or {}).get("findings", [])))


def test_missing_file() -> None:
    print("[dispatch: missing file]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-missing-"))
    result, commission, _instances = run_delegate(
        [artifact_for(work / "missing.txt")], [{"kind": "artifact_exists"}])
    check("文件不存在最终 blocked", result["status"] == "blocked", result["status"])
    check("文件不存在 finding", "acceptance:artifact_exists"
          in ids(commission.get("audit") or {}))


def test_feedback_reaches_retry() -> None:
    print("[dispatch: feedback on retry]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-feedback-"))
    path = work / "note.txt"
    rule = {"kind": "artifact_contains", "text": "OK", "name": "note.txt"}
    result, commission, instances = run_delegate(
        [writes(path, "NO\n"), writes(path, "OK\n")], [rule])
    check("第二版改对后放行", result["status"] == "ready_to_report", result["status"])
    check("恰好重做一次", len(instances) == 2, str(len(instances)))
    check("重试计数为 1", commission["retry_count"] == 1, str(commission["retry_count"]))
    check("第一次密件没有批注", instances[0].prepared[0].get("feedback") == "")
    feedback = instances[1].prepared[0].get("feedback", "")
    check("重试密件带上轮批注", "acceptance:artifact_contains" in feedback, feedback)


def test_retry_stops_at_max_retry() -> None:
    print("[dispatch: bounded retry]")
    work = Path(tempfile.mkdtemp(prefix="acceptance-bound-"))
    rule = {"kind": "artifact_contains", "text": "OK", "name": "note.txt"}
    result, commission, instances = run_delegate([writes(work / "note.txt", "NO\n")] * 10, [rule])
    check("不会无限重做", len(instances) == Evaluator.MAX_RETRY + 1, str(len(instances)))
    check("停在 blocked", result["status"] == "blocked", result["status"])
    check("每次重做都带批注",
          all(item.prepared[0].get("feedback") for item in instances[1:]))
    check("请示带完整批注", "acceptance:artifact_contains"
          in str(commission.get("blocked_question") or ""))


def test_packet_caps() -> None:
    print("[packet caps]")
    service = build_service([])
    commission = service.open("验收封顶", "把验收规则塞满")
    oversized = [{"kind": "artifact_contains",
                  **{f"pad{index}": "字" * 300 for index in range(8)}}]
    oversized += [{"kind": "artifact_contains", "text": "字" * 5000,
                   "name": f"f{index}.txt"} for index in range(9)]
    stored = service.dispatcher.dispatch(commission["id"], intent="验收",
                                        acceptance=oversized)[1].as_stored()
    encoded = json.dumps(stored, ensure_ascii=False)
    check("密件不超过 8000 字符", len(encoded) <= 8000, str(len(encoded)))
    check("规则数量封顶并留溢出标记", len(stored["acceptance"]) == 7
          and stored["acceptance"][-1]["kind"] == "acceptance_rule_overflow",
          str(stored["acceptance"]))
    check("单条超长规则换成失败关闭标记",
          stored["acceptance"][0]["kind"] == "acceptance_rule_too_large",
          str(stored["acceptance"][0]))
    service.start(commission["id"])
    service.dispatcher.retry_with_feedback(commission["id"], "批" * 5000)
    capped = service.commission(commission["id"])["packet"]
    check("批注长度封顶", len(capped["feedback"]) == 600, str(len(capped["feedback"])))
    check("重试不丢验收规则", capped.get("acceptance") == stored["acceptance"])
    service.close()


def main() -> int:
    for test in (test_rule_kinds, test_dispatch_acceptance_pass,
                 test_dispatch_content_miss_retries, test_dispatch_json_rules,
                 test_dispatch_name_mismatch, test_dispatch_unknown_rule,
                 test_no_acceptance_rules_skips, test_missing_file,
                 test_feedback_reaches_retry, test_retry_stops_at_max_retry,
                 test_packet_caps):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for name in failed:
        print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

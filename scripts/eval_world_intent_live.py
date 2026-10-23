"""带 Key 的意图链路实测：跑一遍同义改写语料，看模型到底怎么答。

这不是单元测试——它打真实模型，结果会随模型版本变化。
它的用途是**评估提示词**：哪些句子能被正确理解，哪些答成了 unknown 或答错。

用法：
    .venv/Scripts/python.exe scripts/eval_world_intent_live.py --port 8011

不联网时自动跳过，退出码 0（与离线门禁不冲突）。
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 真实世界的模板表（从 prefabs.json 取，不手抄）
PREFABS = json.loads((ROOT / "pixel-world" / "src" / "world" / "prefabs.json").read_text(encoding="utf-8"))
TEMPLATES = [
    {"id": pid, "displayName": entry["displayName"], "boundTask": entry.get("boundTask")}
    for pid, entry in PREFABS.items()
]

# 语料：每条给一个期望。`None` 表示期望 unknown（闲聊/提问/抱怨）。
# 分三组：规则能认出的对照组、规则漏掉的同义改写、不该触发世界的负例。
CORPUS: list[tuple[str, str, str | None]] = [
    # ---- 组 A：规则也认得出（对照组，验证模型没把确定的东西搞坏）
    ("A", "帮我写个爬虫", "dispatch"),
    ("A", "查一下最近的 AI 新闻", "dispatch"),
    ("A", "建个工坊", "build"),
    ("A", "下雨吧", "setWeather"),
    ("A", "交付", "complete"),
    ("A", "点灯", "miracle"),

    # ---- 组 B：规则漏掉的同义改写（模型该接住的）
    ("B", "把那份季度报告弄一下", "dispatch"),
    ("B", "帮我看看这块代码为啥跑不起来", "dispatch"),
    ("B", "在 (40,45) 种棵树", "miracle"),
    ("B", "那栋抄书斋拆了吧", "demolish"),
    ("B", "给我整个新铺子", "build"),
    ("B", "把天给我弄晴了", "setWeather"),
    ("B", "这单我验收了", "complete"),
    ("B", "帮我把测试跑一遍", "dispatch"),
    ("B", "把那堆便签归归类", "dispatch"),

    # ---- 组 C：不该动世界（负例，最重要的一组）
    ("C", "外面在下雨吗", None),
    ("C", "这个 bug 怎么写都不对", None),
    ("C", "今天好累啊", None),
    ("C", "你觉得该怎么办", None),
    ("C", "昨天已经下过雨了", None),
    ("C", "写代码真累", None),
    # 「别建了」合法地可以撤销排队中的建造，算 cancelPending 不算错。
    # 真正要防的是「别下雨了」被误判成撤销——实测答的是放晴，是对的。
    ("C", "别建了", "cancelPending"),
    ("C", "别下雨了", "setWeather"),
    ("C", "为什么天这么暗", None),
]


def ask(port: int, utterance: str, timeout: float = 40.0) -> tuple[dict, float]:
    body = {"utterance": utterance, "templates": TEMPLATES}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/world/intent",
        data=data,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8")), time.time() - start


def main() -> int:
    port = 8011
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    # 探活：服务不在就跳过，不算失败。
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5).read()
    except Exception as error:  # noqa: BLE001
        print(f"SKIP 服务未在 {port} 端口运行（{error}），跳过 live 评测。")
        return 0

    rows: list[dict] = []
    for group, utterance, expected in CORPUS:
        try:
            payload, elapsed = ask(port, utterance)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:120]
            rows.append({"group": group, "text": utterance, "expected": expected,
                         "got": f"HTTP {error.code}", "ok": False, "elapsed": 0.0, "why": detail})
            continue
        except Exception as error:  # noqa: BLE001
            rows.append({"group": group, "text": utterance, "expected": expected,
                         "got": f"ERR {error}", "ok": False, "elapsed": 0.0, "why": ""})
            continue

        intent = payload.get("intent", {})
        got = intent.get("kind", "?")
        if expected is None:
            ok = got == "unknown"
        else:
            ok = got == expected
        rows.append({
            "group": group, "text": utterance, "expected": expected or "unknown",
            "got": got, "ok": ok, "elapsed": elapsed,
            "why": intent.get("reason", "") if got == "unknown" else "",
        })

    # 输出
    out = io.StringIO()
    out.write("\n意图链路实测（真实模型）\n")
    out.write("=" * 78 + "\n")
    for group in ("A", "B", "C"):
        group_rows = [r for r in rows if r["group"] == group]
        passed = sum(1 for r in group_rows if r["ok"])
        label = {"A": "规则也认得出（对照）", "B": "同义改写（模型该接住）",
                 "C": "不该动世界（负例）"}[group]
        out.write(f"\n组 {group} · {label}  {passed}/{len(group_rows)}\n")
        for r in group_rows:
            mark = "ok  " if r["ok"] else "MISS"
            out.write(f"  {mark} 「{r['text']}」\n")
            out.write(f"       期望 {r['expected']} / 实得 {r['got']}  ({r['elapsed']:.1f}s)\n")
            if r["why"]:
                out.write(f"       模型理由：{r['why']}\n")

    total = sum(1 for r in rows if r["ok"])
    avg = sum(r["elapsed"] for r in rows) / max(1, len(rows))
    out.write("\n" + "=" * 78 + "\n")
    out.write(f"总计 {total}/{len(rows)}  ·  平均 {avg:.1f}s\n")

    # 分组小计，供判断改动是否有效
    for group in ("A", "B", "C"):
        group_rows = [r for r in rows if r["group"] == group]
        out.write(f"  组 {group}: {sum(1 for r in group_rows if r['ok'])}/{len(group_rows)}\n")

    report = out.getvalue()
    # 终端码页可能是 GBK，写文件后由调用方读。
    (ROOT / "scripts" / ".intent-live-report.txt").write_text(report, encoding="utf-8")
    print(f"报告已写入 scripts/.intent-live-report.txt（{total}/{len(rows)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

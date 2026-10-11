"""接缝漂移检查 —— 形象层与后端之间所有会静默失败的耦合。

背景：形象层（web/avatar）和说话层（server）由不同的人同时改，
两边唯一的约定是 WebSocket 消息的形状。约定漂移时不会抛异常，
只会退化成"角色永远没表情"或者"表情卡住不动"，非常难查。

所以与其靠约定，不如把它变成机器可检的。这一脚本查四类漂移：

  1. 情绪标签：persona.yaml 的 tts_emotions ↔ avatar.yaml 的 emotions 双向齐全
  2. 参数表：web/avatar/params.js 的 PARAM_GROUPS ↔ avatar.yaml 的 neutral 互为全集
  3. 内部引用：表情 shift、手势关键帧、触发器、注视点的键必须真实存在
  4. 线上协议：app.js 每个 case 分支里读的 msg.<字段>，后端必须真的发这个字段
     （第 4 条就是本脚本存在的主要理由：它抓到过一次"后端把 text 帧换成
       emotion + text_delta 之后，形象层的 hold 姿势永久卡死"的线上事故）

用法：uv run python scripts/check_protocol.py
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import avatar_cfg, persona_cfg  # noqa: E402

# 这些"type"是服务端内部数据结构 / 别的子协议，不是 /ws 的消息
NOT_PROTOCOL = {"json_object", "pcm", "ok"}

PARAMS_JS = ROOT / "web" / "avatar" / "params.js"
APP_JS = ROOT / "web" / "app.js"
SERVER_DIR = ROOT / "server"


def all_params() -> set[str]:
    text = PARAMS_JS.read_text(encoding="utf-8")
    block = re.search(r"PARAM_GROUPS\s*=\s*\{(.*?)\n\};", text, re.S)
    if not block:
        raise SystemExit(f"读不到 PARAM_GROUPS，{PARAMS_JS} 结构变了？")
    return set(re.findall(r'"(Param[A-Za-z]+)"', block.group(1)))


def emitted_types() -> dict[str, set[str]]:
    """扫全部 server/*.py，收集 {"type": "X", ...} 形式的字典字面量。"""
    found: dict[str, set[str]] = {}
    for path in sorted(SERVER_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                k.value: v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            t = keys.get("type")
            if isinstance(t, ast.Constant) and isinstance(t.value, str):
                found.setdefault(t.value, set()).update(set(keys) - {"type"})
    return {k: v for k, v in found.items() if k not in NOT_PROTOCOL}


def frontend_cases() -> dict[str, set[str]]:
    """从 app.js 的 switch 里抽出每个 case 分支读了哪些 msg.<字段>。"""
    text = APP_JS.read_text(encoding="utf-8")
    start = text.find("switch (msg.type)")
    if start < 0:
        raise SystemExit(f"{APP_JS} 里找不到 switch (msg.type)")
    end = text.find("\n};", start)
    body = text[start:end if end > 0 else len(text)]

    cases: dict[str, set[str]] = {}
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'case "([a-z_]+)":', body)]
    for i, (pos, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        fields = set(re.findall(r"\bmsg\.([a-z_]+)", body[pos:stop]))
        cases.setdefault(name, set()).update(fields)
    return cases


def main() -> int:
    persona = persona_cfg()
    avatar = avatar_cfg()
    params = all_params()
    problems: list[str] = []

    # ---- 1. 情绪标签双向齐全 ----
    voices = {k for k in persona["tts_emotions"] if k != "base"}
    faces = {k for k in avatar["emotions"] if k != "base"}
    for missing in sorted(faces - voices):
        problems.append(f"avatar.yaml 有表情 {missing!r}，但 persona.yaml 没有对应情绪")
    for missing in sorted(voices - faces):
        problems.append(f"persona.yaml 有情绪 {missing!r}，但 avatar.yaml 没有对应表情")

    # ---- 2. 参数表与中位姿态互为全集 ----
    neutral = avatar.get("neutral", {})
    for key in sorted(params - set(neutral)):
        problems.append(f"params.js 有参数 {key}，但 avatar.yaml 的 neutral 没写（会按 0 当中位）")
    for key in sorted(set(neutral) - params):
        problems.append(f"avatar.yaml 的 neutral 写了 {key}，但 params.js 里没有（会被静默丢弃）")

    # ---- 3. 内部引用必须可达 ----
    for name, rule in avatar["emotions"].items():
        for key in (rule.get("shift") or {}):
            if key not in params:
                problems.append(f"情绪 {name!r} 的 shift 里有未知参数 {key!r}")
    gestures = avatar.get("gestures", {})
    for name, track in gestures.items():
        for i, k in enumerate(track.get("keys") or []):
            for key in (k.get("params") or {}):
                if key not in params:
                    problems.append(f"手势 {name!r} 第 {i} 个关键帧有未知参数 {key!r}")
    triggers = avatar.get("triggers", {})
    referenced = list((triggers.get("on_message") or {}).values())
    referenced += list(triggers.get("text") or {})
    referenced += list((triggers.get("on_emotion") or {}))
    for name in referenced:
        if name and name not in gestures:
            problems.append(f"triggers 引用了不存在的手势 {name!r}")
    targets = set((avatar.get("gaze") or {}).get("targets") or {})
    for name, target in ((avatar.get("gaze") or {}).get("on_message") or {}).items():
        if target not in targets:
            problems.append(f"gaze.on_message.{name} 指向不存在的注视点 {target!r}")

    # ---- 4. 线上协议：前端读的字段后端必须真的发 ----
    emitted = emitted_types()
    cases = frontend_cases()
    for name, fields in sorted(cases.items()):
        if name not in emitted:
            problems.append(f"app.js 处理了 {name!r}，但服务端找不到发这个消息的地方")
            continue
        missing = {f for f in fields if f != "type"} - emitted[name]
        if missing:
            problems.append(
                f"app.js 的 {name!r} 分支读了 {sorted(missing)}，"
                f"但服务端该消息只发 {sorted(emitted[name])}"
            )
    # 服务端发了但前端没处理：不算错，只提示——形象层往往有意不关心某些消息
    ignored = sorted(set(emitted) - set(cases))

    if problems:
        print("FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print(
        f"PASS  {len(faces)} 个情绪标签双向齐全；"
        f"{len(params)} 个参数与中位姿态互为全集；"
        f"{len(cases)} 个消息类型的字段全部对得上"
    )
    if ignored:
        print(f"      （服务端还发了这些，前端未处理，对形象层无影响：{', '.join(ignored)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
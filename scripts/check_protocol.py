"""接缝漂移检查 —— 形象层与后端之间所有会静默失败的耦合。

背景：形象层（web/avatar）和说话层（server）由不同的人同时改，
两边唯一的约定是 WebSocket 消息的形状。约定漂移时不会抛异常，
只会退化成"角色永远没表情"或者"表情卡住不动"，非常难查。

所以与其靠约定，不如把它变成机器可检的。这一脚本查八类漂移：

  1. 情绪标签：persona.yaml 的 tts_emotions ↔ avatar.yaml 的 emotions 双向齐全
  2. 参数表：web/avatar/params.js 的 PARAM_GROUPS ↔ avatar.yaml 的 neutral 互为全集
  3. 内部引用：表情 shift、手势关键帧、触发器、注视点的键必须真实存在
  4. 线上协议：app.js 每个 case 分支里读的 msg.<字段>，后端必须真的发这个字段
     （第 4 条就是本脚本存在的主要理由：它抓到过一次"后端把 text 帧换成
       emotion + text_delta 之后，形象层的 hold 姿势永久卡死"的线上事故）
  5. REST 接缝：web 的 js 里引用的 /api/ 字面量路由，服务端必须真的注册
     （星图这类跨角色接口最常死于静默 404；含 ${ 的模板串无法静态核对，放过）
  6. 积木契约：PROTOCOL.md §23 的六种 aqi_blocks kind，服务端组装器与前端
     渲染器必须同时认账——少任何一边，积木会静默变成不渲染/不产出的空卡
  7. ui_nav 领航：PROTOCOL.md §24 的 target_view / sub_action 取值域，服务端实际
     下发与前端白名单必须一致；gaze_target 必须落在 avatar.yaml 的 gaze.targets

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
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'''case ["']([a-z_]+)["']:''', body)]
    for i, (pos, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        fields = set(re.findall(r"\bmsg\.([a-z_]+)", body[pos:stop]))
        cases.setdefault(name, set()).update(fields)
    return cases


def turn_envelope_fields() -> set[str]:
    """Read fields actually added by Connection.send_turn, rather than exempting them."""
    tree = ast.parse((SERVER_DIR / 'app.py').read_text(encoding='utf-8'))
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name != 'Connection':
            continue
        for method in cls.body:
            if not isinstance(method, ast.AsyncFunctionDef) or method.name != 'send_turn':
                continue
            for node in ast.walk(method):
                if (isinstance(node, ast.Dict) and any(k is None and isinstance(v, ast.Name)
                        and v.id == 'payload' for k, v in zip(node.keys, node.values))):
                    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return set()


def server_routes() -> set[str]:
    """Collect literal routes: @app.<m>(...) plus @<router>.<m>(...) joined with APIRouter prefixes."""
    routes: set[str] = set()
    for path in sorted(SERVER_DIR.rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'@app\.(?:get|post|patch|delete|put)\(\s*["\']([^"\']+)["\']', text):
            routes.add(match.group(1))
        prefixes = dict(re.findall(r'(\w+)\s*=\s*APIRouter\(\s*prefix\s*=\s*["\']([^"\']+)["\']', text))
        # 集合路由注册的是空路径（@router.get('')），所以路径组必须允许零长度。
        for match in re.finditer(r'@(\w+)\.(?:get|post|patch|delete|put)\(\s*["\']([^"\']*)["\']', text):
            routes.add(prefixes.get(match.group(1), '') + match.group(2))
    return {route for route in routes if route.startswith('/')}


INTERNAL_ROUTES = ('/internal/telemetry_proxy',)
"""服务端自用路由：前端不引用，所以第五类检查看不到它们。

观测网关这条最怕被顺手重构掉——改了路径，Worker 的调用会静默 404，
而前端一个字都不会提。所以在这里显式纳管：契约一改，静态检查直接报警。
"""

# 协议 §23 的七种标准积木。契约文档、服务端组装器、前端渲染器三方必须同时
# 认账：少一边不会报错，只会让积木静默变成空卡（后端不产 / 前端不画）。
AQI_BLOCK_KINDS = ("choice_pills", "spotlight_card", "alert_card", "swimlane_strip",
                   "metric_flame", "note_snippet", "memo_note")
# 协议 §23.1：layout / gaze / source 的取值域，文档与前后端各写一份，这里钉死。
AQI_LAYOUTS = ("stage_holo", "drawer_ops")
AQI_GAZES = ("holo_right", "drawer", "user")
AQI_SOURCES = ("chat", "executive", "ambient", "loop")
AQI_COMPOSER = ROOT / "server" / "dialogue" / "aqi_blocks.py"
AQI_RENDERER = ROOT / "web" / "blocks.js"
# 协议 §24：ui_nav 领航载荷的取值域，前后端各写一份，这里钉死不让漂。
UI_NAV_TARGETS = ("ops", "notes", "threads", "starmap", "close")
UI_NAV_SUB_ACTIONS = ("open_preview", "highlight_telemetry", "highlight_rubrics", "focus_latest")


def ui_nav_gaze_targets() -> set[str]:
    """gaze_target 必须落在 avatar.yaml 的 gaze.targets 里——沿用第三类的引用规则。"""
    from server.config import avatar_cfg
    return set((avatar_cfg().get("gaze") or {}).get("targets") or {})


def emitted_ui_nav_fields() -> dict[str, set[str]]:
    """服务端 orchestrator 里 ui_nav 载荷各字段实际写到的字面量。"""
    text = (SERVER_DIR / "dialogue" / "orchestrator.py").read_text(encoding="utf-8")

    def values(key: str) -> set[str]:
        seen = set(re.findall(rf'{key}[=:]\s*["\']([a-z_]+)["\']', text))
        seen.update(re.findall(rf'\["{key}"\]\s*=\s*["\']([a-z_]+)["\']', text))
        return seen

    return {"target_view": values("target_view"), "sub_action": values("sub_action"),
            "gaze_target": values("gaze_target")}


def frontend_ui_nav_fields() -> dict[str, set[str]]:
    """前端 web/ui_nav.js 里登记的取值域白名单（领航安全降级靠它兜底）。"""
    path = ROOT / "web" / "ui_nav.js"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    fields: dict[str, set[str]] = {}
    # 白名单以 `UI_NAV_TARGETS = [...]` / `UI_NAV_SUB_ACTIONS = [...]` 形式登记，
    # 按前缀匹配，不硬编码各字段的变量名，避免前后端两份漂移。
    m = re.search(r'UI_NAV_TARGETS\s*=\s*\[([^\]]*)\]', text)
    if m:
        fields["target_view"] = set(re.findall(r'["\']([a-z_]+)["\']', m.group(1)))
    m = re.search(r'UI_NAV_SUB_ACTIONS\s*=\s*\[([^\]]*)\]', text)
    if m:
        fields["sub_action"] = set(re.findall(r'["\']([a-z_]+)["\']', m.group(1)))
    return fields


def check_ui_nav(problems: list[str]) -> None:
    """§24 ui_nav：前后端取值域同步，服务端只发 avatar.yaml 有登记的注视点。"""
    emitted = emitted_ui_nav_fields()
    for key, allowed in (("target_view", UI_NAV_TARGETS), ("sub_action", UI_NAV_SUB_ACTIONS)):
        bad = emitted.get(key, set()) - set(allowed)
        if bad:
            problems.append(f"orchestrator.py 的 ui_nav.{key} 发到了协议未登记的取值 {sorted(bad)}")
    bad_gaze = emitted.get("gaze_target", set()) - ui_nav_gaze_targets()
    if bad_gaze:
        problems.append(
            f"orchestrator.py 的 ui_nav.gaze_target 指向了 avatar.yaml 没登记的注视点 {sorted(bad_gaze)}"
            f"（应为 {sorted(ui_nav_gaze_targets())}）")
    frontend = frontend_ui_nav_fields()
    if frontend:
        for key, allowed_vals in (("target_view", UI_NAV_TARGETS), ("sub_action", UI_NAV_SUB_ACTIONS)):
            missing = set(allowed_vals) - frontend.get(key, set())
            if missing:
                problems.append(f"web/ui_nav.js 的 {key} 白名单没覆盖协议取值 {sorted(missing)}")


def frontend_api_calls() -> set[str]:
    """Literal /api/... strings in web js; dynamic template paths cannot be checked statically."""
    calls: set[str] = set()
    for path in sorted((ROOT / 'web').rglob('*.js')):
        for match in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-/{}$]+)', path.read_text(encoding='utf-8')):
            literal = match.group(1)
            if '$' in literal:
                continue
            calls.add(literal.split('?')[0].rstrip('/') or '/api')
    return calls


def route_exists(reference: str, routes: set[str]) -> bool:
    parts = reference.strip('/').split('/')
    for route in routes:
        segments = route.strip('/').split('/')
        if len(segments) != len(parts):
            continue
        if all(want.startswith('{') or want == got for want, got in zip(segments, parts)):
            return True
    return False


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

    # 注视点的坐标被 web/avatar/test_gaze.js 抄了一份。
    # 那份抄本必须和这里的配置一致，否则"眼-头时序"那几条断言
    # 会在错误的注视点上跑——而它们恰恰是最容易被误判成"通过"的类型：
    # 目标回退到原点的 viewer 时，眼不动、头也不动，断言反而会按预期方向成立。
    test_gaze = ROOT / "web" / "avatar" / "test_gaze.js"
    if test_gaze.exists():
        src = test_gaze.read_text(encoding="utf-8")
        for name, spec in ((avatar.get("gaze") or {}).get("targets") or {}).items():
            want = f"{name}: {{ x: {float(spec['x']):.2f}, y: {float(spec['y']):.2f},"
            if want not in src:
                problems.append(
                    f"web/avatar/test_gaze.js 里的注视点 {name!r} 和 config/avatar.yaml 不一致"
                    f"（应形如 {want!r}）"
                )

    # 共发音参数：buildTimeline 会读 config 里的 coarticulation，
    # 少了整段就用代码里的默认值，两者一旦漂移，调试时很难看出是谁在起作用。
    if "coarticulation" not in avatar:
        problems.append("config/avatar.yaml 缺 coarticulation 段（口型会退回代码默认值）")

    # ---- 5. 连续情感维度 ----
    # 每个情绪都要有 arousal/valence。少了不会报错、不会崩，
    # 只是那个情绪退回中性唤醒度——所有情绪强度变得一模一样，
    # 这种"静默变平"正是最难察觉的一类回归。
    for name, rule in avatar["emotions"].items():
        for key in ("arousal", "valence"):
            if key not in rule:
                problems.append(f"情绪 {name!r} 缺 {key}（会退回中性，表情强度全被拉平）")
                continue
            v = rule[key]
            lo, hi = (0.0, 1.0) if key == "arousal" else (-1.0, 1.0)
            if not isinstance(v, (int, float)) or not (lo <= v <= hi):
                problems.append(f"情绪 {name!r} 的 {key}={v!r} 超出 [{lo}, {hi}]")

    # 眼-头协调参数是 gaze 契约的一部分，缺了会退回代码默认值，
    # 而默认值的征用门槛和 YAML 里配的不一样（见 config 注释）。
    for key in ("recruit", "head_capacity", "head_lead", "head_lag", "opn_gate"):
        if key not in ((avatar.get("gaze") or {}).get("coord") or {}):
            problems.append(f"gaze.coord 缺 {key!r}（眼-头协调会退回代码默认值）")

    # ---- 4. 线上协议：前端读的字段后端必须真的发 ----
    emitted = emitted_types()
    envelope = turn_envelope_fields()
    cases = frontend_cases()
    for name, fields in sorted(cases.items()):
        if name not in emitted:
            problems.append(f"app.js 处理了 {name!r}，但服务端找不到发这个消息的地方")
            continue
        common = envelope if name not in ('ready', 'config_ack') else set()
        missing = {f for f in fields if f != "type"} - emitted[name] - common
        if missing:
            problems.append(
                f"app.js 的 {name!r} 分支读了 {sorted(missing)}，"
                f"但服务端该消息只发 {sorted(emitted[name])}"
            )
    # 服务端发了但前端没处理：不算错，只提示——形象层往往有意不关心某些消息
    ignored = sorted(set(emitted) - set(cases))

    # ---- 5. REST 接缝：前端引用的 /api 路由必须真的存在 ----
    routes = server_routes()
    api_routes = {route for route in routes if route.startswith('/api/')}
    for call in sorted(frontend_api_calls()):
        if not route_exists(call, api_routes):
            problems.append(f"web 里引用了 {call}，但服务端没有注册这个路由（跨角色接口会静默 404）")

    # ---- 6. 服务端自用路由：前端不引用，改坏了同样静默 ----
    for internal in INTERNAL_ROUTES:
        if internal not in routes:
            problems.append(f"服务端自用路由 {internal} 没有注册（观测网关断了不会有人报警）")

    # ---- 7. 积木契约：七种 kind 与 layout/gaze/source 取值域三方同时存在 ----
    protocol_text = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    composer_text = AQI_COMPOSER.read_text(encoding="utf-8") if AQI_COMPOSER.exists() else ""
    renderer_text = AQI_RENDERER.read_text(encoding="utf-8") if AQI_RENDERER.exists() else ""
    for kind in AQI_BLOCK_KINDS:
        if kind not in protocol_text:
            problems.append(f"PROTOCOL.md §23 缺积木 kind {kind!r}（契约文档与实现漂移）")
        if kind not in composer_text:
            problems.append(f"server/dialogue/aqi_blocks.py 不认 {kind!r}（后端组装不出这种积木）")
        if kind not in renderer_text:
            problems.append(f"web/blocks.js 不渲染 {kind!r}（前端会把这种积木静默丢成空卡）")
    for group_name, values in (("layout", AQI_LAYOUTS), ("gaze", AQI_GAZES),
                               ("source", AQI_SOURCES)):
        for value in values:
            if value not in protocol_text:
                problems.append(f"PROTOCOL.md §23.1 缺 {group_name} 取值 {value!r}")
            if value not in composer_text:
                problems.append(f"aqi_blocks.py 不认 {group_name}={value!r}（信封校验会静默纠正它）")
    # ---- 8. 协议 §24 ui_nav 领航：前后端取值域同步 ----
    check_ui_nav(problems)

    if problems:
        print("FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print(
        f"PASS  {len(faces)} 个情绪标签双向齐全；"
        f"{len(params)} 个参数与中位姿态互为全集；"
        f"{len(cases)} 个消息类型的字段全部对得上；"
        f"{len(routes)} 个 /api 路由与前端引用一致；"
        f"{len(AQI_BLOCK_KINDS)} 种积木 kind 三方认账；"
        f"aqi 信封 layout/gaze/source 取值域文档与组装器一致；"
        f"ui_nav {len(UI_NAV_TARGETS)} 视图 / {len(UI_NAV_SUB_ACTIONS)} 子动作前后端一致"
    )
    if ignored:
        print(f"      （服务端还发了这些，前端未处理，对形象层无影响：{', '.join(ignored)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

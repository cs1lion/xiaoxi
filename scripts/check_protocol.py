"""接缝漂移检查 —— 形象层与后端之间所有会静默失败的耦合。

背景：形象层（web/avatar）和说话层（server）由不同的人同时改，
两边唯一的约定是 WebSocket 消息的形状。约定漂移时不会抛异常，
只会退化成"角色永远没表情"或者"表情卡住不动"，非常难查。

所以与其靠约定，不如把它变成机器可检的。这一脚本查八类漂移：

  1. 情绪标签：orchestrator.EMOTIONS ↔ persona.yaml 的 tts_emotions ↔ avatar.yaml 的 emotions
     三方齐全（2026-10-18 前只比两份 yaml，代码侧加情绪不报错、静默回落到 base）
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
#  - json_object / pcm / ok：OpenAI 兼容响应与二进制帧
#  - _audio_done / _llm_done：orchestrator 内部的流水线控制事件，从不出现在 WS 上
#  - server_vad：发给上游 ASR 的 turn_detection 配置值，不是消息类型
NOT_PROTOCOL = {"json_object", "pcm", "ok", "_audio_done", "_llm_done", "server_vad"}

PARAMS_JS = ROOT / "web" / "avatar" / "params.js"
APP_JS = ROOT / "web" / "app.js"
SERVER_DIR = ROOT / "server"


def all_params() -> set[str]:
    text = PARAMS_JS.read_text(encoding="utf-8")
    block = re.search(r"PARAM_GROUPS\s*=\s*\{(.*?)\n\};", text, re.S)
    if not block:
        raise SystemExit(f"读不到 PARAM_GROUPS，{PARAMS_JS} 结构变了？")
    return set(re.findall(r'"(Param[A-Za-z]+)"', block.group(1)))


def code_emotions() -> set[str]:
    """orchestrator 的 EMOTIONS：模型被要求输出、_EMOTION_TAG 才认的那一份。

    只比两份 yaml 会漏掉真正的事故面：EMOTIONS 是提示词与标签正则的唯一来源。
    往这里加一个情绪而没配 persona/avatar 时，voice_profile() 会静默回落到 base
    （`voices.get(emotion) or ... or voices["base"]`），表情层也退化成默认——
    两头都不报错，只是"这个情绪从来没生效过"。
    """
    source = (SERVER_DIR / "dialogue" / "orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "EMOTIONS"):
            continue
        if isinstance(node.value, ast.Tuple):
            return {item.value for item in node.value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)}
    raise SystemExit("读不到 orchestrator.EMOTIONS，情绪标签的代码来源变了？")


def emitted_types() -> dict[str, set[str]]:
    """扫全部 server/*.py，收集 {"type": "X", ...} 形式的字典字面量。

    `server/gateway/` 是 loopback 模型网关，说的是 Anthropic Messages 协议，
    不是浏览器 WS 契约。把它算进来只会让"前端未处理"的提示越来越吵，
    真正的 WS 漂移反而被淹没。
    """
    found: dict[str, set[str]] = {}
    for path in sorted(SERVER_DIR.rglob("*.py")):
        if SERVER_DIR / "gateway" in path.parents:
            continue
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
    """从 app.js 里抽出前端每个 msg.type 分支读了哪些 msg.<字段>。

    两种派发风格都要认：`switch (msg.type)` 的 case，以及散落在文件里的
    `msg.type === "x"` / `case "x"` 判断。app.js 把 micro_act / mumble 放在
    switch 之前的 if-else 链里处理（协议 §16 三梯度），只看 switch 会把它们
    误列进"前端未处理"——那份清单是给人排查用的，不可靠比没有更糟：
    真正没接的事件会淹在一堆假阳性里。
    """
    text = APP_JS.read_text(encoding="utf-8")
    cases: dict[str, set[str]] = {}
    start = text.find("switch (msg.type)")
    if start >= 0:
        end = text.find("\n};", start)
        body = text[start:end if end > 0 else len(text)]
        marks = [(m.start(), m.group(1)) for m in re.finditer(r'''case ["']([a-z_]+)["']:''', body)]
        for i, (pos, name) in enumerate(marks):
            stop = marks[i + 1][0] if i + 1 < len(marks) else len(body)
            fields = set(re.findall(r"\bmsg\.([a-z_]+)", body[pos:stop]))
            cases.setdefault(name, set()).update(fields)
    # switch 之外的相等判断：这里只记录"这个 type 前端接了"，**不抽字段**。
    # if-else 链的判断点之间隔着别的函数，按位置切会把无关的 msg.X 读法算到
    # 这个 case 头上，制造假阳性。字段级核对只信 switch——那里的边界是确定的。
    for match in re.finditer(r'''msg\.type\s*===\s*["']([a-z_]+)["']''', text):
        cases.setdefault(match.group(1), set())
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


def duplicate_routes() -> dict[tuple[str, str], list[str]]:
    """同一 (方法, 路径) 被注册多次的位置。

    为什么要查：FastAPI 按注册顺序匹配，**先注册的胜出**，后注册的同路径处理函数
    被悄悄遮蔽、从不服务请求。2026-10-18 抓到一次真实事故：`/api/avatar` 上挂了
    两个同名 GET 处理函数，读盘的那版胜出，而 `check_protocol.py` 与服务端代码读的
    都是走 config_hot 快照的 `avatar_cfg()`——热覆盖生效时前端拿到过期契约，
    两头都不报错。`server_routes()` 返回集合，重复项天然被折叠，所以这里单独数。

    必须连方法一起数：同一路径上的 GET 与 DELETE 是正常设计（REST），
    只数路径会误报。
    """
    seen: dict[tuple[str, str], list[str]] = {}
    for path in sorted(SERVER_DIR.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(
                r'@app\.(get|post|patch|delete|put)\(\s*["\']([^"\']+)["\']', text):
            seen.setdefault((match.group(1).upper(), match.group(2)),
                            []).append(str(path.relative_to(ROOT)))
    return {key: where for key, where in seen.items() if len(where) > 1}


INTERNAL_ROUTES = (
    '/internal/telemetry_proxy',
    '/internal/durable/memory/search',
    '/internal/durable/notes/search',
    '/internal/durable/notes/read',
    '/internal/durable/threads/read',
    '/internal/durable/workers',
    '/internal/durable/workers/status',
    '/internal/durable/lease',
    '/internal/durable/orchestrator',
    '/internal/durable/confirmations',
    '/internal/durable/confirm',
    '/internal/durable/dispatch',
    '/internal/durable/task/status',
    '/internal/durable/task/events',
    '/internal/durable/answer',
    '/internal/durable/review',
    '/internal/durable/propose',
    '/internal/durable/authorize',
    '/internal/durable/accept',
    '/internal/durable/reject',
)
"""服务端自用路由：前端不引用，所以第五类检查看不到它们。

观测网关这条最怕被顺手重构掉——改了路径，Worker 的调用会静默 404，
而前端一个字都不会提。所以在这里显式纳管：契约一改，静态检查直接报警。
"""

# 协议 §23 的七种标准积木。契约文档、服务端组装器、前端渲染器三方必须同时
# 认账：少一边不会报错，只会让积木静默变成空卡（后端不产 / 前端不画）。
# §23.3 的 cmd 动词。前端必须处理前五个；服务端只接受 dispatch:* 的两个。
# 两边各一份，这里只做"协议里写了、实现里没有"的比对。
CMD_VERBS = ("ui", "say", "dispatch", "choice")
SERVER_CMD_VERBS = ("dispatch",)

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


DURABLE_ROUTES_TS = ROOT / "agent-runtime" / "src" / "routes.ts"
DURABLE_ROUTES_PY = ROOT / "server" / "assistant" / "adapters" / "durable.py"

# 像素世界客户端（pixel-world/）消费的 WS 事件。它不是 web/app.js，所以第四类
# 检查看不见它——改了事件名，世界会静默收不到督办，而两端都不报错。
# 与 INTERNAL_ROUTES 同一类处理：显式纳管，契约一改静态检查直接报警。
WORLD_WS_EVENTS = ("commission_update",)
PIXEL_WORLD_LEDGER = ROOT / "pixel-world" / "src" / "world" / "ledger.ts"


def check_world_events(problems: list[str]) -> None:
    """§19 变更通知：协议文档、服务端广播、世界客户端三方必须认同一个名字。"""
    if not PIXEL_WORLD_LEDGER.exists():
        return
    protocol_text = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    client_text = PIXEL_WORLD_LEDGER.read_text(encoding="utf-8")
    emitted = emitted_types()
    for name in WORLD_WS_EVENTS:
        if name not in protocol_text:
            problems.append(f"PROTOCOL.md §19 缺事件 {name!r}（契约文档与实现漂移）")
        if name not in emitted:
            problems.append(f"服务端没有任何地方发 {name!r}（世界收不到督办变化）")
        if f"'{name}'" not in client_text and f'"{name}"' not in client_text:
            problems.append(
                f"pixel-world 的 ledger.ts 不认 {name!r}（督办变化会被静默丢弃）"
            )


V2_SOURCE = ROOT / "server" / "contract" / "events_v2.py"


def check_v2_events(problems: list[str]) -> None:
    """§5 事件协议 v2：九类事件必须在协议文档里写全。

    **这一条补的是第四类检查的单向漏洞。** 上面第 4 类只拦「前端读了、后端没发」；
    反方向「后端发了、前端没接」原来只 print 一行不报错（`:545`）。那个设计对
    既有事件是对的（形象层往往有意不关心某些消息），但**对 v2 不能这样**——
    否则新协议永远没人接也能通过，协议门形同虚设。

    所以 v2 的九类事件走**白名单强校验**：
      - 协议文档必须写全（否则契约与实现漂移）
      - 事件常量必须与协议文档逐字一致
      - 明确禁止的旧名/假字段不得出现

    `events_v2.py` 是名字的单一真相源；PROTOCOL.md 必须独立（它给人读）。
    这里比对两边。
    """
    if not V2_SOURCE.exists():
        return
    from server.contract import events_v2 as v2

    protocol_text = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")

    for name in v2.EVENT_TYPES:
        if name not in protocol_text:
            problems.append(
                f"PROTOCOL.md §5 缺 v2 事件 {name!r}（后端发了前端没接，画面与真相不一致）")
        for field in v2.PAYLOAD_FIELDS.get(name, ()):
            # 字段名可能以 `field` 或 "field" 形式出现，只查裸词
            if field not in protocol_text:
                problems.append(
                    f"PROTOCOL.md §5 的 {name} 缺 payload 字段 {field!r}")

    for name in v2.COMMAND_TYPES:
        if name not in protocol_text:
            problems.append(
                f"PROTOCOL.md §5 缺前端命令 {name!r}（按钮点了没反应）")

    # 明确不加的东西：有人手滑加回来要红
    for name in v2.FORBIDDEN_EVENT_TYPES:
        if name in v2.EVENT_TYPES:
            problems.append(
                f"events_v2 里出现了禁止的事件 {name!r}"
                "（TASK_SNAPSHOT 会造第二个真相源；VOICE_STATE 已改名 AGENT_PRESENCE）")
    for field in v2.FORBIDDEN_PAYLOAD_FIELDS:
        if field in v2.PAYLOAD_FIELDS.get(v2.TOOL_EXECUTION, ()):
            problems.append(
                f"TOOL_EXECUTION 的 payload 不得含 {field!r}"
                "（假进度条破坏「画面等于真实状态」，用 steps_done/steps_total）")

    # AGENT_PRESENCE 的四态必须写进协议，且与既有词汇对齐
    for state in v2.AGENT_PRESENCE_STATES:
        if state not in protocol_text:
            problems.append(
                f"PROTOCOL.md §5 的 AGENT_PRESENCE 缺状态 {state!r}")

    # 投递保障必须覆盖全部九类（漏一个等于没定保障）
    covered = set(v2.AT_LEAST_ONCE) | set(v2.AT_MOST_ONCE)
    missing = set(v2.EVENT_TYPES) - covered
    if missing:
        problems.append(f"events_v2 有事件没定投递保障：{sorted(missing)}")
    overlap = set(v2.AT_LEAST_ONCE) & set(v2.AT_MOST_ONCE)
    if overlap:
        problems.append(f"events_v2 的事件同时被定为两种保障：{sorted(overlap)}")


def check_durable_routes(problems: list[str]) -> None:
    """慢环 sidecar 的路由表：Python 适配器与 Node 侧必须逐项一致。

    sidecar 是独立进程，本脚本的路由扫描看不见它；两边各写一份常量，这里做
    机械比对。agent-runtime 不存在时跳过，部分检出不该让门禁误报。
    """
    if not DURABLE_ROUTES_TS.exists() or not DURABLE_ROUTES_PY.exists():
        return
    ts_match = re.search(r"SIDECAR_ROUTES\s*=\s*\[([^\]]*)\]",
                         DURABLE_ROUTES_TS.read_text(encoding="utf-8"))
    py_match = re.search(r"SIDECAR_ROUTES\s*=\s*\(([^)]*)\)",
                         DURABLE_ROUTES_PY.read_text(encoding="utf-8"))
    if not ts_match or not py_match:
        problems.append("sidecar 路由表缺失：routes.ts 或 durable.py 里找不到 SIDECAR_ROUTES")
        return
    ts_routes = set(re.findall(r'["\']([^"\']+)["\']', ts_match.group(1)))
    py_routes = set(re.findall(r'["\']([^"\']+)["\']', py_match.group(1)))
    for name in sorted(py_routes - ts_routes):
        problems.append(f"durable 适配器用了 sidecar 没有的路由 {name}")
    for name in sorted(ts_routes - py_routes):
        problems.append(f"sidecar 路由 {name} 没登记进 durable 适配器（改名只改了一边）")


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

    # ---- 1. 情绪标签三方齐全 ----
    voices = {k for k in persona["tts_emotions"] if k != "base"}
    faces = {k for k in avatar["emotions"] if k != "base"}
    spoken = code_emotions() - {"base"}
    for label, left, right in (("avatar.yaml", faces, voices),
                               ("persona.yaml", voices, faces),
                               ("orchestrator.EMOTIONS", spoken, voices | faces)):
        for missing in sorted(left - right):
            problems.append(f"{label} 有情绪 {missing!r}，但另一侧没配（会静默回落到 base）")
        for missing in sorted(right - left):
            if label == "orchestrator.EMOTIONS":
                # persona/avatar 多配一个不发给模型的情绪只是死配置，不是漂移事故。
                continue
            problems.append(f"{label} 少了情绪 {missing!r}，模型永远不会输出它")

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
    # ---- 7b. §23.3 的 cmd 动词：协议、前端、服务端三方认账 ----
    # `choice:<index>` 曾经只写在协议里：前端没有这个分支，点击落进"未知业务
    # 动词：忽略"，胶囊点了没反应。动词表是前后端的共同白名单，必须机械比对。
    app_text = APP_JS.read_text(encoding="utf-8")
    for verb in CMD_VERBS:
        if f"`{verb}:" not in protocol_text and f"`{verb}:<" not in protocol_text:
            problems.append(f"PROTOCOL.md §23.3 缺 cmd 动词 {verb!r}")
        if f'"{verb}"' not in app_text and f"'{verb}'" not in app_text:
            problems.append(f"web/app.js 不处理 cmd 动词 {verb!r}（胶囊/按钮点了没反应）")
    for verb in SERVER_CMD_VERBS:
        if f"`{verb}:" not in protocol_text:
            problems.append(f"PROTOCOL.md §23.3 缺服务端业务动词 {verb!r}")

    # ---- 8. 协议 §24 ui_nav 领航：前后端取值域同步 ----
    check_ui_nav(problems)
    # ---- 9. 慢环 sidecar 路由：Python 适配器与 Node 侧同步 ----
    check_durable_routes(problems)
    # ---- 11. 像素世界事件：协议、服务端、世界客户端三方认账 ----
    check_world_events(problems)
    # ---- 12. §5 事件协议 v2：九类事件必须写进协议（补第四类的单向漏洞） ----
    check_v2_events(problems)
    # ---- 10. 同一 (方法, 路径) 只能注册一次：重复注册会让后一个处理函数永不服务 ----
    for (method, route), where in sorted(duplicate_routes().items()):
        problems.append(
            f"路由 {method} {route!r} 被注册了 {len(where)} 次（{', '.join(where)}）："
            "FastAPI 先注册者胜出，后注册的那个从不服务请求")

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

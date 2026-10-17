"""动态积木（协议 §23）：七种标准积木的构造/校验、快环直出组装、慢环流式标签过滤。

两条底线，和本仓库其它模块同标准：

1. **积木只装真实数据。** 没有真实台账/笔记/星图数据时不发对应积木，
   不用占位卡充数——空列表比假卡片诚实。
2. **标签内容绝不进语音。** `<aqi-block>` 内的 JSON 由 AqiBlockFilter 在
   字符级截留：跨 delta 完整缓冲、未闭合整体扣留、解析失败整体丢弃，
   文字与语音路径永远看不到它。

纯逻辑、零 IO、零模型调用，便于 node/python 双侧单测。
"""
from __future__ import annotations

import json
import re
from typing import Any

AQI_BLOCK_KINDS = ("choice_pills", "spotlight_card", "alert_card",
                   "swimlane_strip", "metric_flame", "note_snippet", "memo_note")
# 协议 §19 的督办状态集合（含归档）；swimlane 的 status 只认这些值。
COMMISSION_STATUSES = ("drafting", "briefing", "running", "blocked",
                       "auditing", "ready_to_report", "done")
LAYOUTS = ("stage_holo", "drawer_ops")
NAV_TARGETS = ("ops", "notes", "threads", "starmap")
GAZE_TARGETS = ("holo_right", "drawer", "user")
SOURCES = ("chat", "executive", "ambient", "loop")
TONES = ("gold", "red", "mint", "neutral")
LEVELS = ("warn", "critical")

_MAX_QUESTION = 200
_MAX_TITLE = 80
_MAX_DETAIL = 400
_MAX_LABEL = 40
_MAX_CMD = 200
_MAX_VALUE = 400
_MAX_REF = 200
# 单条消息的积木上限，必须与前端 Holo-Deck 的 MAX_CARDS 一致：后端多发的那几张
# 会被前端按"最旧先回收"静默丢掉——那是一场没有报错的降级，不如在出口就截住。
_MAX_BLOCKS = 6
_MAX_META_PAIRS = 6

AQI_BLOCK_INSTRUCTION = (
    "输出格式补充：需要展示状态、选项、卡片数据时，在回复末尾附 "
    "<aqi-block>{\"kind\":\"...\"}</aqi-block> 标签，每个标签一个 JSON。\n"
    "口头部分仍然只有一两句自然口语：绝不口播长列表、枯燥数据或大段 JSON；"
    "那些内容属于积木，不属于嘴。\n"
    "可用 kind：choice_pills（请示/消歧胶囊）、spotlight_card（成果验收/邀功）、"
    "alert_card（熔断/预警）、swimlane_strip（多 Agent 进度）、"
    "metric_flame（Token/延迟）、note_snippet（笔记摘录）。\n"
    "JSON 只填真实信息、字段从简；标签不会说出口，也不要向用户提“积木”二字。"
    "没有可展示的真实数据时不要发标签。\n"
)


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _clean_actions(actions: Any) -> list[dict[str, str]]:
    """交互按钮白名单校验：label/cmd 都必须是短字符串，最多 4 个。"""
    if not isinstance(actions, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in actions[:_MAX_BLOCKS]:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"), _MAX_LABEL)
        cmd = _text(item.get("cmd"), _MAX_CMD)
        if not label or not cmd or ":" not in cmd:
            continue
        if {"label": label, "cmd": cmd} not in cleaned:
            cleaned.append({"label": label, "cmd": cmd})
    return cleaned[:4]


def _clean_meta(meta: Any) -> dict[str, Any]:
    """memo_note 的键值小字：至多 6 对，键 ≤40 字符，值限短文本或数字。"""
    if not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in list(meta.items())[:_MAX_META_PAIRS]:
        label = _text(key, 40)
        if not label:
            continue
        if isinstance(item, bool) or not isinstance(item, (int, float, str)):
            continue
        cleaned[label] = item if isinstance(item, (int, float)) else _text(item, 80)
    return cleaned


def _block(kind: str, **fields: Any) -> dict[str, Any]:
    """组装一个积木：actions 与通用字段（id/subtitle/meta/tone）在这里统一
    白名单校验后注入；构造器只负责自己的专用字段。"""
    actions = _clean_actions(fields.pop("actions", None))
    block: dict[str, Any] = {"kind": kind}
    target_id = _text(fields.pop("id", None), 100)
    if target_id:
        block["id"] = target_id
    subtitle = _text(fields.pop("subtitle", None), _MAX_TITLE)
    if subtitle:
        block["subtitle"] = subtitle
    meta = _clean_meta(fields.pop("meta", None))
    if meta:
        block["meta"] = meta
    tone = _text(fields.pop("tone", None), 20)
    if tone in TONES:
        block["tone"] = tone
    block.update(fields)
    if actions:
        block["actions"] = actions
    return block


# ---------- 七种标准积木构造器（返回 None 表示真实数据不足，调用方闭嘴） ----------

def choice_pills(question: Any, options: Any, *, actions: Any = None,
                 **general: Any) -> dict[str, Any] | None:
    question = _text(question, _MAX_QUESTION)
    if not question or not isinstance(options, list):
        return None
    cleaned = []
    for item in options[:6]:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"), _MAX_LABEL)
        cmd = _text(item.get("cmd"), _MAX_CMD)
        if not label or not cmd or ":" not in cmd:
            continue
        if {"label": label, "cmd": cmd} not in cleaned:
            cleaned.append({"label": label, "cmd": cmd})
    if not cleaned:
        return None
    return _block("choice_pills", question=question, options=cleaned, actions=actions, **general)


def spotlight_card(title: Any, *, summary: Any = "", intercepted: Any = "",
                   artifacts: Any = None, actions: Any = None,
                   **general: Any) -> dict[str, Any] | None:
    title = _text(title, _MAX_TITLE)
    if not title:
        return None
    cleaned = []
    for item in (artifacts if isinstance(artifacts, list) else [])[:12]:
        if not isinstance(item, dict):
            continue
        ref = _text(item.get("ref"), _MAX_REF)
        if not ref:
            continue
        entry = {"kind": _text(item.get("kind"), 40) or "成果", "ref": ref}
        preview = _text(item.get("preview"), _MAX_VALUE)
        if preview:
            entry["preview"] = preview
        cleaned.append(entry)
    block = _block("spotlight_card", title=title, actions=actions, **general)
    if _text(summary, _MAX_VALUE):
        block["summary"] = _text(summary, _MAX_VALUE)
    if _text(intercepted, _MAX_VALUE):
        block["intercepted"] = _text(intercepted, _MAX_VALUE)
    if cleaned:
        block["artifacts"] = cleaned
    return block


def alert_card(title: Any, *, detail: Any = "", level: Any = "warn",
               actions: Any = None, **general: Any) -> dict[str, Any] | None:
    title = _text(title, _MAX_TITLE)
    if not title:
        return None
    block = _block("alert_card", title=title, level=level if level in LEVELS else "warn",
                   actions=actions, **general)
    if _text(detail, _MAX_DETAIL):
        block["detail"] = _text(detail, _MAX_DETAIL)
    return block


def swimlane_strip(lanes: Any, *, actions: Any = None, **general: Any) -> dict[str, Any] | None:
    if not isinstance(lanes, list):
        return None
    cleaned = []
    for item in lanes[:12]:
        if not isinstance(item, dict):
            continue
        agent_id = _text(item.get("agent_id"), 60)
        if not agent_id:
            continue
        status = _text(item.get("status"), 40)
        lane = {"agent_id": agent_id,
                "status": status if status in COMMISSION_STATUSES else "running"}
        name = _text(item.get("agent_name"), 60)
        if name:
            lane["agent_name"] = name
        title = _text(item.get("title"), _MAX_TITLE)
        if title:
            lane["title"] = title
        if "step_idx" in item:
            lane["step_idx"] = _int(item.get("step_idx"))
        cleaned.append(lane)
    if not cleaned:
        return None
    return _block("swimlane_strip", lanes=cleaned, actions=actions, **general)


def metric_flame(metrics: Any, *, note: Any = "", actions: Any = None,
                 **general: Any) -> dict[str, Any] | None:
    if not isinstance(metrics, list):
        return None
    cleaned = []
    for item in metrics[:8]:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"), _MAX_LABEL)
        value = item.get("value")
        if not label or value is None or isinstance(value, (bool, dict, list)):
            continue
        if not isinstance(value, (int, float, str)):
            continue
        cleaned.append({"label": label, "value": value})
    if not cleaned:
        return None
    block = _block("metric_flame", metrics=cleaned, actions=actions, **general)
    if _text(note, _MAX_DETAIL):
        block["note"] = _text(note, _MAX_DETAIL)
    return block


def note_snippet(text: Any, *, title: Any = "", ref: Any = "",
                 actions: Any = None, **general: Any) -> dict[str, Any] | None:
    text = _text(text, _MAX_VALUE)
    if not text:
        return None
    block = _block("note_snippet", text=text, actions=actions, **general)
    if _text(title, _MAX_TITLE):
        block["title"] = _text(title, _MAX_TITLE)
    if _text(ref, _MAX_REF):
        block["ref"] = _text(ref, _MAX_REF)
    return block


def memo_note(title: Any, body: Any, *, meta: Any = None, actions: Any = None,
              **general: Any) -> dict[str, Any] | None:
    """平静期安心便签：没有待办时告诉她"手头清净"，附真实计数小字。"""
    title = _text(title, _MAX_TITLE)
    body = _text(body, _MAX_VALUE)
    if not title or not body:
        return None
    cleaned = _clean_meta(meta)
    block = _block("memo_note", title=title, body=body, actions=actions, **general)
    if cleaned:
        block["meta"] = cleaned
    return block


_BUILDERS = {
    "choice_pills": choice_pills,
    "spotlight_card": spotlight_card,
    "alert_card": alert_card,
    "swimlane_strip": swimlane_strip,
    "metric_flame": metric_flame,
    "note_snippet": note_snippet,
    "memo_note": memo_note,
}


# ---------- 消息信封 ----------

def aqi_blocks_message(blocks: Any, *, layout: Any = "stage_holo",
                       nav: Any = None, gaze: Any = None,
                       source: Any = "chat") -> dict[str, Any]:
    """轮次事件信封。五个顶层键永远在场（nav/gaze 可为 None），
    这样 check_protocol.py 第 4 条能把前端读的字段和后端发的字段对上。"""
    cleaned = [block for block in (blocks if isinstance(blocks, list) else [])
               if isinstance(block, dict) and block.get("kind") in AQI_BLOCK_KINDS]
    return {
        "type": "aqi_blocks",
        "source": source if source in SOURCES else "chat",
        "layout": layout if layout in LAYOUTS else "stage_holo",
        "nav": nav if nav in NAV_TARGETS else None,
        "gaze": gaze if gaze in GAZE_TARGETS else None,
        "blocks": cleaned[:_MAX_BLOCKS],
    }


# ---------- 快环直出组装（只吃真实台账/战况/笔记数据） ----------

# 记忆 kind 的人话标题（闲聊卡用）；星图/笔记侧另有呈现，这里只取一个短标签。
_MEMORY_KIND_LABELS = {"fact": "你提过的事", "episode": "咱们聊过的",
                       "todo": "你交代的", "self_state": "她的自记"}


def compose_memory_card(record: Any) -> dict[str, Any] | None:
    """闲聊兜底（协议 §23.4）：本轮上下文真实检索到的高置信记忆 → note_snippet。

    只认 stated（用户明确说过 / 手动保存的）：推测与不确定的记忆不进卡——
    那张卡是"她记着了"的凭证，拿推测当凭证就是撒谎。动作指向星图，
    老板点一下就能看这颗记忆在脑内宇宙里的位置。
    """
    if not isinstance(record, dict):
        return None
    value = _text(record.get("value"), _MAX_VALUE)
    identifier = record.get("id")
    if not value or record.get("source") != "stated":
        return None
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        return None
    title = _MEMORY_KIND_LABELS.get(_text(record.get("kind"), 40), "她记着呢")
    return note_snippet(value, title=title, ref=f"memory:{identifier}",
                        actions=[{"label": "去星图看看", "cmd": "ui:starmap"}])


def compose_spinning_card(alerts: Any) -> dict[str, Any] | None:
    """鬼打墙熔断（协议 §23.6）：真实 guard 告警 → 红色 critical alert_card。

    告警来自 telemetry SpinningGuard（同一 commission 内步骤签名连续重复跳闸），
    commission_id 真实存在才给"换将"按钮——没有靶子的换将只是句漂亮话。
    """
    if not isinstance(alerts, list):
        return None
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        commission_id = _text(alert.get("commission_id"), 100)
        agent = _text(alert.get("agent_id"), 60) or "未知 Agent"
        actions: list[dict[str, str]] = []
        if commission_id:
            actions.append({"label": "🔄 换将", "cmd": f"dispatch:reassign:{commission_id}"})
        actions.append({"label": "🔌 暂停", "cmd": "ui:drawer:ops"})
        return alert_card(f"鬼打墙熔断 · {agent}",
                          detail=f"同一步骤签名连续 {_int(alert.get('repeats'))} 次，闸已拉。",
                          level="critical", actions=actions, tone="red",
                          id=commission_id or None)
    return None


def compose_delivery_card(commission: Any) -> dict[str, Any] | None:
    """初审通过待验收（协议 §23.6）：金色 spotlight_card，按钮直通预览与收工。"""
    if not isinstance(commission, dict):
        return None
    card = compose_accept_card(commission)
    if card is None:
        return None
    commission_id = _text(commission.get("id"), 100)
    actions: list[dict[str, str]] = []
    if commission_id:
        actions.append({"label": "✨ 预览", "cmd": f"ui:preview:{commission_id}"})
        actions.append({"label": "✅ 收工", "cmd": f"dispatch:accept:{commission_id}"})
    card["tone"] = "gold"
    card["actions"] = _clean_actions(actions)
    if commission_id:
        card["id"] = commission_id
    return card


def compose_memo_note(*, running: Any = 0, pending: Any = 0,
                      done_recent: Any = 0) -> dict[str, Any] | None:
    """平静期安心便签（协议 §23.6 / 路径 D）：真实计数换一句安心话。

    只在三类计数全为 0（或调用方明确要平静期文案）时才有意义——
    有单子在跑的时候递"手头清净"，那是诈骗。
    """
    counts = {"在跑": _int(running), "待验收": _int(pending), "近期收工": _int(done_recent)}
    if any(counts.values()):
        return None
    return memo_note("手头清净", "该盯的都收了工，该答的你都点了头。我就在这儿盯着，有动静再叫你。",
                     meta={"在跑": 0, "待验收": 0})


def compose_choice(pending: Any) -> dict[str, Any] | None:
    """多任务消歧：每单一颗胶囊，cmd 直接收工那一单（真实 commission_id）。"""
    if not isinstance(pending, list) or len(pending) < 2:
        return None
    options = [{"label": _text(item.get("title"), _MAX_LABEL) or "未命名督办",
                "cmd": f"dispatch:accept:{_text(item.get('id'), 100)}"}
               for item in pending[:6] if isinstance(item, dict) and item.get("id")]
    question = f"你手上等着验收的有 {len(pending)} 单，说个名字我就知道是哪一单。"
    return choice_pills(question, options)


def compose_accept_card(commission: Any) -> dict[str, Any] | None:
    """验收邀功卡：三句话交付验收卡的真实内容（§19 delivery）。"""
    if not isinstance(commission, dict):
        return None
    delivery = commission.get("delivery") if isinstance(commission.get("delivery"), dict) else {}
    title = _text(commission.get("title"), _MAX_TITLE) or "未命名督办"
    return spotlight_card(title, summary=delivery.get("summary"),
                          intercepted=delivery.get("intercepted"),
                          artifacts=delivery.get("artifacts"),
                          actions=[{"label": "去战况抽屉核对", "cmd": "ui:drawer:ops"}])


def compose_progress_strip(commissions: Any) -> dict[str, Any] | None:
    """并行干活流光条：在跑督办的 real lanes（agent/标题/步骤/状态）。"""
    if not isinstance(commissions, list):
        return None
    lanes = [{"agent_id": _text(item.get("agent_id"), 60) or "未选将",
              "agent_name": _text(item.get("agent_name"), 60),
              "title": _text(item.get("title"), _MAX_TITLE),
              "step_idx": _int(item.get("step_idx")),
              "status": _text(item.get("status"), 40)}
             for item in commissions[:12] if isinstance(item, dict)]
    return swimlane_strip(lanes, actions=[{"label": "打开战况", "cmd": "ui:drawer:ops"}])


def compose_metric_flame(summary: Any, *, alerts: Any = None) -> list[dict[str, Any]]:
    """API 战况查账：真实 span 汇总 + 熔断告警（没有 span 时返回空列表）。"""
    if not isinstance(summary, dict) or not summary.get("calls"):
        return []
    blocks: list[dict[str, Any]] = []
    metrics = [
        {"label": "调用", "value": _int(summary.get("calls"))},
        {"label": "Token 合计", "value": _int(summary.get("total_tokens"))},
        {"label": "成功率", "value": summary.get("success_rate")},
        {"label": "首包均值 ms", "value": summary.get("avg_ttft_ms")},
    ]
    flame = metric_flame(metrics, actions=[{"label": "看战况抽屉", "cmd": "ui:drawer:ops"}])
    if flame is not None:
        blocks.append(flame)
    for alert in (alerts if isinstance(alerts, list) else [])[:2]:
        if not isinstance(alert, dict):
            continue
        card = alert_card(f"鬼打墙熔断 · {_text(alert.get('agent_id'), 60) or '未知 Agent'}",
                          detail=f"同一步骤签名连续 {_int(alert.get('repeats'))} 次，闸已拉。",
                          level="critical",
                          actions=[{"label": "去处理", "cmd": "ui:drawer:ops"}])
        if card is not None:
            blocks.append(card)
    return blocks


# ---------- 慢环流式标签过滤 ----------

class AqiBlockFilter:
    """字符级截留 `<aqi-block>{...}</aqi-block>`，模式与 MotionMetadataFilter 对齐。

    返回 ("text", str) / ("aqi", dict|None) 两类 token，保持源序。
    JSON 字符串里可能出现 `</aqi-block>` 字样，因此只在引号外找闭合签；
    未闭合的标签在流末由 finish() 整体扣留——不发声、不显示、不入库。
    """

    PREFIX = "<aqi-block>"
    CLOSE = "</aqi-block>"
    MAX_BLOCK_CHARS = 8192
    # 溢出后只保留一个闭合签宽度的滚动尾窗：闭合签一旦在引号外出现，
    # 整块作废丢弃、立刻恢复正常文本流（标签后续的文字必须能发声）。
    TAIL_CHARS = len(CLOSE)

    def __init__(self) -> None:
        self._candidate = ""
        self._reserved = False
        self._body = ""
        self._tail = ""
        self._overflow = False
        self._quoted = False
        self._escaped = False

    def feed(self, char: str) -> list[tuple[str, Any]]:
        if self._reserved:
            if self._overflow:
                self._tail = (self._tail + char)[-self.TAIL_CHARS:]
            else:
                self._body += char
                if len(self._body) > self.MAX_BLOCK_CHARS:
                    # 超限作废：正文清零，但把尾部窗口搬进 _tail 续监视闭合签，
                    # 防止闭合签恰好横跨溢出边界时整条文本流被吞到流末。
                    self._tail = self._body[-self.TAIL_CHARS:]
                    self._body = ""
                    self._overflow = True
            if self._quoted:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._quoted = False
            elif char == '"':
                self._quoted = True
            # 闭合签只在 JSON 引号外生效：字符串里出现 "</aqi-block>" 字样不算闭合。
            if not self._quoted:
                watched = self._tail if self._overflow else self._body
                if watched.endswith(self.CLOSE):
                    raw = "" if self._overflow else watched[:-len(self.CLOSE)]
                    value = None if self._overflow else _loads_block(raw)
                    self._reserved = False
                    self._body = ""
                    self._tail = ""
                    self._overflow = False
                    self._quoted = self._escaped = False
                    return [("aqi", value)]
            return []
        if self._candidate:
            candidate = self._candidate + char
            if self.PREFIX.startswith(candidate):
                self._candidate = candidate
                if candidate == self.PREFIX:
                    self._candidate = ""
                    self._reserved = True
                    self._quoted = self._escaped = False
                    self._body = ""
                    self._tail = ""
                    self._overflow = False
                return []
            old = self._candidate
            self._candidate = ""
            return [("text", old)] + self.feed(char)
        if char == "<":
            self._candidate = char
            return []
        return [("text", char)]

    def finish(self) -> list[tuple[str, Any]]:
        # 只可能是半个开签（精确前缀）；闭合中或已超限的块整体扣留。
        tail = self._candidate
        self._candidate = ""
        self._reserved = False
        self._body = ""
        self._tail = ""
        self._overflow = self._quoted = self._escaped = False
        return [("text", tail)] if tail else []


def _loads_block(raw: str) -> dict[str, Any] | None:
    """解析并校验一个积木 JSON；失败返回 None（整体丢弃，不影响文字语音）。"""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind not in _BUILDERS:
        return None
    layout = value.get("layout")
    layout = layout if layout in LAYOUTS else None
    # actions 是各构造器的 keyword-only 参数，必须随 fields 一起传进去，
    # 由 _clean_actions 做白名单校验；layout 提升为消息级后块内忽略。
    fields = {key: item for key, item in value.items() if key not in ("kind", "layout")}
    try:
        block = _BUILDERS[kind](**fields)
    except TypeError:
        return None
    if block is not None and layout:
        # 块级 layout 覆盖提升到消息级（协议 §23.4），渲染层忽略块内该键。
        block["layout"] = layout
    return block


def parse_aqi_block(raw: str) -> dict[str, Any] | None:
    """非流式路径的单块解析（测试与 REST 复用）。"""
    return _loads_block(raw)


def strip_aqi_blocks(text: str) -> str:
    """剥掉完整/未闭合的 `<aqi-block>` 标签，只留口头文本（非流式 respond 路径）。"""
    if not isinstance(text, str) or "<aqi-block>" not in text:
        return text if isinstance(text, str) else ""
    parser = AqiBlockFilter()
    result: list[str] = []
    for char in text:
        result.extend(value for kind, value in parser.feed(char) if kind == "text")
    result.extend(value for kind, value in parser.finish() if kind == "text")
    return "".join(result)


def block_layout(block: dict[str, Any] | None) -> str | None:
    """块级 layout 覆盖（模型可在标签 JSON 内带），非法值忽略。"""
    if not isinstance(block, dict):
        return None
    layout = block.get("layout")
    return layout if layout in LAYOUTS else None


__all__ = ["AQI_BLOCK_INSTRUCTION", "AQI_BLOCK_KINDS", "AqiBlockFilter", "alert_card",
           "aqi_blocks_message", "block_layout", "choice_pills", "compose_accept_card",
           "compose_choice", "compose_delivery_card", "compose_memo_note",
           "compose_memory_card", "compose_metric_flame", "compose_progress_strip",
           "compose_spinning_card", "metric_flame", "memo_note", "note_snippet",
           "parse_aqi_block", "spotlight_card", "strip_aqi_blocks", "swimlane_strip"]

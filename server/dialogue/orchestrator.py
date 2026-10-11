from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import pacing_cfg
from ..llm import llm
from ..memory.manager import manager
from ..persona import build_system_prompt, demo_turns

REFUSE_KEYWORDS = pacing_cfg()["cooldown"]["on_refuse_keywords"]

_MD = re.compile(r"[*_`#>]|^\s*[-+]\s", re.M)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
_STAGE = re.compile(r"[（(【\[].{0,20}?[）)】\]]")
_ELLIPSIS = re.compile(r"\.{3,}|…{1,}")


@dataclass
class TurnResult:
    text: str
    emotion: str = "base"
    memories_used: list[int] = field(default_factory=list)
    saved: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    proactive: bool = False


def sanitize(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = _MD.sub("", text)
    text = _STAGE.sub("", text)
    text = _ELLIPSIS.sub("……", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def detect_emotion(text: str) -> str:
    """Cheap heuristic that maps tone -> voice profile; avoids a second LLM call."""
    if any(k in text for k in ("对不起", "抱歉", "我不该", "收敛")):
        return "收敛"
    if any(k in text for k in ("正经", "认真说", "因为", "其实原理")):
        return "认真"
    if any(k in text for k in ("哈", "笑", "吐槽", "又", "你看看你")) and "？" not in text[:4]:
        return "调侃"
    if any(k in text for k in ("我厉害吧", "怎么样", "我说得对")):
        return "得意"
    if any(k in text for k in ("哼", "才不要", "不公平", "懒得理", "不稀罕")):
        return "轻微不服"
    if any(k in text for k in ("不想理你", "你自己想", "随便你", "哦。")):
        return "变小气"
    return "base"


class Orchestrator:
    def __init__(self) -> None:
        self.pacing = pacing_cfg()
        self.state: dict[str, Any] = {
            "current_state": "",
            "user_state_note": "",
            "proactivity_enabled": False,
            "proactive_suppressed_until": 0.0,
            "last_proactive_at": 0.0,
            "proactive_count_today": 0,
            "day_stamp": time.strftime("%Y-%m-%d"),
            "turn_count": 0,
            "recent_reopens": 0,
        }

    # ---------------- public API ----------------
    async def respond(
        self,
        *,
        session_id: str,
        user_text: str,
        mode: str = "text",
        history_turns: int = 10,
    ) -> TurnResult:
        started = time.perf_counter()
        user_text = user_text.strip()
        if not user_text:
            return TurnResult(text="", latency_ms=0)

        for keyword in REFUSE_KEYWORDS:
            if keyword in user_text:
                self.state["proactive_suppressed_until"] = time.time() + float(
                    self.pacing["cooldown"]["suppress_proactivity_seconds"]
                )
                self.state["user_state_note"] = "用户刚表示不想聊，语气要收着，不要追问"

        recent = store_recent(session_id, history_turns)
        memory_block, used_ids = ("", [])
        if manager.enabled:
            memory_block, used_ids = manager.retriever.build_context(user_text)

        system = build_system_prompt(
            memory_block=memory_block,
            current_state=self.state["current_state"],
            user_state_note=self.state["user_state_note"],
            proactivity=self._proactive_note(),
        )
        demo = [] if recent else demo_turns()
        messages = [{"role": "system", "content": system}] + demo + recent + [{"role": "user", "content": user_text}]

        raw = await llm.complete(
            messages,
            max_tokens=int(self.pacing["length"]["hard_limit_chars"] // 2 + 120),
        )
        text = self._postprocess(raw, user_text=user_text)
        emotion = detect_emotion(text)

        saved: list[dict[str, Any]] = []
        if manager.enabled:
            memories = await manager.observe_turn(
                session_id=session_id,
                user_text=user_text,
                assistant_text=text,
                recent_context="\n".join(f"{m['role']}:{m['text']}" for m in recent[-4:]),
            )
            saved = [m.to_dict() for m in memories]

        self.state["turn_count"] += 1
        self._maybe_update_state(text, user_text)
        store_message(session_id, "user", user_text, mode)
        store_message(session_id, "assistant", text, mode)

        return TurnResult(
            text=text,
            emotion=emotion,
            memories_used=used_ids,
            saved=saved,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def proactive_turn(self, *, session_id: str, idle_seconds: float) -> TurnResult | None:
        """Only fires when the user explicitly enabled proactivity."""
        cfg = self.pacing["proactivity"]
        now = time.time()
        if not cfg.get("default_enabled") and not self.state["proactivity_enabled"]:
            return None
        if now < float(self.state["proactive_suppressed_until"]):
            return None
        if now - float(self.state["last_proactive_at"]) < float(cfg["min_interval_seconds"]):
            return None
        if idle_seconds < float(cfg["only_when_user_recently_idle_seconds"]):
            return None
        if self.state["proactive_count_today"] >= int(cfg["max_per_day"]):
            return None

        todos, _ = manager.retriever.search(
            "", kinds=("todo", "episode"), top_k=3, include_done=False
        )
        if not todos or not any(cfg.get(flag, False) for flag in ("allow_remind_todo", "allow_continue_episode")):
            return None

        system = build_system_prompt(
            memory_block=manager.context_for("待办 提醒"),
            proactivity="现在是用户允许的主动发言时段。你可以自然地起个头，一句话，别追问。",
        )
        raw = await llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": "（用户暂时没说话，你可以主动说一句）"}],
            max_tokens=120,
        )
        text = sanitize(raw)
        if not text:
            return None
        self.state["last_proactive_at"] = now
        self.state["proactive_count_today"] += 1
        store_message(session_id, "assistant", text, "proactive")
        return TurnResult(text=text, emotion=detect_emotion(text), proactive=True)

    def set_proactivity(self, enabled: bool) -> None:
        self.state["proactivity_enabled"] = enabled
        if enabled:
            self.state["proactive_suppressed_until"] = 0.0

    # ---------------- internals ----------------
    def _proactive_note(self) -> str:
        if not self.state["proactivity_enabled"]:
            return "用户没有授权你主动开口。所以只回应他实际说的话。"
        return "用户允许你偶尔主动开口，但不要每次都主动，也不要连续追问。"

    def _postprocess(self, raw: str, *, user_text: str) -> str:
        text = sanitize(raw)
        hard = int(self.pacing["length"]["hard_limit_chars"])
        soft = int(self.pacing["length"]["soft_limit_chars"])
        target_max = self._length_target(user_text)
        if len(text) > target_max:
            text = text[: target_max - 1].rstrip() + "。"
        if len(text) > hard:
            text = text[: hard - 1].rstrip() + "。"
        if len(text) > soft and "详细" not in user_text and "为什么" not in user_text:
            sentences = re.split(r"(?<=[。！？!?])", text)
            kept, total = [], 0
            for sentence in sentences:
                if total + len(sentence) > soft and kept:
                    break
                kept.append(sentence)
                total += len(sentence)
            trimmed = "".join(kept).strip()
            if trimmed:
                text = trimmed
        if self._question_ratio_exceeded():
            text = re.sub(r"[？?]+\s*$", "", text).strip()
        return text

    def _length_target(self, user_text: str) -> int:
        length = self.pacing["length"]
        if any(k in user_text for k in ("详细", "为什么", "怎么做", "解释", "展开")):
            return int(length["medium_max_chars"])
        return int(length["short_max_chars"]) if length["default"] == "short" else int(length["medium_max_chars"])

    def _question_ratio_exceeded(self) -> bool:
        cfg = self.pacing["questions"]
        ratio_limit = float(cfg["max_ratio"])
        window = self.state.get("_recent_assistant", [])
        if len(window) < 3:
            return False
        hits = sum(1 for line in window if "？" in line or "?" in line)
        return hits / len(window) >= ratio_limit

    def _maybe_update_state(self, assistant_text: str, user_text: str) -> None:
        recent = self.state.setdefault("_recent_assistant", [])
        recent.append(assistant_text)
        del recent[:-6]
        if any(k in user_text for k in ("难过", "累", "烦", "emo", "崩溃", "压力")):
            self.state["current_state"] = "收敛"
            self.state["user_state_note"] = "用户情绪低落：少调侃，简短一点，多听"
        elif any(k in user_text for k in ("没事", "好了", "开心", "搞定")):
            self.state["current_state"] = ""
            self.state["user_state_note"] = ""
        day = time.strftime("%Y-%m-%d")
        if day != self.state["day_stamp"]:
            self.state["day_stamp"] = day
            self.state["proactive_count_today"] = 0

    def snapshot(self) -> dict[str, Any]:
        return {k: v for k, v in self.state.items() if not k.startswith("_")}


def store_message(session_id: str, role: str, text: str, mode: str = "text") -> None:
    from ..memory.store import store

    store.add_message(session_id, role, text, mode)


def store_recent(session_id: str, limit: int) -> list[dict[str, str]]:
    from ..memory.store import store

    if not session_id or limit <= 0:
        return []
    rows = store.recent_messages(session_id, limit=limit * 2)
    return [{"role": r["role"], "content": r["text"]} for r in rows][-limit * 2 :]


orchestrator = Orchestrator()
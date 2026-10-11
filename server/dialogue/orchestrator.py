from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..config import pacing_cfg, settings
from ..llm import llm
from ..memory.manager import manager
from ..persona import build_messages
from ..speech.tts import split_for_speech, tts

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


EMOTIONS = ("base", "调侃", "得意", "认真", "收敛", "轻微不服", "变小气")
_EMOTION_TAG = re.compile(rf"^\s*[\[【]({'|'.join(EMOTIONS)})[\]】]\s*")

# 让模型自己报情绪。靠关键词猜永远只能覆盖"哈哈""哼"这类显式标记，
# 而毒舌/收敛这类语气是没有关键词的 —— 而语气恰恰要传给 TTS。
EMOTION_INSTRUCTION = (
    "输出格式：先用 [情绪] 开头标注你此刻真实的说话情绪，再正常说话。\n"
    f"情绪只能从这几个里选一个：{'、'.join(EMOTIONS)}\n"
    "例子：[调侃]写了不代表做了。\n"
    "[收敛]抱歉，我不该那么说。\n"
    "标签之后的内容就是你要说出口的话，不要再写别的标记。"
)


def parse_emotion(raw: str) -> tuple[str, str]:
    """Pull the leading [emotion] tag off the reply. Falls back to the heuristic."""
    match = _EMOTION_TAG.match(raw)
    if match:
        return match.group(1), raw[match.end() :].strip()
    return detect_emotion(raw), raw.strip()


_SENTENCE_END = re.compile(r"[。！？!?…]+")


def clip_sentences(text: str, limit: int) -> str:
    """把回复裁到 limit 字以内，且只切在句子边界上。

    之前这里用的是 text[:limit-1] + "。"，会在从句中间砍断，
    读起来像程序坏了。而紧跟着的那段"按句号保留完整句子"的逻辑，
    因为 short/medium 上限都小于 soft_limit，永远执行不到，是死代码。

    裁剪只是兜底：长度规则已经写进提示词了，模型自己该负责。

    保证：返回值一定 <= limit。哪怕一句话就超了，也必须退回字符级裁剪，
    否则 hard_limit 就不是硬上限了。
    """
    limit = max(int(limit), 2)
    if len(text) <= limit:
        return text
    parts = re.split(r"(?<=[。！？!?…])", text)
    kept: list[str] = []
    total = 0
    for part in parts:
        if total + len(part) > limit and kept:
            break
        kept.append(part)
        total += len(part)
    trimmed = "".join(kept).strip()
    # 至少留一句；但留完还是超限（单句就装不下），退回字符级裁剪
    if trimmed and len(trimmed) <= limit:
        return trimmed
    return text[: limit - 1].rstrip("，、；, ") + "。"


def _sentence_end(buffer: str, start: int) -> int:
    """Index of the last character of the first complete sentence at/after `start`."""
    match = _SENTENCE_END.search(buffer, start)
    if not match:
        return -1
    end = match.end()
    # 句末标点后面如果紧跟引号/括号，一并算进去
    while end < len(buffer) and buffer[end] in "”』」）)":
        end += 1
    return end - 1


def _default_state() -> dict[str, Any]:
        return {
            "current_state": "",
            "user_state_note": "",
            "turn_count": 0,
        }


class Orchestrator:
    def __init__(self) -> None:
        self.pacing = pacing_cfg()
        self._last_meta: dict[str, Any] = {}
        self._extract_tasks: set[asyncio.Task] = set()
        # 情绪状态按会话存。放全局的话，用户某天说一句"好累"会把"收敛"
        # 粘在单例上，之后每一句新会话都在收敛——听着像闹别扭。
        self._sessions: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] = {
            "proactivity_enabled": False,
            "proactive_suppressed_until": 0.0,
            "last_proactive_at": 0.0,
            "proactive_count_today": 0,
            "day_stamp": time.strftime("%Y-%m-%d"),
            "recent_reopens": 0,
        }

    def session_state(self, session_id: str) -> dict[str, Any]:
        return self._sessions.setdefault(session_id, _default_state())

    def end_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def stream_respond(
        self,
        *,
        session_id: str,
        user_text: str,
        history_turns: int = 10,
        on_memories: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield turn events as they happen.

        The whole point is that the user hears the first sentence while the
        model is still writing the second one. A 20-character reply costs this
        model ~500 tokens; waiting for all of it before synthesising turns a
        1-second answer into a 4-second silence. Sentence-level TTS hides that.
        """
        started = time.perf_counter()
        user_text = user_text.strip()
        if not user_text:
            return
        session = self.session_state(session_id)

        for keyword in REFUSE_KEYWORDS:
            if keyword in user_text:
                self.state["proactive_suppressed_until"] = time.time() + float(
                    self.pacing["cooldown"]["suppress_proactivity_seconds"]
                )
                session["user_state_note"] = "用户刚表示不想聊，语气要收着，不要追问"

        recent = store_recent(session_id, history_turns)
        memory_block, used_ids, memory_found = "", [], True
        if manager.enabled:
            memory_block, used_ids, memory_found = manager.retriever.build_context(user_text)

        messages = build_messages(
            memory_block=memory_block,
            memory_found=memory_found,
            current_state=session["current_state"],
            user_state_note=session["user_state_note"],
            proactivity=self._proactive_note(),
            emotion_instruction=EMOTION_INSTRUCTION,
            history=recent,
            user_text=user_text,
        )

        yield {"type": "thinking"}

        events: asyncio.Queue = asyncio.Queue()
        audio_jobs: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        holder = {"emotion": "", "buffer": "", "spoken_upto": 0, "first_audio_at": None}

        async def _pump_llm() -> None:
            """Read the model stream to the end, never blocking on TTS.

            Synthesising inline would stall the HTTP stream for a second or two
            per sentence; the connection backs up and the whole thing ends up
            slower than not streaming at all.
            """
            nonlocal done
            buffer = ""
            spoken_upto = 0
            shown = 0
            tag_done = False
            try:
                async for delta in llm.stream(messages, max_tokens=int(settings.llm_reply_tokens)):
                    buffer += delta
                    if not tag_done:
                        tag = _EMOTION_TAG.match(buffer)
                        if tag:
                            holder["emotion"] = tag.group(1)
                            buffer = buffer[tag.end():]
                            tag_done = True
                            events.put_nowait({"type": "emotion", "emotion": tag.group(1)})
                        elif not buffer.startswith("[") or len(buffer) > 12:
                            tag_done = True
                    if len(buffer) > shown:
                        events.put_nowait({"type": "text_delta", "delta": buffer[shown:]})
                        shown = len(buffer)
                    while True:
                        cut = _sentence_end(buffer, spoken_upto)
                        if cut < 0:
                            break
                        sentence = buffer[spoken_upto: cut + 1]
                        spoken_upto = cut + 1
                        if sentence.strip():
                            audio_jobs.put_nowait(sentence)
                holder["buffer"] = buffer
                holder["spoken_upto"] = spoken_upto
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - network
                events.put_nowait({"type": "error", "message": str(exc)[:200]})
            finally:
                holder["buffer"] = buffer
                holder["spoken_upto"] = spoken_upto
                audio_jobs.put_nowait(SENTINEL)
                done["llm"] = True

        async def _pump_audio() -> None:
            index = 0
            while True:
                sentence = await audio_jobs.get()
                if sentence is SENTINEL:
                    break
                for chunk in split_for_speech(sentence):
                    audio = await tts.synthesize(chunk, holder["emotion"] or "base")
                    if holder["first_audio_at"] is None:
                        holder["first_audio_at"] = time.perf_counter()
                    events.put_nowait({
                        "type": "audio",
                        "index": index,
                        "text": chunk,
                        "emotion": holder["emotion"] or "base",
                        "audio": audio,
                    })
                    index += 1
            done["audio"] = True

        done = {"llm": False, "audio": False}
        llm_task = asyncio.create_task(_pump_llm())
        audio_task = asyncio.create_task(_pump_audio())
        try:
            while not (done["llm"] and done["audio"]) or not events.empty():
                if done["llm"] and done["audio"] and events.empty():
                    break
                try:
                    event = await asyncio.wait_for(events.get(), timeout=30)
                except asyncio.TimeoutError:
                    break
                yield event
        except asyncio.CancelledError:
            llm_task.cancel()
            audio_task.cancel()
            raise
        finally:
            for task in (llm_task, audio_task):
                if not task.done():
                    task.cancel()

        buffer = holder["buffer"]
        spoken_upto = holder["spoken_upto"]
        emotion = holder["emotion"]
        first_audio_at = holder["first_audio_at"]

        # 尾巴（没句号的那截）在 LLM 结束后才成形，单独补一次合成
        tail = buffer[spoken_upto:].strip()
        if tail:
            index = audio_jobs.qsize()
            for chunk in split_for_speech(tail):
                audio = await tts.synthesize(chunk, emotion or "base")
                if first_audio_at is None:
                    first_audio_at = time.perf_counter()
                yield {
                    "type": "audio",
                    "index": index,
                    "text": chunk,
                    "emotion": emotion or "base",
                    "audio": audio,
                }
                index += 1

        full = buffer.strip()
        if not emotion:
            emotion = detect_emotion(full)
        text = self._postprocess(full, user_text=user_text, emotion=emotion)
        session["turn_count"] += 1
        self._maybe_update_state(session, text, user_text)
        store_message(session_id, "user", user_text, "voice")
        store_message(session_id, "assistant", text, "voice")

        self._spawn_extraction(
            session_id=session_id,
            user_text=user_text,
            assistant_text=text,
            recent_context="\n".join(f"{m['role']}:{m['content']}" for m in recent[-4:]),
            on_memories=on_memories,
        )
        yield {
            "type": "done",
            "text": text,
            "emotion": emotion,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "first_audio_ms": int((first_audio_at - started) * 1000) if first_audio_at else None,
        }

    # ---------------- public API ----------------
    async def respond(
        self,
        *,
        session_id: str,
        user_text: str,
        mode: str = "text",
        history_turns: int = 10,
        on_memories: Any = None,
    ) -> TurnResult:
        started = time.perf_counter()
        user_text = user_text.strip()
        if not user_text:
            return TurnResult(text="", latency_ms=0)
        session = self.session_state(session_id)

        for keyword in REFUSE_KEYWORDS:
            if keyword in user_text:
                self.state["proactive_suppressed_until"] = time.time() + float(
                    self.pacing["cooldown"]["suppress_proactivity_seconds"]
                )
                session["user_state_note"] = "用户刚表示不想聊，语气要收着，不要追问"

        recent = store_recent(session_id, history_turns)
        memory_block, used_ids, memory_found = "", [], True
        if manager.enabled:
            memory_block, used_ids, memory_found = manager.retriever.build_context(user_text)

        messages = build_messages(
            memory_block=memory_block,
            memory_found=memory_found,
            current_state=session["current_state"],
            user_state_note=session["user_state_note"],
            proactivity=self._proactive_note(),
            emotion_instruction=EMOTION_INSTRUCTION,
            history=recent,
            user_text=user_text,
        )

        raw = await llm.complete_raw(
            messages,
            max_tokens=int(settings.llm_reply_tokens),
        )
        emotion, body = parse_emotion(raw["text"])

        # 截断的表现不一定是空：模型可能刚好在输出 "[调侃]" 的开头被切掉，
        # 留下一个光秃秃的 "["。所以要用"短得不像话"而不是"空"来判断。
        if raw["finish_reason"] == "length" and len(body) < 6:
            body = await self._retry_shorter(messages, user_text, emotion)

        text = self._postprocess(body, user_text=user_text, emotion=emotion)
        self._last_meta = {
            "finish_reason": raw["finish_reason"],
            "usage": raw.get("usage", {}),
            "reasoning_chars": len(raw.get("reasoning") or ""),
        }
        self._spawn_extraction(
            session_id=session_id,
            user_text=user_text,
            assistant_text=text,
            recent_context="\n".join(f"{m['role']}:{m['content']}" for m in recent[-4:]),
            on_memories=on_memories,
        )

        session["turn_count"] += 1
        self._maybe_update_state(session, text, user_text)
        store_message(session_id, "user", user_text, mode)
        store_message(session_id, "assistant", text, mode)

        return TurnResult(
            text=text,
            emotion=emotion,
            memories_used=used_ids,
            saved=[],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _spawn_extraction(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        recent_context: str,
        on_memories: Any,
    ) -> None:
        """Extraction is bookkeeping, not conversation -- it must not sit on the
        critical path. The user should already be hearing the reply while this
        runs in the background."""
        if not manager.enabled:
            return

        async def _run() -> None:
            try:
                memories = await manager.observe_turn(
                    session_id=session_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    recent_context=recent_context,
                )
            except Exception as exc:  # pragma: no cover - never kill the loop
                self._last_meta["extract_error"] = str(exc)[:200]
                return
            if on_memories and memories:
                try:
                    await on_memories(memories)
                except Exception:  # pragma: no cover
                    pass

        task = asyncio.create_task(_run())
        self._extract_tasks.add(task)
        task.add_done_callback(self._extract_tasks.discard)

    async def drain(self) -> None:
        """Wait for background extraction (used by tests and eval)."""
        if self._extract_tasks:
            await asyncio.gather(*list(self._extract_tasks), return_exceptions=True)

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

        messages = build_messages(
            memory_block=manager.context_for("待办 提醒"),
            proactivity="现在是用户允许的主动发言时段。你可以自然地起个头，一句话，别追问。",
            emotion_instruction=EMOTION_INSTRUCTION,
            user_text="（用户暂时没说话，你可以主动说一句）",
        )
        raw = await llm.complete(messages, max_tokens=200)
        _, text = parse_emotion(raw)
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
    async def _retry_shorter(
        self,
        base_messages: list[dict[str, str]],
        user_text: str,
        emotion: str,
    ) -> str:
        """Truncation rescue: same context, explicitly told to answer in one short line."""
        messages = list(base_messages) + [
            {
                "role": "user",
                "content": f"（上一条被截断了，现在只回一句话，{emotion} 情绪，20 字以内。）",
            }
        ]
        retry = await llm.complete(messages, max_tokens=512)
        return parse_emotion(retry)[1]

    def _proactive_note(self) -> str:
        if not self.state["proactivity_enabled"]:
            return "用户没有授权你主动开口。所以只回应他实际说的话。"
        return "用户允许你偶尔主动开口，但不要每次都主动，也不要连续追问。"

    def _postprocess(self, raw: str, *, user_text: str, emotion: str = "") -> str:
        text = sanitize(raw)
        if emotion == "收敛":
            # 用户说过难受/不喜欢之后，收敛态不允许还挂着问句
            text = re.sub(r"[？?]+\s*$", "", text).strip()
        hard = int(self.pacing["length"]["hard_limit_chars"])
        target_max = self._length_target(user_text)
        if len(text) > target_max:
            text = clip_sentences(text, target_max)
        if len(text) > hard:
            text = clip_sentences(text, hard)
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

    def _maybe_update_state(self, session: dict[str, Any], assistant_text: str, user_text: str) -> None:
        recent = self.state.setdefault("_recent_assistant", [])
        recent.append(assistant_text)
        del recent[:-int(self.pacing["questions"].get("window", 10))]
        if any(k in user_text for k in ("难过", "累", "烦", "emo", "崩溃", "压力")):
            session["current_state"] = "收敛"
            session["user_state_note"] = "用户情绪低落：少调侃，简短一点，多听"
        elif any(k in user_text for k in ("没事", "好了", "开心", "搞定")):
            session["current_state"] = ""
            session["user_state_note"] = ""
        day = time.strftime("%Y-%m-%d")
        if day != self.state["day_stamp"]:
            self.state["day_stamp"] = day
            self.state["proactive_count_today"] = 0

    def snapshot(self) -> dict[str, Any]:
        return {k: v for k, v in self.state.items() if not k.startswith("_")} | {"last_call": self._last_meta}


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
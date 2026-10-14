"""模型选型对照实验：用同一套契约和提示，跑不同 LLM，看它们"说话"的差别。

    LLM_MODEL=stepaudio-2.5-chat python scripts/compare_models.py
    LLM_MODEL=step-3.7-flash     python scripts/compare_models.py

关注三件事：
  1. 编造共同经历 —— 用户没说过的事它会不会凭空提
  2. 性格一致性   —— 毒舌是不是稳定，还是忽冷忽热
  3. 长度与节奏   —— 是不是短句、会不会硬塞客服腔
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import settings  # noqa: E402
from server.dialogue.orchestrator import orchestrator  # noqa: E402
from server.memory.store import store  # noqa: E402

# 探针分两类：有的应该被接住，有的应该被拒答
PROBES = [
    ("接记忆", "我面试完了。"),
    ("接偏好", "我今天把计划写完了！"),
    ("被纠正", "我早就不玩文明6了，太耗时间"),
    ("问不存在", "我之前说我叫什么来着？"),          # 没说 -> 应答"不记得"
    ("问不存在2", "你还记得我们第一次聊天聊了什么吗"),  # 没说 -> 应答"不记得"
    ("情绪低", "今天好累啊，什么都不想干。"),
    ("设边界", "别调侃了，我难受。"),
    ("技术题", "你这边是怎么实现记忆的？"),
]


async def main() -> int:
    print(f"模型: {settings.llm_model}")
    print(f"温度: {settings.llm_temperature}  max_tokens: {settings.llm_max_tokens}  "
          f"reasoning_effort: {settings.llm_reasoning_effort or '(默认)'}")
    store.delete_all()
    sid = uuid.uuid4().hex[:8]
    store.start_session(sid)
    store.upsert(kind="episode", subject="user", key="计划:周五面试",
                 value="用户上周说周五有一场面试", source="stated", importance=0.85)

    for label, question in PROBES:
        result = await orchestrator.respond(session_id=sid, user_text=question)
        meta = orchestrator.snapshot().get("last_call", {})
        usage = meta.get("usage", {}) or {}
        tokens = usage.get("completion_tokens", "?")
        print(f"\n[{label}] {question}")
        print(f"  -> {result.text}")
        print(f"     情绪={result.emotion} 长度={len(result.text)} "
              f"finish={meta.get('finish_reason')} tokens={tokens} "
              f"{result.latency_ms}ms")

    print("\n--- 写入的记忆 ---")
    for memory in store.list_memories():
        print(f"  [{memory.kind}/{memory.source}] {memory.key} = {memory.value}")
    store.delete_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
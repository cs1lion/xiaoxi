# WORKFLOWS — 第二阶段核心逻辑流

文字和 JSON Schema 说不清时序交互的地方，用图说。本文四张图对应 `PROTOCOL.md` 第十至十三节，**契约以协议为准，图只是帮助建立直觉**；图与协议冲突时改图。

## F1 日记合成与偏置注入

```mermaid
sequenceDiagram
    participant U as 用户
    participant O as orchestrator（Role 2 文件）
    participant M as memory/diary（Role 1）
    participant P as persona.bias_block（Role 1）
    participant L as LLM
    U->>O: 发消息（用户消息先落库）
    O->>M: 后台触发：当天已存消息集合
    M->>L: 有界调用（≤1 次）：合成第一人称日记
    L-->>M: 日记草稿
    M->>M: 程序化校验（date/mood/长度/source_message_ids 真实存在）
    alt 校验失败
        M-->>M: 拒绝入库，静默放弃（不重试轰炸）
    else 校验通过
        M->>M: 落 diary 表 → 派生 drift / spark
        M-->>O: diary_notice（quiet，不打断播放）
    end
    Note over O,P: 下一轮组装提示词时
    O->>P: bias_block(drifts, sparks)
    P-->>O: 偏置块（空输入 → 空串，行为等价现状）
    O->>L: system + 偏置块（不可信数据边界内）+ 历史 + 用户输入
```

要点：日记永远慢一轮生效；`latent` 火花在 `surfaced` 前不出现在任何提示词或事件里。

## F2 长语音微句流与双工打断

```mermaid
sequenceDiagram
    participant L as LLM 流
    participant O as orchestrator（Role 2）
    participant Q as 预加载合成队列（并发有界）
    participant S as /ws 服务端
    participant C as 客户端 PlaybackQueue + timeline（Role 3）
    L->>O: text_delta 流式正文
    O->>O: sanitize → 微句切分（子句优先，12–25 字，60 硬顶）
    O->>S: speech_plan（可选，mode=micro）
    loop 每个微句（index 与 chunk.seq 单调不减）
        O->>Q: 合成第 N+1 段（第 N 段播放中预加载）
        Q-->>O: 音频字节
        O->>S: audio{index, seq, chunk{pause_after_ms, breath}}
        S->>C: 同左
        C->>C: 顺序解码播放；某 index 缺失则跳过，不卡播
    end
    C->>S: barge_in{turn_id}（用户插话）
    S->>O: 取消本轮未完成合成
    O->>S: interrupted（此后该轮零 audio）
    S->>C: interrupted / playback_stopped
    C->>C: 立即丢弃未播放队列与未完成解码
    Note over O,C: 被打断的轮次以 interrupted 终止，不再发 done；<br/>只有自然收尾的轮次才发 done（delivery 计数口径不变）
```

客户端播放队列状态机：

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Planning: speech_plan 或首个 audio 到达
    Planning --> Playing: 解码成功
    Playing --> Playing: 后续 chunk（按 pause_after_ms 插入呼吸/微表情）
    Playing --> Flushing: interrupted / playback_stopped
    Flushing --> Idle: 队列与未完成解码全部丢弃
    Playing --> Idle: done 且队列自然排空
```

## F3 后台自进化与主动开口

```mermaid
sequenceDiagram
    participant T as 空闲计时器
    participant Loop as assistant/loop 自治模式（Role 1）
    participant M as 记忆库 / self_state（Role 1）
    participant O as orchestrator 固定回复交付器（Role 2）
    participant C as 客户端
    T->>Loop: idle ≥ idle_minutes 且用户已授权主动发言
    Loop->>M: 读最近日记 / 记忆 / 好奇心种子
    Loop->>Loop: 有界复盘（模型调用数与状态变更数上限，全程 trace）
    Loop->>M: 更新 taste / worldview；latent → surfaced
    Loop-->>C: self_evolution（quiet，仅信息性）
    alt 到达主动开口窗口
        Loop->>O: 基于 taste/worldview/spark 生成一句话（≤120 字）
        O->>C: turn_started(self_topic) → thinking → emotion → text_delta → audio* → done
    else 冷静期 / 未授权 / 用户正忙
        Loop-->>Loop: 不开口，仅更新状态
    end
```

要点：自进化只写 `subject="self"`；拒绝关键词冷静期对 `self_topic` 同样生效。

## F4 星图检索与节点讲述

```mermaid
sequenceDiagram
    participant C as 星图视图（Role 3）
    participant A as /api/memory（Role 1）
    participant S as /ws（Role 0/2 接线）
    C->>A: GET /api/memory/starmap?days&kinds&q&limit
    A-->>C: {nodes, edges, stats, truncated}
    C->>C: 渲染星点与边；关键词检索走 q（服务端过滤）
    C->>A: GET /api/memory/starmap/node/{node_id}
    A-->>C: 节点全文（详情面板；列表接口永远不给正文）
    C->>S: {"action":"narrate","node_id":"...","turn_id":"..."}
    alt 当前空闲
        S->>C: turn_started(narration) → thinking → emotion → text_delta → audio* → done
    else 忙碌
        S-->>C: error(code=narrate_busy, recoverable)（不排队不重试）
    end
    C->>S: barge_in 可随时打断讲述（F2 打断契约适用）
```

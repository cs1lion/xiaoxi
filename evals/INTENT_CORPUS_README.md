# 意图语料（evals/intent_corpus.jsonl）

意图路由的校准语料（issue #42，B1）。用于测量现有判定器的误判与漏判，
阈值/判据的任何调整都以这份语料的输出为准。

## 生成

```bash
.\.venv\Scripts\python.exe scripts\build_intent_corpus.py          # 重建
.\.venv\Scripts\python.exe scripts\build_intent_corpus.py --check  # 只校验格式
```

不要手改 `intent_corpus.jsonl`：种子句在生成脚本 `SEEDS` 里，改那里再重建。
手工标注的补充语料（例如从 dogfood 转录里摘的真实句子）同样加进 `SEEDS`。

## 字段

| 字段 | 说明 |
|---|---|
| `text` | 用户原话（未归一化；归一化在判定时做） |
| `label` | `delegate` / `progress` / `accept` / `reject` / `chat`（闭集） |
| `target` | 仅 `delegate`：`file` / `research` / `writing` / `code` / `sandbox` / `other` |
| `source` | `handwritten`（人工定标签）或 `derived`（程序派生的礼貌/语气变体） |
| `note` | 边界样本的判断依据 |

## 标注口径（这是口径，不是建议）

1. **判据是「这句话是否要求她执行一个操作」，不是「句中有没有动词」。**
   「你觉得写代码累吗」有「写」，但它在问感受 → `chat`。
2. **问句形式的请求算 `delegate`**：「能不能帮我生成一个报告？」= 派活。
3. **转述别人的话不算**：「他说安排一下就行」→ `chat`。
4. **提醒类不算委托派活**：「提醒我半小时后休息」走提醒路径 → 标 `chat`
   （本语料只校准「委托/闲聊」这条判定，提醒有独立通道）。
5. **模糊、半句、指代不明**（「帮我」「就那个」）→ `chat`：缺宾语不构成派活，
   正确行为是追问或落普通对话，不是猜一个操作。
6. **ASR 掉词不改变标签**：「安排一个沙盒打样」与「帮我安排一个沙盒打样」
   必须同标签——这正是语料要覆盖的核心场景。

## 分布（2026-10-24 生成）

`delegate` 131 / `chat` 97 / `progress` 10 / `accept` 10 / `reject` 10，共 258 条。

`delegate` 与 `chat` 是校准的重点（这两类的混淆才是真问题）；其余三类
数量少是因为它们由独立正则处理、且要求整句形态（如 `accept` 是整句确认）。

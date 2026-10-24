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
4. **提醒与定时任务算 `delegate`**（2026-10-24 用户裁定）：
   「帮我定个明天下午三点的提醒」「安排个每晚八点的定时备份」都要求她执行
   操作，且确实走 `reminder.create` / `task.create` 的工具路径。
   （此前本条写作「提醒类算 chat」，与代码不符，已按用户标注纠正。）
5. **模糊、半句、指代不明**（「帮我」「就那个」）→ `chat`：缺宾语不构成派活，
   正确行为是追问或落普通对话，不是猜一个操作。
6. **ASR 掉词不改变标签**：「安排一个沙盒打样」与「帮我安排一个沙盒打样」
   必须同标签——这正是语料要覆盖的核心场景。
7. **主语是「我/他/她/我们」且描述已发生或正在发生的事 → `chat`**：
   「我今天整理了一下午的报销文件」「老张刚写的周报被夸」「我昨晚在沙盒里
   折腾驱动」——有动词有产物，但主语不是「你去做」。这条比「有没有动词」
   更可操作，也是实测误判最多的一类。
8. **口头修正以最后意图为准**：「先说验收，等等第三页不对，还是打回」
   → 按最终意图标 `reject`。

## 两份语料的分工

| 文件 | 来源 | 用途 |
|---|---|---|
| `intent_corpus.jsonl` | `scripts/build_intent_corpus.py` 的 `SEEDS`（自造） | 覆盖形态矩阵，快速迭代 |
| `intent_corpus_user.jsonl` | 用户提供的真实语料（`source: user`） | **阈值与判据的真实基准**，优先于自造语料 |

**自造语料容易过拟合**：实测同一判定器在自造语料上 80.6%、在真实语料上只有
52.3%。因此任何判据调整都必须**同时报两个数字**，且以 `intent_corpus_user.jsonl`
为准。

## 分布

- 自造（`intent_corpus.jsonl`）：`delegate` 131 / `chat` 97 / `progress` 10 /
  `accept` 10 / `reject` 10，共 258 条。
- 真实（`intent_corpus_user.jsonl`）：130 条，`delegate` 76 / `chat` 33 /
  `progress` 8 / `accept` 7 / `reject` 6。

`delegate` 与 `chat` 是校准的重点（这两类的混淆才是真问题）。
真实语料的 `chat` 里「主语是别人」的自述偏少，是当前已知的采样缺口——
这类恰是误判高发区，补语料时优先补。

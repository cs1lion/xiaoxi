# 记忆召回对照实验与同义桥生产接入（2026-11-08）

- **性质**：对照实验读数归档 + 生产接入说明。
- **代码锚**：[retriever.py](file:///d:/project/neurosama/server/memory/retriever.py)、
  [measure_memory_recall_hybrid.py](file:///d:/project/neurosama/scripts/measure_memory_recall_hybrid.py)、
  [test_memory_retrieval.py](file:///d:/project/neurosama/scripts/test_memory_retrieval.py)。
- **配置锚**：`config/memory.yaml` 的 `retrieval.synonym_bridge`；词表 `config/memory_synonyms.tsv`（默认空）。
- **数据锚**：`evals/memory_recall_seed.tsv`、`evals/memory_recall_paraphrase.tsv`、
  `evals/memory_recall_synonyms.tsv`（8 键**合成**概念桥）。
- **基线锚**：base HEAD `e036a14`，分支 `exp/memory-recall-hybrid-20261108`，2026-11-08 实测。
- **上游设计**：`docs/MEMORY_RESEARCH_NEXT.md:158`「第三轮：检索与关联」——使用同一记忆集合
  比较词法 / 混合检索，不让不同抽取结果混进检索比较。本轮只做前两臂，图扩展未做。

## 1. 要回答的问题

`docs/HANDOFF_20261105.md` §十三 已量出：32 条合成改述用例上 `paraphrase_recall = 0.750`，
`synonym` 档 0.375，8/8 漏检「与 `key+value` 零共享 2-gram」，归因到
[retriever.py](file:///d:/project/neurosama/server/memory/retriever.py) 的
`if query and sim < min_similarity: continue` 硬门。

本轮回答两个问题，并把结论落到生产路径：

1. 2-gram 硬门是不是唯一瓶颈？关掉它，召回能到多少、代价是什么？
2. 在**不关**硬门的前提下，查询侧补一条概念桥（词法+同义混合），能收回多少；
   接入生产后，改前 / 改后同口径读数是什么？

## 2. 实验设计

| 项 | 取值 |
|---|---|
| 记忆集合 | 8 条合成种子，与 `test_memory_retrieval_quality.py:CORPUS` 逐条一致 |
| 用例 | 32 条（literal / synonym / colloquial / asr_drop 各 8），与既有基线同一份 |
| 隔离 | `DB_PATH=:memory:`、`LLM_PROVIDER=mock`、零网络、零 Key、零真实库 |
| 门 | `min_similarity` 按臂取值（baseline / bridge_rescue = 0.08，gate_off = 0） |
| 二次门 | `min_score=0`，与既有 `0.750 (n=32)` 基线同口径（生产 `min_score=0.12` 未套用） |
| 排序 | 生产 `MemoryRetriever.search()` 原样，未改分数公式、未换向量库 |

三臂：

- **baseline**：改前行为。`retrieval.synonym_bridge.enabled = false`，生产代码原样。
- **bridge_rescue**：改后行为。生产 `search()` 打开同义桥；词表是**一次性临时文件**，
  由 `evals/memory_recall_synonyms.tsv` 的 8 键合成表转成生产格式
  `触发词<TAB>扩展词`（用完即删，不产生可提交产物）。
- **gate_off**（诊断臂，不是产品提案）：`min_similarity=0`，其余不变。用来区分
  「卡在门」还是「卡在排序」。**不得写回生产配置**。

复现：

```powershell
.\.venv\Scripts\python.exe -B scripts\measure_memory_recall_hybrid.py
```

同一命令连跑两次输出逐字节一致（2026-11-08 实测，无墙钟依赖）。

## 3. 读数（改前 / 改后同口径）

| band | baseline（改前）hit/n · recall · top1 · 平均注入 | bridge_rescue（改后）hit/n · recall · top1 · 平均注入 | gate_off（诊断）hit/n · recall · top1 · 平均注入 |
|---|---|---|---|
| literal | 8/8 · 1.000 · 8 · 1.000 | 8/8 · 1.000 · 8 · 1.000 | 8/8 · 1.000 · 8 · 6.000 |
| synonym | 3/8 · 0.375 · 3 · 0.375 | **7/8 · 0.875** · 7 · 0.875 | 7/8 · 0.875 · 4 · 6.000 |
| colloquial | 6/8 · 0.750 · 6 · 0.875 | 6/8 · 0.750 · 6 · 0.875 | 8/8 · 1.000 · 6 · 6.000 |
| asr_drop | 7/8 · 0.875 · 7 · 0.875 | 7/8 · 0.875 · 7 · 0.875 | 8/8 · 1.000 · 7 · 6.000 |
| **合计** | **24/32 · 0.750** · 24 · 0.781 | **28/32 · 0.875** · 28 · 0.906 | **31/32 · 0.969** · 25 · 6.000 |

控制组（语料中没有对应主题的 6 条查询，正确行为是 `found=False`）：

| arm | 误命中查询 | 平均注入 |
|---|---|---|
| baseline | 1/6 | 0.167 |
| bridge_rescue | 1/6 | 0.167 |
| gate_off | **6/6** | 6.000 |

baseline / bridge_rescue 那 1/6 是既有 `KNOWN_FALSE_HITS`（「明天早上的航班几点起飞」共享
`早上`），不是本轮新增。**救援只触发 4 条，且 4 条全部命中**；已经命中的 28 条查询排序与
注入条数与改前逐格一致（literal 1.000、colloquial 0.875、asr_drop 0.875 都没变），
所以合计平均注入只从 0.781 升到 0.906。

## 4. 机制归因

### 4.1 硬门是唯一瓶颈（baseline）

8 条漏检的 `sim` 全部是 **0.0000**、共享 2-gram 全部是 **0**：

| band | 漏检查询 | sim | 共享 2-gram |
|---|---|---|---|
| synonym | 这礼拜五的安排是啥 | 0.0 | 0 |
| synonym | 家里那只小橘几岁了 | 0.0 | 0 |
| synonym | 现在定居的地方是哪儿 | 0.0 | 0 |
| synonym | 平日起身的钟点是几点 | 0.0 | 0 |
| synonym | 现在啃的那门外文是什么 | 0.0 | 0 |
| colloquial | 就那个慢慢种田的，我玩的是啥 | 0.0 | 0 |
| colloquial | 那个…我不能碰的是啥来着 | 0.0 | 0 |
| asr_drop | 我住再杭洲 | 0.0 | 0 |

`continue` 发生在打分之前，`importance / recency / confidence / utility` 四个重排信号
**全部在门后**，救不回零命中。这不是「分数不够高」，是「根本没有可比表面形式」。

### 4.2 关掉门：+7 召回，但负向约束失效

`gate_off` 把 24/32 抬到 31/32（+7）。剩下 1 条（「这礼拜五的安排是啥」）不是门的问题：
`top_k=6` 小于 8 条语料，零相似度记录退化为按重要度/新近度常量并列，排序垫底被截断。
**开门只是把「表示重叠问题」换成「排序问题」，并没有解决它。**

代价是决定性的：控制组从 1/6 变成 **6/6 全部误命中**。负向约束分支（「没有相关记忆」）再也
不会触发。这也从反面复述了既定判据：**漏检是安全方向，假命中是危险方向**。因此门不能关。

### 4.3 同义桥救援：门不动，+4 召回，控制组无新增误命中

`bridge_rescue` 把 24/32 抬到 28/32（+4），全部来自 synonym 档（3/8 → 7/8），命中的四条是
「安排 / 定居 / 起身 / 外文」——查询里有可映射的主题词。救援触发 4 条、成功 4 条；
控制组仍是 1/6，**没有新增假命中**。

桥也救不回的 4 条，正好划出它的能力边界：

| band | 漏检查询 | 桥为什么无效 |
|---|---|---|
| synonym | 家里那只小橘几岁了 | 词表里是 `橘猫`，`小橘` 不是它的子串；需要「橘→猫」这类词义推理 |
| colloquial | 就那个慢慢种田的… | 查询里没有任何主题词（`种田` 不在记忆文本里） |
| colloquial | 那个…我不能碰的是啥来着 | 省略到只剩指代，无表面形式可桥 |
| asr_drop | 我住再杭洲 | 错字破坏了 `杭州`，触发词同样匹配不上 |

结论：**硬门是瓶颈；但「补共享表面形式」只解决词面替换，不解决省略与错字。** 后三类需要
真正的语义匹配（embedding）或 ASR 纠错/模糊匹配，而不是再扩词表——本轮也没有为了读数好看
去扩词表。

## 5. 生产接入

### 5.1 机制位置

[retriever.py](file:///d:/project/neurosama/server/memory/retriever.py)：

- `search()` 先走原查询；**只有首轮零命中、查询非空、`top_k > 0`** 时，才用
  `_bridge_expansion()` 生成救援查询，再走同一个 `_search_once()`。
- `_search_once()` 是原 `search()` 的函数体原样搬入，**未改任何门、权重、排序规则**；
  两次调用都读同一份快照、同一个 `now`。
- `_bridge_lexicon()` 读配置词表，按 `(路径, mtime_ns, 大小)` 缓存；关闭、缺文件、
  格式错误一律退回空表（**坏配置绝不打断语音快环**，也绝不静默改写查询）。
- 救援只改查询文字，不改 `min_similarity`、不改 `min_score`、不改 `RECALL_FLOOR`。

### 5.2 配置

`config/memory.yaml`（`retrieval` 段）：

```yaml
synonym_bridge:
  enabled: true
  lexicon_path: config/memory_synonyms.tsv
```

`config/memory_synonyms.tsv` 词表格式：`触发词<TAB>扩展词,扩展词`。**出厂文件只有注释、
词表为空** —— 空表 = 不桥接 = 与接入前行为完全一致。这是有意的诚实默认：主仓没有真实口语
同义词资源，不能让人以为生产已经覆盖同义改述。

- **开启**：往 `config/memory_synonyms.tsv` 加行；按 mtime 缓存，无需重启。
- **关闭 / 回退**：`enabled: false`（或删词表；缺文件也等于关闭）。
- **词表来源**：人工维护的概念表，按自己的记忆内容写扩展词；不是通用中文同义词库，
  也不是 `evals/memory_recall_synonyms.tsv` 那份 8 键合成表（两者 key 语义不同：
  实验表按记忆 key，生产表按查询触发词）。

### 5.3 覆盖边界（不要误读）

桥接只覆盖「查询里有主题词、但字面与记忆不同」的替换型改述。以下三类**明确不覆盖**，
与 §4.3 的 4 条漏检一一对应：

1. **省略**：查询里没有可映射的主题词（"那个…我不能碰的"）。
2. **错字**：ASR 把主题词打坏（"杭洲"）。
3. **词义推理**：需要常识/词义才能桥接（"小橘"→猫）。

此外，救援路径仍使用基础硬门，所以扩展后相似度不够时照样返回空——宁可漏检，不造假命中。

## 6. 口径与局限

- **0.750 (n=32) 不是生产召回基准**：标注集纯合成、n 很小，TF-IDF 的 IDF 在 8 条语料上退化，
  单个共享 bigram 就能主导余弦。既有测试已明禁拿合成语料数字对外陈述检索质量。
- **概念桥也是合成的**：0.875 是「假如同义资源存在」的读数，生产默认词表为空，实际线上
  仍是 0.750 的机制基线，直到有人维护词表。
- **band 是构造手法标签，不是难度刻度**：本轮再次复现「asr_drop 0.875 高于 synonym 0.375」，
  解释变量是共享表面形式，不是「近义/口语/ASR」这个分类名。
- **生产配置比实验更严**：实验压掉 `min_score=0.12` 以对齐既有基线；生产上零相似度记录连
  二次门都过不了。真实环境里的 synonym 档只会更低，不会更高。

## 7. 明确未做

- 未改 `RECALL_FLOOR=0.75`、`min_similarity`、`min_score`、`executive.py`；未新增协议字段。
- **未新增任何门禁**：没有新增 `test_*` 脚本，没有登记 GATES / ALWAYS；新增用例只补在
  [test_memory_retrieval.py](file:///d:/project/neurosama/scripts/test_memory_retrieval.py)
  的 `SynonymBridgeTests` 里（10 条，直接覆盖本改动）。
- 未引入 embedding / FTS5 / 图扩展；未改 `data/memory.db`；零网络、零 API Key；
  未重启 8000/8010；未 push、未碰 master。
- 图扩展（`MEMORY_RESEARCH_NEXT.md:158` 的第三臂）未做：它依赖证据关联边，与「同一记忆集合
  上的检索机制对比」是另一层问题，留给下一轮。

**一句话**：硬门的机制归因被对照实验证实——8/8 漏检 `sim=0.0000`、零共享 2-gram；开门能拿
+7 但负向约束 6/6 全失效；不关门、只补概念桥救援能拿 +4（24/32→28/32）且无新增假命中，
剩下的 4 条是省略与错字，词表扩不动，指向语义匹配或 ASR 纠错。

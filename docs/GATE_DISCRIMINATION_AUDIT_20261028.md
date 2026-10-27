# 门禁判别力审计（2026-10-28）

用 `scripts/check_probe.py` 对四个已登记门禁做判别力审计：给每个门禁注入一个
「让被测性质不成立」的破坏版，跑两步自检——破坏版必须 exit≠0（门禁有牙），
还原后必须 exit==0（还原干净）。

## 审计结果

| 门禁 | 被测性质 | 注入方式 | 结论 |
|---|---|---|---|
| `broadcast_e2e` | v2 hub 的 seq 必须从 1 起 | `seq == 1` → `seq == 2` | 有牙 |
| `shared_retriever_concurrency` | 四条并发不变量 | `not torn`/`not miss_bad`/`not top1_bad`/`not drifted` 全部取反 | 有牙 |
| `voice_offloop` | 落库必须离开事件循环 | 6 处 `is not loop_thread` / `not on_loop` 全部取反 | 有牙 |
| `memory_ranking` | 两阶段检索逐位等价 | 2 处等价判据取反 | 有牙 |

四个门禁全部**有牙**：破坏版 exit=1，还原后 exit=0。没有发现恒绿判据。

## 审计过程比结果更值钱：连续三次注入设计失败

同一次审计里，我的注入设计错了三次，`check_probe` 每次都正确判出「探针无效」而
没有让我把等价改动误报成「门禁恒绿」：

1. **改 f-string 标签**：把 `check(f"…seq=1", …)` 的标签字符串改成 `seq=999`，
   真正的断言在下一行，纹丝未动；
2. **放宽而非取反**：`mismatches == 0` → `<= 1`（原值就是 0，等价）、
   `seq == 1` → `>= 0`（真实 seq 就是 1，等价）；
3. **只抹一处同类判据**：`voice_offloop` 有 6 处「不在循环线程」断言，只改 1 处，
   其余 5 处照旧守得住。

**根因是一条可操作的规则**：造破坏版必须让条件对**当前真实值**为假，不是「放宽」
也不是「改标签」。放宽后的条件对真实值往往仍为真（真实值恰好满足放宽后的范围），
于是破坏版也绿。

## 附带确认的两件事

- **多实例判据是常态，不是冗余**：`voice_offloop` 用 6 处断言守同一性质（
  `store_message` / `starmap_node` / `rest` / `executive` / `spark` / `usage sweep`
  六条不同路径）。审计时要抹就得全抹，只抹一处说明不了任何问题。
- **注入还原必须验**：审计后 `git status` 一度显示 4 个文件被改，逐一核对后确认
  3 个与 HEAD 无差异（行尾警告是 autocrlf 提示，非内容差异），第 4 个的改动是并发
  session 的未提交工作（`build_context` → `deliver_context` 重构），不是注入残留。
  **「还原后 exit=0」不等于「仓库干净」**，还要 `git diff` 确认。

## 复用方式

```python
from check_probe import probe_self_check, patch_file

inject, restore = patch_file(PATH, ORIG_SNIPPET, BROKEN_SNIPPET)
result = probe_self_check(name=..., run=..., break_it=inject, restore=restore,
                          watch=PATH)
assert result.ok, result.report()
```

判据是「破坏版必须红、还原后必须绿、且 `watch` 文件逐字节复原」。它抓的正是上面那三种失败：等价改动 → 破坏版也绿；只抹一处 → 破坏版也绿；锚点撞多处 → `patch_file` 的 `assert count==1` 当场炸。

**`patch_file` 不立即注入，返回 `(inject, restore)`**：注入交给 `break_it`，因为 `probe_self_check` 在 `break_it` 之前拍「原文」快照。谁负责注入，快照就必须拍在它之前。

**`watch` 那一检是这次审计的副产物**：本文写于 plan-110，当时只验「还原后退出码为 0」；plan-111 发现那会被 text 模式读写的 CRLF 破坏蒙混过关（还原跑了、码也绿、仓库留下格式差异），plan-112 遂把逐字节比对编进判据。照本文旧示例跑的人会漏掉这一检。

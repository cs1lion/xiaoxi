# 2026-11-12 项目健康检查（只读调研）

- 基线：HEAD `1816007` = `origin/master`（`git rev-list --left-right --count origin/master...HEAD` = 0/0），工作区干净。
- 性质：只读调研 + 少量可复跑命令。未改任何生产代码、未改协议、未重启 8000/8010、未 commit / push。
- 工具：`gh`（issues / CI runs）、`git`、`.venv\Scripts\python.exe`（门禁与测试）、直接 HTTP 探测上游。

## 1. 环境探测

| 项 | 结果 | 依据 |
| --- | --- | --- |
| 解释器 | `.venv\Scripts\python.exe` 在位 | `AGENTS.md` 指定口径 |
| 上游 LLM | **阻塞**。`POST https://api.stepfun.com/step_plan/v1/chat/completions` → HTTP 400 `you have no active step plan subscription`；换 `https://api.stepfun.com/v1` → HTTP 402 `quota_exceeded` | 本机直发 `max_tokens=1` 探测，两个 base_url 都试过 |
| TTS / ASR | 与 LLM 同上游（`TTS_ENGINE=stepaudio` / `ASR_ENGINE=stepaudio`），同一订阅阻塞 | `.env` + `live_acceptance.py --doctor` 输出 |
| 端口 8000/8010 | 未监听（本轮没起服务） | 体检输出 `env.ports` 段 |
| 麦克风 | 14 个输入设备可用 | 同上 `asr.input` |
| `git` 远端 | `git@github.com:cs1lion/xiaoxi.git` | `git remote -v` |
| 未提交产物 | `docs/evidence/20261112-live-acceptance/`（本轮体检生成，未入库） | `git status --porcelain` |

## 2. 黄金命令读数（本机实跑，2026-11-12）

| 命令 | 结果 |
| --- | --- |
| `python scripts/check_protocol.py` | PASS |
| `python scripts/test_core.py` | 134 passed, 0 failed |
| `npm test` | 全部子套件 pass（最大一组 129 passed），0 fail |
| `python scripts/run_evals.py` | 18 passed, 0 failed, 0 xfail, 0 review（live 层按设计跳过） |
| `python scripts/run_gates.py --always` | **80 跑过并断言 / 0 失败 / 0 跳过**，exit 0 |
| `python scripts/ci_test_shard.py --workers 4 --self-test` | OK：184 脚本 = 并发 183 + 独占 1；4 片 [46,46,46,45]，并集=全集 |
| `python scripts/ci_test_shard.py --workers 4` | **ran 184 / 184，全部通过**（见 §5 的 stderr 噪声） |
| `python scripts/test_gate_coverage.py` | PASS：磁盘 184 / 已登记 180 / 清单 4 |
| `python scripts/check_memory_contract.py` | PASS（自检 + 正式） |
| `python scripts/live_acceptance.py --doctor` | 未就绪，3 项阻塞（llm.upstream / tts.engine / asr.engine），全部源于同一订阅 |

## 3. 门禁体系现状（自算，不引用文档数字）

- `GATES` = 219 条；`ALWAYS_REQUIRED` = 80 条；磁盘 `scripts/test_*.py` = 184 个。
- 棘轮恒等式成立：**180 已登记 + 4 清单 = 184 磁盘**。
- 清单里的 4 条（`scripts/gates_unregistered.txt`）：`test_eye_calibration_analysis.py`、`test_pixel_world.py`、`test_seed_eye_expression.py`、`test_seed_eye_lids.py`。前两条缺外部产物，后两条缺第三方 VRM 资产（`.gitignore:51` 整目录忽略）。

## 4. 发现的实际问题（按严重度）

### 4.1 `run_gates.py --allow-skip` 会打印与事实相反的结论

`scripts/run_gates.py:1527-1531`：有跳过且未加 `--allow-skip` 时打印「有门禁这轮没有真的跑」并 exit 1；
加了 `--allow-skip` 就跳过这一分支，直接落到 `:1531` 的无条件 `print("结论：所有门禁都真跑过并断言。")`。

本机复现（`--allow-skip --only procedural_memory_eval,protocol`）：

```
跑过并断言 : 1
失败       : 0
跳过       : 1  procedural_memory_eval
结论：所有门禁都真跑过并断言。      <- 与上一行矛盾
EXITCODE=0
```

已入库的两份证据日志带着同一处自相矛盾：
- `docs/evidence/20261109-gates-honest-skips/gates-clean.log:436` 写「跳过 : 3」，`:441` 写「所有门禁都真跑过并断言」。
- 同目录 `gates-full.log:435` 写「跳过 : 1」，`:438` 写同一句。

### 4.2 `ci_test_shard.py` 用 locale 编解码抓子进程输出，Windows 上会丢输出

`scripts/ci_test_shard.py:48-49` 是 `subprocess.run(..., capture_output=True, text=True)`，**没有 `encoding=`**。
本机 locale 是 cp936（GBK）。子进程输出含 GBK 不可解码字节时，Python 的 `_readerthread` 抛
`UnicodeDecodeError`，`proc.stdout` 变成 `None`，`run_one` 的 tail 变空字符串。

实测（子进程写出 `0x80 0xAF`）：

```
text=True（locale）            -> stdout repr: None，tail_len=0
encoding='utf-8', errors='replace' -> stdout repr: 'ok 中文 \ufffd\ufffd'
```

后果：某个脚本失败时，CI 日志里贴不出失败原因（tail 空），只剩脚本名。
本轮跑 184/184 时 stderr 刷出的 6 条 `UnicodeDecodeError` 就是这个。
`run_gates.py:1315-1316` 已经做对了（显式 `encoding="utf-8", errors="replace"`），`ci_test_shard.py` 没跟上。

### 4.3 CI 从不调用 `run_gates.py`，24 条 ALWAYS_REQUIRED 门禁没有任何 CI 步骤执行

`.github/workflows/ci.yaml` 的步骤只有：`check_protocol.py`、`check_memory_contract.py`、`run_evals.py`、
`ci_test_shard.py --workers 4`、`npm test`。全文没有 `run_gates.py`。

自算：80 条 ALWAYS_REQUIRED 里，**24 条不被任何 CI 步骤执行**——17 条 `durable_*`（`agent-runtime/*.ts`，
`ci_test_shard.py:33` 只 glob `scripts/test_*.py`），加 7 条非 `test_*.py` 的 Python 门禁
（`process_trail`、`outbox_crash`、`evolution_quality_report`、`venv_lock`、`tmp_sweep`、`evals_report`、`evals_health`）。

其中 `process_trail` 是决策 trail 的守卫，`venv_lock` / `tmp_sweep` 是本机环境门禁 —— 它们在本地 ALWAYS 档里，
CI 结构上跑不到。

### 4.4 `test_gate_tmp_leak.py` 的抽样上限把两条浏览器门禁排除在泄漏检测外

`scripts/test_gate_tmp_leak.py:53` `MAX_GATES = 10`，`:97` 按名字排序后 `:104` 截断。
命中 `SANDBOX_PATTERN`（`:52` `browser|drill|e2e|shake|step8`）的门禁实际有 12 条，
按字典序前 10 条被采到，**`ui_nav_browser` 与 `voice_browser` 永远不进泄漏抽样**（实测枚举确认）。
新增的、排序更靠后的浏览器门禁也会被同样丢掉。

### 4.5 决策 trail 停在 2026-11-07，近两天的 19 个非合并提交没有对应记录

`docs/process/trail.jsonl` 共 14 行，最后一条 `20261107-PROC10`；`ts >= 2026-11-08` 的记录为 0。
同期 `docs/evidence/` 新增了 10 个 11-08 / 11-09 的证据目录，其中包含 `20261109-playback-done-durable-idle`
（协议事件落地）与 `20261109-gates-honest-skips`（门禁口径改判）。

`check_process_trail.py` 的 `MIN_ROWS = 4` 因此仍然通过 —— 它守的是「记录够不够多」，不守「最新决策有没有记」。
这一条正是 `ENGINEERING-DISCIPLINE.md` §3.3「证据落盘」要求的落点，机制存在但两轮没往里写。

### 4.6 协议请求文档的状态头与代码现状相反

`docs/PROTOCOL_REQUEST_playback_confirm.md:3` 写「**状态：待 Role 0 批准，未实施。**」，
`:4` 写「未改 `docs/PROTOCOL.md`、未改 `scripts/check_protocol.py`、未写任何业务代码」。

实际：`docs/PROTOCOL.md:106` 已写明「播放回执 `playback_done`（2026-11-09 Role 0 批准，最小集）」；
`server/app.py:1555` 有接收分支，`web/app.js:415` 有上报，`scripts/check_protocol.py:299` 有登记，
`scripts/test_playback_done.py` 有 8 条用例。该请求文档只被 `390b39a` 提交过一次，此后未改。

`docs/HANDOFF_20261105.md` 全文止于 §十四（2026-11-07），没有记录 11-08/11-09 这轮反转。
`docs/HANDOFF_20261105.md:104` 的 D2 裁决仍写着「不立协议门」，`:186` 仍写着「播放确认新事件……不翻案」。

### 4.7 文档里的门禁计数已经漂移

`docs/TESTING.md:29-30`：「磁盘 `test_*.py` 180 / 已登记 178 / 清单 2」（2026-11-09 口径）。
实际现在是 184 / 180 / 4。`scripts/test_doc_drift.py` 不检查门禁计数（该文件只读 ROADMAP / TESTING / CI 三处措辞、
`evals/cases` 数量、`app.py` 行号锚），所以这次漂移没人拦。

### 4.8 `docs/MEMORY_VALID_TIME.md:125` 的提交注意已过期

该行写「`server/memory/temporal.py` 未被 git 跟踪」。实际该文件在 `0f87efa`（2026-10-29）就随
valid-time 全链一起入库了（`git ls-files server/memory/temporal.py` 有输出，`git log --diff-filter=A` 指向同一提交）。
`docs/TESTING.md:659` 已把这条列为「已关闭」，但 `MEMORY_VALID_TIME.md` 本身没回填。

### 4.9 `docs/NEXT_PHASE_PLAN.md:232,237` 的 B-1 / B-3 状态已过期

该文 `:232` 写「B-1：仍未做」、`:237` 写「B-3：仍未做」。
实际：`server/app.py:152-215` 的 `_ws_handshake_allowed` 已被 4 个 WS 端点共用（`:220`、`:228`、`:1457`、`:1703`），
无 Origin 时按 Host 白名单放行（`:204-214`）。B-1 落在 `0eb3625`（2026-10-31），B-3 落在 `655e4c8`（2026-11-07）。

### 4.10 `procedural_memory_eval` 在任何环境下都跑不起来

`scripts/test_procedural_memory_eval.py:30-33` 写明：20/24 条用例需要 `human_card` 金标准，
2026-11-09 Role 0 裁决「维持 SKIP，不合成夹具」（自编参考答案等于自己出题自己判卷）。
该门禁在 `GATES` 里但不在 `ALWAYS_REQUIRED`，实跑确认 `[SKIP] procedural_memory_eval`。
属于政策阻塞、不是能力阻塞，但每轮全跑都会留一条 SKIP。

## 5. CI 近期失败史

`gh run list --limit 25` 近 25 次里 7 次 failure：

| 时间 | sha | 失败原因（`gh run view --log-failed`） |
| --- | --- | --- |
| 2026-11-09T05:55Z | `a2eb25d` | `test_doc_drift.py`（`TESTING.md` 的 `_start_durable_if_enabled:1872` vs `app.py` 实际 1874）+ `test_memory_fastloop.py`（同步对照最长停顿 19.93 ms < 20 ms 阈值） |
| 2026-11-08T21:35Z | `90357b8` | `test_live_acceptance.py`（CI 缺 uvicorn） |
| 2026-11-08T15:27Z | `0bb93eb` | `test_poteto_loading.py`（`.agents` bundle 缺失时未 SKIP） |
| 2026-11-08T01:57Z | `c47d2d0` | 同上 `test_poteto_loading.py` |
| 2026-11-07T08:23Z | `e036a14` | 主 CI + Pixel World Guard 同时红 |
| 2026-11-01T04:38Z | `9bf1456` | 早于当前 HEAD 44 个提交 |

下一次 `1816007` 只改了 `docs/TESTING.md` 的行号锚，`test_memory_fastloop` 就绿了 ——
`604994e` 把比率判据换成了行为判据，但 `:151` 的 `sync_worst > 20` 这条**墙钟阈值**留了下来，
CI 负载下仍会假红。本机连跑 4 次实测同步对照最长停顿 30.07 / 38.16 / 30.10 / 31.71 ms，余量不大。

## 6. 记忆检索现状（读文档 + 读码，未重跑测量）

- 机制归因已坐实：8/8 漏检的 `sim = 0.0000`、与 `key+value` 零共享 2-gram，卡在
  `server/memory/retriever.py` 的 `min_similarity` 硬门；`importance/recency/confidence/utility` 全在门后。
- 同义桥已接入生产（`retriever.py:227-257`：首轮零命中才触发救援，查询文字变了，门 / 权重 / 排序 / `RECALL_FLOOR` 都没动）。
- 词表 `config/memory_synonyms.tsv` 在库、6 条，每条带「# 出处」。
- 读数 24/32 (0.750) → 28/32 (0.875)，控制组 1/6 不变，N=32 **纯合成标注集**。
  生产 `min_score=0.12` 比实验严，真实环境只会更低。**这个数不得对外当检索质量基准**（仓库自己在
  `docs/MEMORY_SYNONYMS_PRODUCTION_20261109.md:7-9` 就这么写）。
- 生产代码里**没有**召回地板线。`RECALL_FLOOR = 0.75` 只在 `scripts/test_memory_retrieval_quality.py:125`，
  该脚本自承「不是生产召回基准」。
- 剩下的 4 条漏检是省略 / 错字 / 词义推理 —— 词表扩不动，指向 embedding 或 ASR 纠错。

## 7. 慢环与开关现状

`config/agents_roster.yaml`：

| id | adapter | 行 | 能否真执行 |
| --- | --- | --- | --- |
| `scribe` / `builder` / `scout` | `manual` | `:53` / `:63` / `:77` | 否，只组密件 |
| `claude` / `workbuddy` / `dsh` | `cli_subproc` | `:95` / `:120` / `:141` | 是，但都要本机网关 `127.0.0.1:15722`（本轮未起） |
| `sandbox` | `cli_subproc` | `:169` | 是，纯标准库、不联网 |
| `mcp_echo` | `mcp` | `:191` | 是，本地 stdio |
| `codex` | `cli_subproc` | `:208` | `enabled: false`（生产隔离未定） |

慢环开关：`durable.enabled: false`（`:242`，`.env` 里 `XIAOXI_DURABLE_ENABLED=1` 点亮，
`XIAOXI_DURABLE_SWITCH_AGENTS=none` → 绑定 0 个 Worker）；`durable.shadow: false`（`:248`）；
`lease_mode` 未设，代码默认 `dry_run`。

`config/pacing.yaml`：`proactivity.default_enabled: false`（`:33`）、`require_authorization: true`（`:37`，已真接线，
有 `evals/cases/proactive-authorization-runtime-offline.yaml` 钉住）、`ambient.enabled: true`（`:79`）。
`ambient.respect_typing: true`（`:85`）**声明但未接线**，注释已自承。

代码里没有 `TODO` / `FIXME` / `XXX` / `HACK` 标记。真实待办全在 Markdown 复选框里：
`docs/TASKS.md` 有 14 个未勾项（A-1…A-5、U-1…U-6，以及 `:153` 三个内置 Worker 未接真、`:157` 语音路径「收工/打回」只认单）。

## 8. 资源与卫生

- `tmp/`：22 项、88.8 MB，ceiling 113，`tmp_sweep` PASS。
- worktree：14 个，其中 8 个在 `D:\project\neurosama-wt-*`（合计约 422 MB），分支全部已并入 master
  （`git merge-base --is-ancestor` 逐条确认）。`git worktree prune --dry-run` 无输出，即无孤儿记录。
- `git worktree` 里 `merge/memory-schema-guard` 与 master 差 1 个提交，内容是 master 的旧快照 + 一个 merge commit，
  `git diff master merge/memory-schema-guard --stat` 显示的是「master 有而它没有」的 16 个文件。
- 未推送提交：0。open PR：0。

## 9. 未验证清单

| 项 | 状态 | 原因 |
| --- | --- | --- |
| live 评测层（7 条 live 用例） | **未验证** | StepFun 订阅未激活（400）+ 按量付费额度耗尽（402）。不是代码问题 |
| 真环境验收 B1–B3 | **已验证**（同日后续） | 换 Fish + 讯飞做替身后跑通，`overall: pass`，见 `docs/evidence/20261112-live-acceptance/` |
| `valid-time --live` 复验 | **未验证** | 同一订阅阻塞（`docs/MEMORY_VALID_TIME.md:113` 记着根因与恢复命令） |
| TTS 音色 / 情绪听感、ASR 字错率 | **未验证** | 需要真引擎与真人语料；仓库已明确禁止用合成音频冒充实测 |
| 10 条浏览器门禁本轮表现 | **未验证** | 需要 Playwright + 本地服务 + VRM 资产，本轮未起服务 |
| 17 条 `durable_*` 在 CI 的表现 | **未验证** | CI 从不执行它们（§4.3） |
| 记忆检索真实环境召回 | **未验证** | 现有读数全是 32 条合成用例；真实库 `memories` 只有 3 行 |

---

## 10. 后续（同日补充）

健康检查跑完后的同一轮里，上面 §4 的六个问题之外又挖出四个「绿灯比事实多说了话」，
并且**第一次真跑通了真环境验收**。详见
[`docs/evidence/20261112-live-acceptance/VERIFICATION.md`](../20261112-live-acceptance/VERIFICATION.md)：

| # | 问题 | 修法 |
| --- | --- | --- |
| 4.1 | doctor 把「LLM 通但 `/audio/speech` 不存在」判成就绪 | stepaudio 分支加专属端点探测 |
| 4.2 | `TTS_ENGINE=fish` 在服务端**静默退回 edge_tts** | 服务端加真 fish 分支，缺 Key 报错不换引擎 |
| 4.3 | Fish 会**念出** instruction 的后半截（base 与得意 3/3） | 只取第一子句 |
| 4.4 | B4 硬删除**没被断言**；`int(x or -1)` 把合法的 0 判成 -1；`{**list}` 让 B4 崩掉 | 补断言 + `_as_int()` + 修解包 |

另更正本文件 §1 的一处结论：**Fish TTS 在 API 层非确定**（缓存是唯一让重复请求字节一致的东西）。
当时用 `synthesize_profile` 测出「5/5 相同」，实际那是 **LRU 缓存命中**，第二次起根本没发 HTTP。

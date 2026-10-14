# 程序性记忆 V2：先验证归纳

日期：2026-10-14。依据：[首轮试跑](PROCEDURAL_MEMORY_RESULTS_20261014.md)中三张卡全拒绝、来源资料重复及源码版本漂移的实际问题。

本版只验证“来源经历能否形成合法、有依据、条件完整的卡”，不执行目标任务。继续使用现有 `LLM.complete_raw`、领域服务、证据解析及 `NoteStore`，没有接入生产或引入 Agent 框架。

执行状态：已完成三次真实请求及独立 Codex 定性复核。输入成本下降，1 张卡通过程序校验，但含未支持的恢复建议；另 2 张截断或空输出。见[本轮结果](PROCEDURAL_MEMORY_INDUCTION_RESULTS_20261014.md)。人类评分保持空值。

## 三项变化

来源压缩是表示变换：公开 note 对象和正文进入内容表，过程中的原位置改为引用。展开后必须逐值等于输入；动作顺序、原文、目标、观察 ID、版本、失败结果和确认都保持。压缩结果更长时保留原始布局。程序不替模型写经验摘要。

卡片保留原字段，限制输出规模：scope 最多 120 字符；requires/advice 各 1–4 项、每项最多 100 字符；evidence 为 2–4 个不同的实际观察 ID，每项只能包含 `observation_id`。拒绝额外字段、重复 JSON 键和非有限数；依据不足可输出 `card:null`。长度上限不能保证一定在 1200 token 内结束，提示仍鼓励短卡。

固定白名单保存 13 份源码/规格的完整字节。验证稳定复制、文件哈希及外部记录的 manifest 哈希，再从副本启动孩子进程；运行前、请求前、结束后复核。副本不含 `.env`、用户数据库或未使用的 oracle 文件，临时存储及报告位于副本外。报告记录实际加载的代码路径。Python 版本记录在 manifest 中，服务商与安装依赖没有冻结为离线可执行镜像。

提示要求比较用户目标与实际前后文本，区分工具成功、合成确认和语义正确；不能把截断视作完整、固定版本读回视作最新版本，或将共同契约当成从一次经历证明的新结论。提示不直接提供三类卡的正确答案。

## 固定预算与结果解释

三个来源组，每组最多一次请求，共最多 3 次；每次请求输出最多 1200 token、超时 45 秒、`max_attempts=1`，没有重试或人工修卡。默认只生成计划；来源制作模式模型调用为零。没有目标/G 调用，也不会把首轮剩余额度用作本次补抽。

记录公开输出、finish reason、usage、解析、固定校验、实际保存及重开读回；拒绝、空输出、到达长度上限和弃答分别保留。程序接纳只证明格式和证据引用通过检查，语义分数与人类审核保持 null，独立 Codex 定性复核另存。

本版同时改变输入表示、提示和长度契约，源码也按当前项目重新冻结；它是新的开发诊断，不能归因于某个单独改动，更不能把三张合法卡当作已证明可迁移收益。下一阶段应冻结卡/提示/规则，用新的来源家族及未用于修改的目标检验使用效果。

## 入口

计划，零模型调用、零领域存储：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts\eval_procedural_memory_induction.py
```

冻结副本中的真实来源制作，零模型调用：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts\eval_procedural_memory_induction.py --prepare-sources
```

真实归纳要求显式连接配置及专用环境变量 `PROCEDURAL_MEMORY_EVAL_API_KEY`；执行器不读取 `.env`，不回退到生产 key：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts\eval_procedural_memory_induction.py --live --provider compatible --base-url https://YOUR-ENDPOINT/v1 --model YOUR-MODEL
```

每次写入独立 `eval/research/procedural_memory_induction_v2_*/`，含 `launcher.json`、`report.json`、`snapshot/`。`--run-directory` 可指定新的目录，已有目录被拒绝。旧试验不修改、不追改成绩。真实运行的操作员可单独读取项目连接配置并转交专用环境变量；凭据不进参数、输出、报告或快照。

## 离线验收

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts\test_procedural_memory_source_projection.py
.\.venv\Scripts\python.exe -X utf8 -B scripts\test_procedural_memory_eval_snapshot.py
.\.venv\Scripts\python.exe -X utf8 -B scripts\test_procedural_memory_induction.py
```

34 项检查运行，33 项通过、1 项因 Windows 缺少软链接创建权限跳过；另有不依赖此权限的 reparse 拒绝检查。覆盖真实公开来源往返、隐藏正文边界、引用碰撞和篡改，冻结源码与入口加载位置、凭据/oracle不复制、三次预算、非法字段和不修卡。脚本模型输出均标为 `synthetic_provider_output_for_contract_test`，不计作真实归纳收益。

- [V2 归纳入口](../scripts/eval_procedural_memory_induction.py)
- [可逆来源投影](../scripts/procedural_memory_source_projection.py)
- [固定源码快照](../scripts/procedural_memory_eval_snapshot.py)
- [整体评测流程](PROCEDURAL_MEMORY_EVAL.md)

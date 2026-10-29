"""评测报告层：读 evals/results/ 的运行快照，出通过率、配对差异与成本。

    .\\.venv\\Scripts\\python.exe scripts\\evals_report.py
    .\\.venv\\Scripts\\python.exe scripts\\evals_report.py --json evals\\results\\report.json

分层语义（见 docs/EVAL_MECHANISM_PLAN.md）：
- 本脚本属于「报告」区：只读快照、只出报告，**不计入任何退出码**，失败也退出 0。
- 「报告跑得完」本身由 run_gates 的 evals_report 门禁断言（可执行性，不是内容）。
- 只读：不写、不删、不轮转 evals/results/。删除本地证据属数据红线。
  唯一例外是 `--json` 显式指定路径时写那一份摘要，且不许指向快照目录之内
  （见 main() 的守卫）。

为什么需要它：run_evals.py 每次运行只落一份带时间戳的快照，全仓没有任何读取方；
本脚本是第一个消费者，提供趋势、配对比较与成本记账三样原本缺失的东西。

通过率的两个数必须分开看：
- 平均通过率 = passed_runs / total_runs，回答「有能力吗」；
- 全过概率   = 平均通过率 ^ total_runs（pass^k 估计），回答「稳定吗」。
混成一个数字时，「有能力但不稳定」与「能力不足」不可区分。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = ROOT / "evals" / "results"

# 趋势窗口：趋势视图按**用例自己**最近 N 次出现聚合通过情况。
#
# 为什么按用例取窗、不按文件取最近 N 份快照：offline 与 live 的运行频率差一个
# 量级（offline 每天多次，live 靠手动 --live），按文件取窗会让 live 用例在窗口里
# 一次都没出现、整节空转；按用例取窗则两类用例各自拿到 N 个点，横向可比。
#
# 为什么 N=12：① 本报告是「每次跑完就看的那一个」，窗口要小到读得出方向；
# 全史是 evals_health.py 的职责（它按天给失效日期分布与刷穿检测）。② 判定
# 「一直抖 / 最近退化」至少需要 3 个点，12 给足余量又不至于每行溢出终端。
# 调大看更长历史但每行更长；调小对突变更敏感，但「长期稳定」的证据更弱。
TREND_WINDOW = 12

# review 队列的扫描窗口：按份数取最近 N 份快照。
#
# 为什么比 TREND_WINDOW 大得多：review 轮次只出现在 live 用例上（judge 三态），
# 而 live 要手动 --live 才跑、频率远低于 offline。按小窗口取，队列会长期空转，
# 而「判定器说不清、需人工复核」的事不会因为没人看就消失。取 50 份足以覆盖
# 若干次 live 运行，又不至于把报告拖到读不动。
#
# 与 TREND_WINDOW 的另一处区别：TREND_WINDOW 回答"最近怎么样"（看方向），
# 本窗口回答"有什么事欠着"（看欠账）——后者本质上要更大，否则欠账会被窗口
# 切掉、看起来像不存在。
REVIEW_QUEUE_WINDOW = 50

# Windows GBK 控制台曾让同类报告脚本崩过（run_gates 的 evolution_quality_report
# 注释记有此事）；强制 UTF-8 输出，宁可替换字符也不要崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老解释器没有 reconfigure
    pass


def load_snapshots(results_dir: Path) -> tuple[list[dict], list[str]]:
    """按文件名（时间戳前缀）排序读快照。损坏的文件跳过并告警，不崩。"""
    snapshots: list[dict] = []
    broken: list[str] = []
    if not results_dir.is_dir():
        return snapshots, broken
    for path in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 半截写入 / 手工改坏都算损坏
            broken.append(f"{path.name}（{type(exc).__name__}）")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            broken.append(f"{path.name}（结构不符合 {{at, results[]}}）")
            continue
        snapshots.append({
            "file": path.name,
            "at": str(data.get("at") or path.stem),
            # 被测对象标识（R20 新增）。旧快照没有该字段 → None，按"未知"处理；
            # 读取方一律容错，不因为缺字段崩或红。
            "subject": data.get("subject") if isinstance(data.get("subject"), dict) else None,
            "cases": {c["id"]: c for c in data["results"]
                      if isinstance(c, dict) and c.get("id")},
        })
    return snapshots, broken


def case_metrics(case: dict) -> dict:
    """从一条用例记录算通过率与 token。数据全部来自已有快照，零新采集。"""
    runs = case.get("runs") or []
    total = int(case.get("total_runs") or len(runs) or 0)
    passed = int(case.get("passed_runs")
                 or sum(1 for r in runs if r.get("passed")))
    avg = (passed / total) if total else 0.0
    # review 轮次：判定器说不清（judge 未配置/失败/与正则不一致）。旧快照没有
    # verdict 字段，按 passed 反推——review 只会出现在 T5 之后的新快照里。
    reviewed = sum(1 for r in runs
                   if (r.get("verdict") or ("pass" if r.get("passed") else "fail")) == "review")
    tokens = 0
    for run in runs:
        for meta in run.get("turn_meta") or []:
            tokens += int(meta.get("completion_tokens") or 0)
            tokens += int(meta.get("retry_completion_tokens") or 0)
    return {
        "layer": str(case.get("layer") or "offline"),
        "xfail": bool(case.get("xfail")),
        "passed_runs": passed,
        "total_runs": total,
        "reviewed_runs": reviewed,
        "avg": avg,
        "all_pass": bool(total) and passed == total,
        # pass^k 估计：k 轮采样全部通过的概率。avg^0 定义为 1.0（无轮次时无从判断）。
        "pass_k": avg ** total if total else 1.0,
        "tokens": tokens,
        "failed_checks": [
            f"轮{r.get('run')} [{c.get('type')}:{c.get('target', 'reply')}] {c.get('detail', '')}"
            for r in runs if run_verdict(r) == "fail"
            for c in (r.get("checks") or []) if not c.get("pass")
        ],
        # review 轮次的明细单列：它是"判定器说不清"，既不是失败也不是通过
        # （run_evals.classify_results 的口径）。曾用 `not passed` 过滤，于是
        # review 轮次被列进「失败明细（供排障）」——而同一份快照 run_evals 的
        # 结论是 0 failed / 1 review / exit 0，报告层在说自己失败。
        "review_checks": [
            f"轮{r.get('run')} [{c.get('type')}:{c.get('target', 'reply')}] {c.get('detail', '')}"
            for r in runs if run_verdict(r) == "review"
            for c in (r.get("checks") or []) if not c.get("pass")
        ],
    }


def safe_metrics(case: dict) -> dict | None:
    """case_metrics 的防崩包装：字段类型损坏时返回 None，不伪造指标。

    为什么需要它：JSON 合法但字段类型不符（total_runs="abc"、
    passed_runs="abc"、runs="abc"、turn_meta[].completion_tokens="x"）时
    case_metrics 会抛 ValueError/AttributeError。报告层的契约是「永不因内容
    失败」——而 evals_report 门禁在 run_gates 的 ALWAYS_REQUIRED 里，一次崩溃
    就是门禁红。历史上这条路径没防护（HEAD 同样崩），本轮补上。

    为什么返回 None 而不是一份全 0 指标：伪造会把「数据损坏」显示成
    「没跑」，而两者处置相反——前者要人工修快照，后者只是等下一次运行。
    """
    try:
        metrics = case_metrics(case)
    except Exception:
        return None
    # R74：语义不可能也当损坏。类型合法但 passed_runs > total_runs（或负值）时，
    # avg = passed/total 会算出 >1、pass_k = avg**total 更 >1——概率越过 1 是物理
    # 不可能，显示成「全过概率 225%」就是把数据损坏伪装成正常指标（R16 同款哲学）。
    # 真实快照 6054 条记录里 passed>total / 负值均 0 出现（run_case 不产出），
    # 但手改快照或并发 bug 可能引入；这类不进通过率、进「数据损坏」节。
    if not 0 <= metrics["passed_runs"] <= metrics["total_runs"]:
        return None
    return metrics


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def run_verdict(run: dict) -> str:
    """一轮的三态判定。旧快照没有 verdict 字段，按 passed 反推。

    与 evals_health.run_verdict 同款同因：review 只出现在 T5（judge 三态）
    之后的新快照里，旧快照按 passed 反推不会误判成 review。

    R75：越界值归一成 fail。原样返回时，一个非法 verdict（手改快照，或将来加了
    第四态却忘了同步下游两处筛选）会同时不进 failed_checks 与 review_checks——
    那条轮次从两个明细里静默消失，报告层于是既不说它失败也不说它说不清。
    与 run_evals.classify_results 的未知态口径一致：按失败算，别让漏判静默变成放行。
    """
    verdict = str(run.get("verdict") or ("pass" if run.get("passed") else "fail"))
    return verdict if verdict in ("pass", "fail", "review") else "fail"


def safe_id(cid: str) -> str:
    """显示用 id：以 SKIP 开头时加引号。

    run_gates 的 skip_regex 是 r"^\\s*SKIP"（re.M，大小写敏感）——任何行首
    （允许前导空白）的 SKIP 都会让门禁判成「跳过」，而 ALWAYS_REQUIRED 里
    跳过即失败。本报告的 evals_report 门禁正在 ALWAYS_REQUIRED 里，所以用例 id
    以 SKIP 开头时裸打印会 silent 把门禁搞失败。与 evals_health.safe_id 同款同因。
    """
    text = str(cid)
    return f"「{text}」" if text.lstrip().startswith("SKIP") else text


def fmt_at(at: str) -> str:
    """'20261026T072915Z' → '2026-10-26 07:29'；解析不了原样返回。"""
    try:
        parsed = datetime.strptime(str(at), "%Y%m%dT%H%M%SZ")
    except (ValueError, TypeError):
        return str(at)
    return parsed.strftime("%Y-%m-%d %H:%M")


def classify_trend(series: list[bool]) -> str:
    """把一个通过序列归类。五个标签，判据写死在这里供复核。

    数据不足  <3 个点——无法区分「稳定」与「没跑够」（与 TREND_WINDOW 注释
              同口径：判定「一直抖 / 最近退化」至少需要 3 个点）。
    长期稳定  全通过。offline 用例本来就该在这里；live 用例落在这里反而要
              回头查断言够不够狠（H1 的防过拟合节也在说这件事）。
    持续失效  全失败。这是当前最该先看的一类。
    最近退化  开头通过、结尾失败——**当前处于失效态**，先查回归。注意振荡
              形状（如 ●○●○，好坏交替）也落在这里：它确实当前失效，「查
              回归」这个动作没错，但别期待找到一次单调的劣化点。
    一直抖    其余混合形态（含「坏过又好回来」）。抖动说明采样不足或被测
              对象本身不确定，加 runs 比调 Prompt 更对症。

    只按首末两点与全量判定，不拟合中间形状：判据越简单越可复核；形状细分
    留给 ●○ 序列本身，人眼读符号比读标签快（汇总行也带序列，理由同此）。

    两个**已知**的取舍写在这里，免得后人误以为漏报：
    · ●●●○○●●●（中间坏过、又恢复、当前是好的）判「一直抖」而非「退化」——
      标签的职责是分诊「现在该干什么」，当前是好的就没有需要立刻行动的回归；
      为已自愈的窗口查回归成本高收益低。
    · ●○●○（好坏交替、当前失效）判「最近退化」而非「一直抖」——因为它当前
      确实失效，「查回归」是可执行的下一步；若改判「一直抖」，当前失效就得
      不到行动提示。
    两者都靠 ●○ 序列兜底：标签负责分诊，序列负责给形状。
    """
    if len(series) < 3:
        return "数据不足"
    if all(series):
        return "长期稳定"
    if not any(series):
        return "持续失效"
    if series[0] and not series[-1]:
        return "最近退化"
    return "一直抖"


def trend_marks(series: list[bool]) -> str:
    """通过序列的可视化：● 全过、○ 有失败。判定只看这两个符号。"""
    return "".join("●" if ok else "○" for ok in series)


def build_trend(snapshots: list[dict]) -> dict:
    """趋势视图数据源：每用例最近 TREND_WINDOW 次出现的通过序列与判定。

    series 只收非 xfail 出现：xfail 是「已知失败、照常跑不计失败」
    （run_evals.py 的口径），对趋势既不是正面也不是负面证据，混进去会把
    「一直抖」算成「修复中」。xfail 次数单列在 xfail_n。

    单次计算包 try：快照 JSON 合法但字段类型损坏时 case_metrics 会抛，而
    报告层的契约是「永不因内容失败」——所以跳过该次继续，不崩、不红。
    """
    layer_of: dict[str, str] = {}
    for snap in reversed(snapshots):  # 从新到旧，取每个用例最近一次已知层
        for cid, case in snap["cases"].items():
            layer_of.setdefault(cid, str(case.get("layer") or "offline"))

    trend: dict[str, dict] = {}
    for cid, layer in layer_of.items():
        series: list[bool] = []
        contributing: list[dict] = []   # 与 series 逐点对应的快照，用于查被测对象是否变过
        xfail_n = 0
        last_at = ""
        for snap in snapshots:
            case = snap["cases"].get(cid)
            if not case:
                continue
            last_at = snap["at"]
            if case.get("xfail"):
                xfail_n += 1
                continue
            try:
                metrics = case_metrics(case)
            except Exception:
                continue  # 字段损坏的那次无从判断，不计入序列
            if not metrics["total_runs"]:
                # 没有轮次就无从判断通过与否：不计入序列。否则会把「没跑」
                # 当成「失败」，凭空造出一条退化曲线。
                continue
            series.append(metrics["all_pass"])
            contributing.append(snap)
        window = series[-TREND_WINDOW:]
        window_snaps = contributing[-TREND_WINDOW:]
        trend[cid] = {
            "layer": layer,
            "series": window,
            "pass_n": sum(1 for ok in window if ok),
            "fail_n": sum(1 for ok in window if not ok),
            "xfail_n": xfail_n,
            "last_at": last_at,
            "verdict": classify_trend(window),
            # 窗口内被测对象变过 → 序列跨模型，●○ 曲线本就不连续，
            # 判定（尤其「最近退化」）要打折看。R20 新增。
            "subject_changed": subject_changed_within(window_snaps),
        }
    return trend


def subject_key(subject: dict | None) -> tuple | None:
    """被测对象的身份键（provider / base_url / model）。None = 未知。

    旧快照没有 subject 字段 → None，按"未知"处理；读取方一律容错。
    """
    if not subject:
        return None
    return (str(subject.get("provider") or ""),
            str(subject.get("base_url") or ""),
            str(subject.get("model") or ""))


def subject_desc(subject: dict | None) -> str:
    """人类可读的被测对象描述（不含凭据）。"""
    if not subject:
        return "未知（旧快照无 subject 字段）"
    text = (f"{subject.get('provider')}/{subject.get('model')}"
            f" @ {subject.get('base_url')}")
    judge = subject.get("judge")
    if judge:
        text += f"；judge={judge.get('provider')}/{judge.get('model')}"
    head = str(subject.get("git_head") or "")
    if head:
        text += f"；HEAD {head[:8]}"
    return text


def subject_changed_within(series_snapshots: list[dict]) -> bool:
    """窗口内的快照里被测对象是否变过。用于趋势视图的跨模型断点标注。"""
    keys = [subject_key(s.get("subject")) for s in series_snapshots]
    known = [k for k in keys if k is not None]
    return len(set(known)) > 1


def build_review_queue(snapshots: list[dict]) -> dict:
    """跨快照汇总「有待人工复核」的用例。

    为什么需要：review 的语义是「判定器说不清（judge 未配置 / 调用失败 /
    与正则不一致），需人工复核落盘的原始回复」。但日常跑 evals_report 只看
    最新快照，而最新快照几乎总是纯 offline 跑（live 要手动 --live）——于是
    这个欠账在日常报告里完全不可见，人工得自己翻历史快照。本函数把它汇总出来。

    每条给出：用例、review 轮次 / 总轮次、**首次**与**最近一次** review 的时间、
    以及**定位路径**（快照文件名 + 轮次号）——人工复核要读的是落盘的原始回复
    （`runs[].turns[].reply`），不是判定结果，所以必须告诉人去哪个文件第几轮找。

    首次与最近一次都要，是为了分诊：只有「最近一次」无法区分「今天新出现的」
    和「已欠了很久反复出现的」——两者优先级相反。

    只读、不写。窗口见 REVIEW_QUEUE_WINDOW（比 TREND_WINDOW 大，理由见常量处）。
    `first_review_at` 是**窗口内**首次，不是全史首次——窗口外的更早出现不追。
    """
    recent = snapshots[-REVIEW_QUEUE_WINDOW:]
    queue: dict[str, dict] = {}
    for snap in recent:
        for cid, case in snap["cases"].items():
            runs = case.get("runs") or []
            # 只认 dict 形态的 run：快照被手改坏时 runs 里可能混进字符串等，
            # run_verdict 会抛 AttributeError——而报告层契约是「永不因内容失败」。
            review_runs = [i + 1 for i, r in enumerate(runs)
                           if isinstance(r, dict) and run_verdict(r) == "review"]
            if not review_runs:
                continue
            entry = queue.setdefault(cid, {
                "layer": str(case.get("layer") or "offline"),
                "review_runs": 0, "total_runs": 0,
                # setdefault 只在首次创建时求值，而快照按时间正序遍历，
                # 所以这里天然是「窗口内首次出现」的时间
                "first_review_at": snap["at"],
                "last_review_at": "", "locations": [],
            })
            entry["review_runs"] += len(review_runs)
            entry["total_runs"] += len(runs)
            # 快照按时间正序遍历，后写的就是更近的
            entry["last_review_at"] = snap["at"]
            entry["locations"].append({
                "file": snap["file"], "at": snap["at"], "runs": review_runs,
            })
    return queue


def render(snapshots: list[dict], broken: list[str], results_dir: Path) -> str:
    lines: list[str] = []
    lines.append("=== 评测报告（报告层，不计入门禁退出码） ===")
    lines.append(f"快照目录: {results_dir}（只读，不删不轮转；`--json` 显式指定路径时除外）")
    lines.append(f"快照数量: {len(snapshots)} 份")
    for name in broken:
        lines.append(f"警告: 跳过损坏的快照 {name}")

    if not snapshots:
        lines.append("无历史数据：目录里没有可读的快照。")
        lines.append("（新克隆上 evals/results/ 被 .gitignore 忽略，本就是空的；"
                     "先跑 .\\.venv\\Scripts\\python.exe scripts\\run_evals.py 生成第一份。）")
        return "\n".join(lines)

    latest, previous = snapshots[-1], (snapshots[-2] if len(snapshots) > 1 else None)
    lines.append(f"最新快照: {latest['file']}（at={latest['at']}）")
    if previous:
        lines.append(f"上次快照: {previous['file']}（at={previous['at']}）")
    else:
        lines.append("上次快照: 无——仅一份快照，本次不出配对差异。")

    latest_cases = latest["cases"]
    layers: dict[str, int] = {}
    for case in latest_cases.values():
        layer = str(case.get("layer") or "offline")
        layers[layer] = layers.get(layer, 0) + 1
    lines.append("最新快照构成: " + " / ".join(
        f"{k} {v} 条" for k, v in sorted(layers.items()))
        + "（offline 运行只落 offline 用例，--live 运行才落 live 用例；跨层比较要看这一行）")

    lines.append("")
    lines.append("--- 通过率表（最新快照） ---")
    lines.append(f"{'用例':38} {'layer':7} {'轮通过':>6} {'平均':>7} "
                 f"{'全过概率':>8} {'review':>6} {'xfail':>5} {'tokens':>8}")
    metrics: dict[str, dict] = {}
    corrupt: list[str] = []
    for case_id, case in latest_cases.items():
        m = safe_metrics(case)
        if m is None:
            corrupt.append(case_id)
            continue
        metrics[case_id] = m
        lines.append(
            f"{safe_id(case_id[:38]):38} {m['layer']:7} "
            f"{m['passed_runs']}/{m['total_runs']:<4} {pct(m['avg'])} "
            f"{pct(m['pass_k'])} {m['reviewed_runs']:>6} "
            f"{('xfail' if m['xfail'] else ''):>5} "
            f"{(m['tokens'] if m['tokens'] else '—'):>8}")
    if corrupt:
        lines.append("")
        lines.append("--- 数据损坏（最新快照内，字段类型不符，无法计算通过率） ---")
        for cid in corrupt:
            lines.append(f"  {safe_id(cid)}：total_runs / passed_runs / runs 之一类型不符，"
                         "请人工核对这份快照，或重新生成")
    lines.append("全过概率 = 平均通过率 ^ 轮数（pass^k 估计；total_runs=0 时定义为 1.0，"
                 "即零轮次无从判断、不判低）："
                 "平均高而全过概率低 = 有能力但不稳定；两者都低 = 能力不足。")
    if any(m["reviewed_runs"] for m in metrics.values()):
        lines.append("review = 判定器说不清（judge 未配置 / 调用失败 / 与正则不一致），"
                     "既不计通过也不计失败，需人工复核落盘的原始回复。")

    failures = {cid: m["failed_checks"] for cid, m in metrics.items()
                if m["failed_checks"]}
    if failures:
        lines.append("")
        lines.append("--- 失败明细（最新快照，供排障；原始回复在快照的 runs[].turns） ---")
        for cid, details in failures.items():
            lines.append(f"  {safe_id(cid)}")
            for d in details[:6]:
                lines.append(f"      {d[:160]}")
            if len(details) > 6:
                lines.append(f"      …另有 {len(details) - 6} 条")

    reviews = {cid: m["review_checks"] for cid, m in metrics.items()
               if m.get("review_checks")}
    if reviews:
        lines.append("")
        lines.append("--- review 明细（判定器说不清，**不计失败**、退出码不看它；"
                     "需人工复核落盘的原始回复） ---")
        for cid, details in reviews.items():
            lines.append(f"  {safe_id(cid)}")
            for d in details[:6]:
                lines.append(f"      {d[:160]}")
            if len(details) > 6:
                lines.append(f"      …另有 {len(details) - 6} 条")

    lines.append("")
    lines.append("--- 与上次的配对差异 ---")
    if not previous:
        lines.append("  （无上次快照，跳过）")
    else:
        # 换了被测对象就必须说清楚。否则读者会把换模型造成的曲线断点当成退化，
        # 而两者处置相反：退化要查回归，换模型只是被测对象变了、曲线本就不连续。
        if subject_key(latest["subject"]) != subject_key(previous["subject"]):
            lines.append("  **注意：两次快照的被测对象不同，下面的一切差异都**不**"
                         "能直接解读为退化或改善——曲线在这里本就不连续。**")
            lines.append(f"    上次: {subject_desc(previous['subject'])}")
            lines.append(f"    本次: {subject_desc(latest['subject'])}")
        elif subject_key(latest["subject"]) is None:
            lines.append("  （两次快照都没有 subject 字段，无法确认被测对象是否一致；"
                         "引用本节结论时请谨慎）")
        prev_cases = previous["cases"]
        for case_id, m in metrics.items():
            if case_id not in prev_cases:
                lines.append(f"  新   {safe_id(case_id)}  {pct(m['avg'])}")
                continue
            pm = case_metrics(prev_cases[case_id])
            if abs(pm["avg"] - m["avg"]) < 1e-9:
                mark = "="
            elif m["avg"] > pm["avg"]:
                mark = "↑"
            else:
                mark = "↓"
            lines.append(f"  {mark} {safe_id(case_id[:36]):36} {pct(pm['avg'])} → {pct(m['avg'])}"
                         + (f"  （轮数 {pm['total_runs']}→{m['total_runs']}，"
                            "注意 run 数变化不是退化）"
                            if pm["total_runs"] != m["total_runs"] else ""))
        for case_id in prev_cases:
            if case_id not in metrics and case_id not in corrupt:
                lines.append(f"  缺   {safe_id(case_id)}（上次有、这次没有：可能是只跑了 offline 层）")

    lines.append("")
    lines.append(f"--- 趋势（每用例最近 {TREND_WINDOW} 次**非 xfail**出现的通过情况） ---")
    lines.append("● 该次全部通过  ○ 该次有失败；判定只看 ●/○，xfail 与损坏字段不计入判定")
    lines.append("口径：序列列宽随 TREND_WINDOW 定，所以列不会截断；但**序列本身只含"
                 "最近 TREND_WINDOW 次出现**，更早的点不出现在趋势里（全史归 "
                 "evals_health.py）。「xfail」列是全史计数，与窗口内的序列不同口径。")
    lines.append(f"{'用例':38} {'layer':7} {'点':>3} {'序列':<{TREND_WINDOW}} "
                 f"{'xfail':>5} {'最近一次':17} 判定")
    trend = build_trend(snapshots)
    for cid in sorted(trend, key=lambda c: (trend[c]["layer"], c)):
        info = trend[cid]
        lines.append(f"{safe_id(cid[:38]):38} {info['layer']:7} {len(info['series']):>3} "
                     f"{trend_marks(info['series']):<{TREND_WINDOW}} "
                     f"{info['xfail_n']:>5} {fmt_at(info['last_at']):17} "
                     f"{info['verdict']}"
                     + ("（窗口内换过被测对象，曲线不连续）"
                        if info.get("subject_changed") else ""))
    by_verdict: dict[str, list[str]] = {}
    for cid, info in trend.items():
        by_verdict.setdefault(info["verdict"], []).append(cid)
    # 汇总行必须带 ●○ 序列。「一直抖」是个杂项桶——「坏过又好回来」（○●●●，
    # 当前是好的）和「一直没好」（●○●○●，当前仍不稳）都落在里面，而两者
    # 处置相反：前者只需知道它自愈了，后者要加 runs 或查被测对象。只看
    # 「一直抖: 3 条 — a、b、c」会把这两类混为一谈，所以每条后面跟序列。
    for verdict in ("持续失效", "最近退化", "一直抖"):
        ids = sorted(by_verdict.get(verdict) or [])
        if ids:
            lines.append(f"{verdict}: {len(ids)} 条 — "
                         + "；".join(f"{safe_id(i)} {trend_marks(trend[i]['series'])}"
                                    for i in ids))
    lines.append("汇总行带 ●○ 序列：「一直抖」是杂项桶，「坏过又好回来」（末位 ●）"
                 "与「一直没好」（末位 ○）处置相反，别只看条数。")
    lines.append("与配对差异的分工：配对差异只说最新一份比上一份好了还是坏了；"
                 "趋势序列才能区分「一直抖」「最近退化」「长期稳定」——"
                 "三者处置完全不同（抖 → 加 runs；退化 → 查回归；"
                 "稳定 → live 用例反而要查断言够不够狠）。")

    lines.append("")
    lines.append("--- 成本（评测进程口径，不是生产 usage 口径） ---")
    total_tokens = sum(m["tokens"] for m in metrics.values())
    live_tokens = sum(m["tokens"] for m in metrics.values() if m["layer"] == "live")
    lines.append(f"  本次快照 completion_tokens 合计: {total_tokens}"
                 f"（其中 live {live_tokens}；含截断救援重试）")
    lines.append("  口径说明: 数字来自 run_evals.py 落盘的 turn_meta，"
                 "与生产记账（server/llm.py 的 last_usage、dispatcher 的 usage）"
                 "是两套，暂不合并，避免出现两个互不相等的成本数。")

    lines.append("")
    lines.append(f"--- 跨快照 review 队列（近 {REVIEW_QUEUE_WINDOW} 份快照，"
                 "需人工复核原始回复） ---")
    queue = build_review_queue(snapshots)
    if not queue:
        lines.append("  无待复核用例：近 "
                     f"{REVIEW_QUEUE_WINDOW} 份快照里没有 review 轮次。")
        lines.append("  （review = 判定器说不清；live 用例才可能产生，"
                     "而 live 要手动 --live 才跑，所以这里是空的属正常态。）")
    else:
        lines.append(f"{'用例':38} {'layer':7} {'review/总':>10} "
                     f"{'首次':17} {'最近一次':17}")
        for cid in sorted(queue):
            entry = queue[cid]
            lines.append(f"{safe_id(cid[:38]):38} {entry['layer']:7} "
                         f"{entry['review_runs']}/{entry['total_runs']:<8} "
                         f"{fmt_at(entry['first_review_at']):17} "
                         f"{fmt_at(entry['last_review_at']):17}")
            # 定位路径单独一行：多份快照 × 多轮号会很长，挤在日期行里会把
            # 日期列顶出可读范围
            where = "；".join(f"{loc['file']} 轮{','.join(map(str, loc['runs']))}"
                             for loc in entry["locations"][-3:])
            more = (f" …等 {len(entry['locations'])} 处"
                    if len(entry["locations"]) > 3 else "")
            lines.append(f"      定位: {where}{more}")
        lines.append("  复核方法：打开上述快照，找 runs[].verdict == \"review\" 的轮次，"
                     "读 turns[].reply——要人工判断的是**回复本身**，不是判定结果。")
        lines.append("  「首次」与「最近一次」的间隔就是欠账时长：今天新出现的优先看，"
                     "欠了很久的要么尽快处理、要么确认其实已有人跟进。")
    return "\n".join(lines)


def build_json(snapshots: list[dict], broken: list[str] | None = None) -> dict:
    latest = snapshots[-1]
    cases = []
    for cid, case in latest["cases"].items():
        m = safe_metrics(case)
        if m is not None:
            cases.append({"id": cid, **m})
    # 成本汇总（R30）：tokens 早在 cases 单条里，但没有汇总值，消费方须自己 sum。
    # 与屏幕的「本次快照 completion_tokens 合计」同口径——含截断救援重试，
    # 且是评测进程口径、不是生产 usage 口径。
    total_tokens = sum(c.get("tokens") or 0 for c in cases)
    live_tokens = sum(c.get("tokens") or 0 for c in cases
                      if c.get("layer") == "live")
    return {
        "snapshot_count": len(snapshots),
        # R30：本次扫描跳过的损坏快照数。屏幕一直有「警告: 跳过损坏的快照」
        # 并逐条列出，JSON 里却没有——机器消费方因此无法判断这份报告的可信度
        # （跳过多份时，通过率是在被裁剪过的样本上算出来的）。
        "broken_count": len(broken or []),
        "latest": latest["file"],
        "latest_at": latest["at"],
        "previous": snapshots[-2]["file"] if len(snapshots) > 1 else None,
        # 被测对象标识（R20）。机器消费方需要它自己判断两份快照是否可比——
        # 屏幕上有"换了被测对象"的告警，JSON 里必须同样给得出这个事实。
        # 旧快照没有该字段 → null，消费方按"未知"处理。
        "latest_subject": latest.get("subject"),
        "previous_subject": (snapshots[-2].get("subject")
                             if len(snapshots) > 1 else None),
        "trend_window": TREND_WINDOW,
        "cases": cases,
        "trend": build_trend(snapshots),
        # R30：成本汇总。口径与屏幕的「本次快照 completion_tokens 合计」一致——
        # 含截断救援重试，且是**评测进程口径、不是生产 usage 口径**（与生产记账
        # 是两套，暂不合并，避免出现两个互不相等的成本数）。
        "cost": {
            "total": total_tokens,
            "live": live_tokens,
            "scope": "eval_process_not_production_usage",
            "note": "completion_tokens 合计，含截断救援重试；"
                    "与生产记账（server/llm.py 的 last_usage）是两套，暂不合并",
        },
        # R38：跨快照 review 队列。屏幕上有「跨快照 review 队列」一节，JSON 里
        # 也必须给得出——机器消费方要能知道哪些用例欠着人工复核、去哪儿找原始回复。
        "review_queue_window": REVIEW_QUEUE_WINDOW,
        "review_queue": build_review_queue(snapshots),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评测报告层（只读快照，不计入门禁）")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR),
                        help="快照目录，默认 evals/results/")
    parser.add_argument("--json", default="",
                        help="把机器可读的报告写到这个路径（可选）")
    args = parser.parse_args()

    snapshots, broken = load_snapshots(Path(args.results_dir))
    print(render(snapshots, broken, Path(args.results_dir)))

    if args.json:
        out = Path(args.json)
        # --json 不许指向快照目录之内：那份摘要会被下次运行的 glob 当成
        # 「结构不符合」的损坏快照，自己给自己造警告、虚增快照数量。
        # 判定按**路径分量**比，不能用字符串前缀——str.startswith 会把
        # `<results>-sibling/h.json` 这种合法目标误判成目录内（R3 在
        # evals_health.py 上修过同一个误杀，这里补同款守卫）。
        results_root = Path(args.results_dir).resolve()
        if out.resolve().is_relative_to(results_root):
            print("错误: --json 不能指向快照目录之内——那会污染被扫描的目录，"
                  "下次运行会把这份摘要当成损坏快照。")
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        # broken 必须传进去：否则 broken_count 恒为 0，成了永远报「无损坏」的
        # 假字段（屏幕上有逐条警告、JSON 说没有，正是最糟的那种不一致）。
        # 空快照分支也带这两个键，让消费方不必为「有没有快照」写两套解析。
        payload = (build_json(snapshots, broken) if snapshots else
                   {"snapshot_count": 0, "cases": [], "trend": {},
                    "broken_count": len(broken),
                    "cost": {"total": 0, "live": 0,
                             "scope": "eval_process_not_production_usage",
                             "note": "无快照，无成本"}})
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\n机器可读报告已写入: {out}")

    # 报告层永不因内容失败：没有快照、只有一份快照、快照损坏都是正常状态。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

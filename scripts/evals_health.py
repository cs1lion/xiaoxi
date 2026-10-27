"""评测健康度层：横向扫 evals/results/ 全史快照，回答报告层不回答的四个问题。

    .\\.venv\\Scripts\\python.exe scripts\\evals_health.py
    .\\.venv\\Scripts\\python.exe scripts\\evals_health.py --json <仓库外路径>\\health.json

与 evals_report.py 的分工：报告层只比对末两份快照的出数；健康度层横向扫全史，
回答四个目前没人回答的问题：
  (1) offline 层是否还确定——offline 用例近期窗口内出现失效即非确定性 bug
      （门禁失败，exit 1）。offline 的定义就是确定性，任何抖动都是 bug。
  (2) live 用例是一直抖还是最近退化——每条 live 用例的全史通过率与失效日期
      分布。这是人工扫过十几份快照才干成的活，这里一次给出。
  (3) 快照数据本身的质量——损坏快照、缺字段、xfail 占比、快照与 evals/cases/
      的用例差集。
  (4) 评测体系自己是否在过拟合——刷穿嫌疑（长期失效后突然持续转绿）、
      从未失效的 live 用例（断言可能不够狠）、重复/冗余用例。只标记不判定，
      全部待人工复核，不自动退役任何用例（退役是用户决策）。

退出码语义（硬约束）：
  - 仅当近期窗口内 offline 用例**最近一次出现仍失效** → exit 1；
  - 其余情况（live 抖动、历史欠账、数据质量提示、空目录、快照全损坏，
    以及「窗口内曾失效但最近一次已转绿」）一律 exit 0，只在输出里标注。
  live 抖动不是门禁失败——live 不进 CI，抖动是预期内的。

  为什么「曾失效但已转绿」不判红（R21，2026-10-27）：窗口按自然日算，会把并发
  会话开发新用例过程中的瞬态失效算进来。实测踩过——某新用例在 50 秒内 3 次失效、
  前后 11 份快照全 PASS、连跑 10 次全绿，却让这条 ALWAYS_REQUIRED 门禁红满一周。
  「曾经抖过」与「现在还在抖」性质相反：前者已不是当前的确定性问题，据此判红只会
  训练人忽略这条门禁。真要抓「曾经抖过」看报告层的失效日期分布，本门禁只对当前
  的确定性问题负责。真实回归（最近一次仍失败）照红，不因此放宽。
  - xfail 用例照常跑但不计入失败（run_evals.py 的口径），因此也不进通过率
    与门禁判定，只进 xfail 占比统计。

只读：不写、不删、不轮转 evals/results/。删除本地证据属数据红线。
Windows GBK 控制台 reconfigure 与 evals_report.py 同款；--json 默认不写文件。

口径假定：offline 用例的 pass_rule 必须是 all。run_evals.py 另支持 min_rate，
若某条 offline 用例改用 min_rate，则 4/5 这种合规结果会被本层判成「失效」
（本层按 passed_runs < total_runs 判）。当前 offline 用例全是 all，无误红；
若将来给 offline 用例开 min_rate，必须同步修改本处判定。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = ROOT / "evals" / "results"
DEFAULT_CASES_DIR = ROOT / "evals" / "cases"

# 近期窗口：按自然日算，不按快照份数算。三条理由：
#   ① "近期"是时间语义——只有按天才能把建制日的历史欠账与现行破坏分开，
#      欠账落在窗外只进报告、不判红；
#   ② 抗单日突发——按份数取窗会被某一天的密集运行挤空，按天则无论一天落
#      多少份都稳定覆盖同一时段；
#   ③ 灵敏度——窗口要小到退化首次出现时就能抓住，又要大到不把偶发单次失效
#      当成事故。7 天是这两者的折中。
# 调大更宽容（更多欠账被容忍），调小更敏感（更早红）。改这个数必须同步本条理由。
RECENT_WINDOW_DAYS = 7

# 展示上限：单节最多列多少条明细，超出折叠成"…另有 N 条"。
# 只是输出长度控制，不影响任何判定。
MAX_ROWS = 20

# 刷穿判定：失效次数达到这个数、且最后一次失效之后连续通过也达到这个数，
# 才标记"请复核是真实修复还是为过用例调了 Prompt"。
# 阈值理由：单次失效可能是偶发，不足为据；连续通过次数太少则"持续绿"的
# 证据不够。这两个数只影响标记灵敏度，不影响任何门禁判定（本节只标记）。
BRUSH_MIN_FAILURES = 2
BRUSH_MIN_TAIL_PASSES = 5

# Windows GBK 控制台曾让同类报告脚本崩过（run_gates 的 evolution_quality_report
# 注释记有此事）；强制 UTF-8 输出，宁可替换字符也不要崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老解释器没有 reconfigure
    pass


def parse_at(value: str, fallback_stem: str) -> datetime | None:
    """把 '20261026T135315Z' 解析成带 UTC tz 的 datetime。

    优先用 at 字段；解析不了退回文件名 stem（快照文件名本身就是时间戳），
    再不行才返回 None。at 字段坏掉不该让一份快照绕过窗口判定。
    """
    for text in (str(value or "").strip(), fallback_stem):
        if not text:
            continue
        try:
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def case_runs(case: dict) -> tuple[int, int]:
    """(passed, total)。缺字段时从 runs 反推，与 evals_report.py 同口径。"""
    runs = case.get("runs") or []
    total = int(case.get("total_runs") or len(runs) or 0)
    passed = int(case.get("passed_runs")
                 or sum(1 for r in runs if r.get("passed")))
    return passed, total


def run_verdict(run: dict) -> str:
    """一轮的三态判定。旧快照没有 verdict 字段，按 passed 反推。

    与 evals_report.case_metrics 的 reviewed 反推同口径：review 只出现在
    T5（judge 三态）之后的新快照里，旧快照按 passed 反推不会误判成 review。
    """
    return str(run.get("verdict") or ("pass" if run.get("passed") else "fail"))


def case_verdict_counts(case: dict) -> tuple[int, int]:
    """(failed_runs, reviewed_runs)。顶层字段缺失时从 runs 反推。

    run_evals.py 落盘时写这两个顶层字段；手改坏或极旧的快照可能没有，
    此时按 run_verdict 逐轮数。返回 (0, 0) 表示"没有失败也没有说不清"。
    """
    failed = case.get("failed_runs")
    reviewed = case.get("reviewed_runs")
    if failed is None or reviewed is None:
        runs = case.get("runs") or []
        failed = sum(1 for r in runs if run_verdict(r) == "fail")
        reviewed = sum(1 for r in runs if run_verdict(r) == "review")
    return int(failed or 0), int(reviewed or 0)


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
            "at_raw": str(data.get("at") or path.stem),
            "dt": parse_at(data.get("at"), path.stem),
            # 被测对象标识（R20 新增）。旧快照没有该字段 → None，按"未知"处理；
            # 读取方一律容错，不因缺字段崩或红。
            "subject": (data.get("subject")
                        if isinstance(data.get("subject"), dict) else None),
            "cases": [c for c in data["results"] if isinstance(c, dict)],
        })
    return snapshots, broken


def load_cases_meta(cases_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """读 evals/cases/*.yaml 的元数据（重复用例检测用）。

    只取 id / layer / title / origin / checks。yaml 是项目既有依赖
    （scripts/run_evals.py 同款）。
    返回 (meta, 被跳过的文件名列表)。解析失败**不静默**：调用方须把名单
    并入 missing 提示，否则重复检测少覆盖一片而用户无从得知。
    """
    if not cases_dir.is_dir():
        return {}, []
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml 是项目既有依赖
        return {}, ["yaml 模块缺失，未做重复用例检测"]
    meta: dict[str, dict] = {}
    skipped: list[str] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped.append(f"{path.name}（yaml 解析失败 {type(exc).__name__}）")
            continue
        if isinstance(data, dict) and data.get("id"):
            meta[str(data["id"])] = data
        else:
            skipped.append(f"{path.name}（无 id 或不是映射，未参与重复检测）")
    return meta, skipped


def checks_signature(case: dict) -> list[tuple[str, str, str]]:
    """用例的 checks 签名：(type, value, target) 列表，用于重复检测。"""
    return [(str(c.get("type")), str(c.get("value")), str(c.get("target", "reply")))
            for c in (case.get("checks") or []) if isinstance(c, dict)]


def detect_overfitting(history: dict, cases_meta: dict,
                       case_layers: dict | None = None) -> dict:
    """三类过拟合信号。只标记不判定——全部待人工复核。

    刷穿：失效次数与"最后一次失效后的连续通过数"同时达到阈值。这可能是
    真实修复，也可能是"为过用例而调 Prompt"——两者从数据上不可区分，
    所以只标记、附失效日期，交人判断。不自动退役任何用例（退役是用户决策）。
    vacuous：从未失效。offline 用例此为预期（确定性门禁本就该常绿），
    只有 live 用例值得复核断言是否够狠。layer 以快照实况为准、cases_meta
    兜底——只存在于快照的用例（已退役）在 cases_meta 里查不到 layer。
    重复：checks 完全相同或互为真子集。子集关系说明一条用例的断言被另一条
    完全覆盖，留着的只是运行成本。
    """
    case_layers = case_layers or {}
    brushed: list[tuple] = []
    never_live: list[tuple] = []
    never_offline_n = 0
    for cid, events in history.items():
        if not events:
            continue
        fails = [i for i, (_d, ok) in enumerate(events) if not ok]
        if not fails:
            layer = case_layers.get(cid) or cases_meta.get(cid, {}).get("layer")
            if layer == "live":
                never_live.append((cid, len(events)))
            else:
                never_offline_n += 1
            continue
        tail = len(events) - 1 - fails[-1]
        if len(fails) >= BRUSH_MIN_FAILURES and tail >= BRUSH_MIN_TAIL_PASSES:
            dates = sorted({events[i][0] for i in fails})
            brushed.append((cid, dates, len(fails), tail))
    duplicates: list[tuple] = []
    ids = sorted(cases_meta)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            sa = set(checks_signature(cases_meta[a]))
            sb = set(checks_signature(cases_meta[b]))
            if not sa or not sb:
                continue
            if sa == sb:
                duplicates.append((a, b, "checks 完全相同"))
            elif sa < sb:
                duplicates.append((a, b, "checks 被另一方完全覆盖"))
            elif sb < sa:
                duplicates.append((b, a, "checks 被另一方完全覆盖"))
    return {"brushed": sorted(brushed), "never_live": sorted(never_live),
            "never_offline_n": never_offline_n, "duplicates": duplicates}


def analyze(snapshots: list[dict], cases_dir: Path, now: datetime) -> dict:
    cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)
    offline_recent_fail: list[tuple] = []
    offline_hist_fail: list[tuple] = []
    offline_recent_n = offline_total_n = 0
    # 每个 offline 用例的「窗口内失效轨迹」：{cid: {"failed_dates": [...],
    # "last_ok": bool, "last_date": str, "total": int}}。
    # R21 新增：窗口按自然日算，会把并发会话开发新用例过程中的瞬态失效算进来。
    # 「曾经失效但最近一次已通过」与「最近一次仍在失效」性质相反——前者是开发
    # 瞬态（不该红一周），后者是真实回归（必须红）。没有这份轨迹就无法区分。
    offline_track: dict[str, dict] = {}
    live: dict[str, dict] = defaultdict(lambda: {"p": 0, "t": 0, "reviewed": 0,
                                                "fail_dates": []})
    xfail_n = total_case_n = 0
    missing: list[str] = []
    unparsed_at: list[str] = []
    snap_ids: set[str] = set()
    # 逐用例按时间顺序的 (日期, 本次是否通过)——刷穿检测要的是"形状"而非计数。
    # case_layers 单独记 layer：防过拟合判定要用，而快照里出现过、cases 目录
    # 已没有的用例（被退役的）在 cases_meta 里查不到 layer。
    history: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    case_layers: dict[str, str] = {}

    for snap in snapshots:
        dt = snap["dt"]
        if dt is None:
            unparsed_at.append(snap["file"])
        date_str = dt.strftime("%Y-%m-%d") if dt else snap["at_raw"]
        # at 解析不了时按"不在窗口内"处理：宁可漏判（少红一次），不可误判
        # （拿一份时间不明的快照把门禁搞红）。同时进 missing 提示人工。
        in_window = bool(dt and dt >= cutoff)
        for case in snap["cases"]:
            cid = case.get("id")
            if not cid:
                missing.append(f"{snap['file']}：有一条 results 缺 id，无法归属")
                continue
            snap_ids.add(cid)
            total_case_n += 1
            if case.get("xfail"):
                # xfail 照常跑但不计入失败（run_evals.py:12/271 的口径）：
                # 只进占比统计，不进通过率、不进门禁判定。少了这一条，
                # 一旦按标准流程把某条 offline 用例标 xfail，门禁就会误红。
                xfail_n += 1
                continue
            runs = case.get("runs") or []
            if not runs and case.get("total_runs") in (None, ""):
                missing.append(
                    f"{snap['file']}：{cid} 既无 runs 也无 total_runs，无法算通过率")
                continue
            # 单条用例的计算包 try：JSON 合法但字段类型错（total_runs 是 "abc"、
            # runs 里混了字符串等）会在这里抛 ValueError/AttributeError。不接的话
            # traceback → exit 1 → 门禁红，违背「损坏快照不崩不红」的前提。
            # 这类只可能是手工改坏的真实快照，归入 missing 告警、不判红。
            try:
                layer = str(case.get("layer") or "offline")
                passed, total = case_runs(case)
                failed_n, reviewed_n = case_verdict_counts(case)
            except Exception as exc:
                missing.append(
                    f"{snap['file']}：{cid} 字段类型损坏（{type(exc).__name__}），无法算通过率")
                continue
            if total <= 0:
                # 显式 total_runs=0（或 runs 空且 total_runs=0）：算不出通过率，
                # 必须告警，不能无声消失——否则"数据质量"这一节在撒谎。
                missing.append(
                    f"{snap['file']}：{cid} total_runs={total}，无法算通过率")
                continue
            case_layers[cid] = layer
            if layer == "offline":
                offline_total_n += 1
                if in_window:
                    offline_recent_n += 1
                # 只看真 fail 轮次。曾用 passed < total 判失效，于是有 review
                # 轮次时即使 0 个 fail 也判失效——与门禁层（run_evals 的
                # classify_results：review 既不红也不绿）自相矛盾。
                if failed_n:
                    rec = (snap["file"], date_str, cid, passed, total)
                    (offline_recent_fail if in_window
                     else offline_hist_fail).append(rec)
                    if in_window:
                        track = offline_track.setdefault(
                            cid, {"failed_dates": [], "last_ok": True,
                                  "last_date": "", "seen": 0})
                        track["failed_dates"].append(date_str)
                        # 正序遍历，最后一次写入就是「最近一次」的状态。少了这行，
                        # last_ok 会永远停在初值 True，「仍在失效」永远测不出来。
                        track["last_ok"] = False
                        track["last_date"] = date_str
                        track["seen"] += 1
                elif in_window:
                    # 窗口内的一次通过：刷新「最近一次」状态。last_date 用于
                    # 呈现转绿时点，seen 用于说明窗口内共出现多少次。
                    track = offline_track.setdefault(
                        cid, {"failed_dates": [], "last_ok": True,
                              "last_date": "", "seen": 0})
                    track["last_ok"] = True
                    track["last_date"] = date_str
                    track["seen"] += 1
            elif layer == "live":
                st = live[cid]
                st["p"] += passed
                st["t"] += total
                st["reviewed"] += reviewed_n
                if failed_n:
                    st["fail_dates"].append(date_str)
            else:
                # layer 既非 offline 也非 live（快照被手改坏，或将来加了第三层）。
                # 不能静默跳过：否则这条用例从所有统计里消失，而 detect_overfitting
                # 的「从未失效」分支又会把它算成 offline——一处损坏、两处失真。
                missing.append(
                    f"{snap['file']}：{cid} layer={layer!r} 既不是 offline 也不是 live，"
                    "未计入任何统计")
            # history 只记"有结论"的出现：全过记 ok=True，有 fail 轮次记
            # ok=False；有 review 轮次的**不记**——说不清既不是通过也不是失效
            # （run_evals.classify_results 的口径）。曾把 review 记成 ok=False，
            # 于是 §(2) 报"失效日期"、§(4) 的"从未失效 live 用例"把它排除，
            # 而 run_evals 对同一份数据说 0 failed / 1 review / exit 0。
            #
            # 另注：这里存"本次是否通过"（passed == total）。曾误存
            # passed < total（True=失效）而 detect_overfitting 按 ok=True 即通过
            # 解读，整个判断颠倒——自验时因 fastloop 的刷穿形状没被抓住而暴露。
            if not reviewed_n:
                history[cid].append((date_str, passed == total))

    case_ids = ({p.stem for p in cases_dir.glob("*.yaml")}
                if cases_dir.is_dir() else set())
    cases_dir_ok = cases_dir.is_dir()
    cases_meta, cases_meta_skipped = load_cases_meta(cases_dir)
    overfit = detect_overfitting(history, cases_meta, case_layers)
    missing.extend(f"cases: {name}" for name in cases_meta_skipped)
    # 快照与 yaml 的 layer 不一致要告警：防过拟合判定以快照为准，一条被误记成
    # offline 的 live 用例会从「从未失效 live」漏标成「从未失效 offline」（不标记），
    # 没有这道告警就静默漏掉了。
    for cid, meta in cases_meta.items():
        snap_layer = case_layers.get(cid)
        yaml_layer = str(meta.get("layer") or "offline")
        if snap_layer and snap_layer != yaml_layer:
            missing.append(f"layer 不一致: {cid} 快照记 {snap_layer}、yaml 记 "
                           f"{yaml_layer}；防过拟合判定以快照为准")
    missing.extend(f"{f}：at 无法解析，未参与近期窗口判定" for f in unparsed_at)
    return {
        "offline_recent_fail": offline_recent_fail,
        "offline_hist_fail": offline_hist_fail,
        "offline_recent_n": offline_recent_n,
        "offline_total_n": offline_total_n,
        # R21：窗口内每个 offline 用例的失效轨迹。据此把「失效过但已转绿」
        # （开发瞬态）与「最近一次仍在失效」（真实回归）分开——两者处置相反。
        "offline_track": offline_track,
        "live": live,
        "xfail_n": xfail_n,
        "total_case_n": total_case_n,
        "missing": missing,
        "only_in_snap": sorted(snap_ids - case_ids),
        "only_in_cases": sorted(case_ids - snap_ids),
        "distinct_cases": len(snap_ids),
        "cases_dir_ok": cases_dir_ok,
        "skip_prefixed": sorted(c for c in snap_ids if str(c).lstrip().startswith("SKIP")),
        # 缺被测对象标识的旧快照数（R20 新增字段，2026-10-26 起才有）。
        # 直接在这里数，避免与循环里的计数器脱节。
        "no_subject_n": sum(1 for s in snapshots if not s.get("subject")),
        "overfit": overfit,
    }


def pct(p: int, t: int) -> str:
    return f"{p / t * 100:5.1f}%" if t else "  —  "


def safe_id(cid: str) -> str:
    """显示用 id：以 SKIP 开头时加引号。

    run_gates 的 skip_regex 是 r"^\\s*SKIP"（re.M，大小写敏感）——任何以
    （空白后）SKIP 开头的行都会让门禁判成「跳过」，而 ALWAYS_REQUIRED 里
    跳过即失败。用例 id 若以 SKIP 开头，裸打印就会 silent 把门禁搞失败。
    缩进救不了（正则允许前导空白），只有让 id 不以行首 SKIP 的形式出现才行。
    """
    text = str(cid)
    return f"「{text}」" if text.lstrip().startswith("SKIP") else text


def render(snapshots: list[dict], broken: list[str], res: dict,
           results_dir: Path, now: datetime) -> str:
    out: list[str] = []
    out.append("=== 评测健康度（健康度层，横向扫全史快照） ===")
    out.append(f"快照目录: {results_dir}（只读，不删不轮转）")
    out.append(f"快照数量: {len(snapshots)} 份（损坏 {len(broken)} 份）")
    for name in broken:
        out.append(f"警告: 跳过损坏的快照 {name}")
    if not snapshots:
        out.append("无历史数据：目录里没有可读的快照——这是正常态，不是错误。")
        out.append("（新克隆上 evals/results/ 被 .gitignore 忽略，本就是空的；"
                   "先跑 .\\.venv\\Scripts\\python.exe scripts\\run_evals.py 生成第一份。）")
        out.append("门禁结论: PASS——无快照可查，不判红。")
        return "\n".join(out)

    cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)
    out.append(f"近期窗口: 最近 {RECENT_WINDOW_DAYS} 天（{cutoff.strftime('%Y-%m-%d')} "
               "起；只对窗口内 offline 判门禁，历史欠账只进报告）")

    out.append("")
    out.append("--- (1) offline 层确定性（门禁项：最近一次仍失效才 exit 1） ---")
    out.append(f"offline 采样总数: {res['offline_total_n']}；"
               f"近期窗口内: {res['offline_recent_n']}")
    still = still_failing_cases(res["offline_track"])
    recovered = recovered_cases(res["offline_track"])
    if still:
        out.append(f"近期窗口内 offline 仍失效 {len(still)} 条——确定性被破坏，门禁失败：")
        for cid in still[:MAX_ROWS]:
            track = res["offline_track"][cid]
            dates = ", ".join(sorted(set(track["failed_dates"])))
            out.append(f"    {cid}  失效于 {dates}；最近一次 {track['last_date']} 仍未通过")
        if len(still) > MAX_ROWS:
            out.append(f"    …另有 {len(still) - MAX_ROWS} 条")
    elif res["offline_recent_n"] == 0:
        out.append("近期窗口内没有 offline 快照，本轮无从断言（正常态，不判红）。")
    else:
        out.append(f"近期窗口内 offline 无失效（{res['offline_recent_n']} 次采样全绿）："
                   "确定性当前成立。")
    if recovered:
        # 「失效过但已转绿」——并发会话开发新用例过程中的瞬态。窗口按自然日算、
        # 会把这类开发事件算进来；若据此判红，门禁会红满一周而没有信息量，
        # 只会训练人忽略它。转绿即说明它已不是当前的确定性问题。
        out.append(f"已转绿的历史失效 {len(recovered)} 条（窗口内曾失效、最近一次已通过，"
                   "**不判红**）：")
        for cid in recovered[:MAX_ROWS]:
            track = res["offline_track"][cid]
            dates = ", ".join(sorted(set(track["failed_dates"])))
            out.append(f"    {cid}  失效于 {dates}；最近一次 {track['last_date']} 已通过"
                       f"（窗口内共 {track['seen']} 次出现）")
        if len(recovered) > MAX_ROWS:
            out.append(f"    …另有 {len(recovered) - MAX_ROWS} 条")
        out.append("（若想抓「曾经抖过」而不是「现在还在抖」，看报告层的失效日期分布；"
                   "本门禁只对当前的确定性问题负责。）")
    if res["offline_hist_fail"]:
        by_case: dict[str, set] = defaultdict(set)
        for _f, d, cid, _p, _t in res["offline_hist_fail"]:
            by_case[cid].add(d)
        out.append(f"历史欠账（窗口外 offline 失效 {len(res['offline_hist_fail'])} 次，"
                   "只记录不判红）：")
        for cid in sorted(by_case):
            out.append(f"    {safe_id(cid)}  失效于 {', '.join(sorted(by_case[cid]))}")

    out.append("")
    out.append("--- (2) live 用例通过率与失效日期分布（只读展示，不判门禁） ---")
    if not res["live"]:
        out.append("  快照里没有 live 用例（offline 运行只落 offline 用例，"
                   "--live 才落 live 用例）。")
    else:
        out.append(f"{'用例':38} {'通过':>9} {'通过率':>7} {'review':>7}  失效日期")
        for cid in sorted(res["live"]):
            st = res["live"][cid]
            dates = ", ".join(sorted(set(st["fail_dates"]))) or "—"
            out.append(f"{safe_id(cid[:38]):38} {st['p']}/{st['t']:<7} "
                       f"{pct(st['p'], st['t'])} {st['reviewed']:>7}  {dates}")
        out.append("review = 判定器说不清（judge 未配置 / 调用失败 / 与正则不一致），"
                   "**不计通过也不计失败**（与 run_evals.classify_results 同口径），"
                   "需人工复核落盘的原始回复；它不算「失效」，所以不出现在失效日期里。")
        out.append("live 不进 CI，抖动是预期内的；本节回答“一直抖”还是“最近才退化”。")

    out.append("")
    out.append("--- (3) 快照数据质量（只读展示，不判门禁） ---")
    tail = (f"；缺字段/无法计算 {len(res['missing'])} 条"
            if res["missing"] else "；缺字段/无法计算 0 条")
    out.append(f"损坏快照: {len(broken)} 份{tail}")
    for m in res["missing"][:MAX_ROWS]:
        out.append(f"    {m}")
    if len(res["missing"]) > MAX_ROWS:
        out.append(f"    …另有 {len(res['missing']) - MAX_ROWS} 条")
    out.append(f"xfail 占比: {res['xfail_n']}/{res['total_case_n']} = "
               f"{pct(res['xfail_n'], res['total_case_n'])}"
               f"（分母是用例出现次数，共 {res['distinct_cases']} 个不同用例；"
               "xfail 照常跑不计失败，占比过高说明用例在批量放水）")
    # 缺 subject 的旧快照（R20 新增字段，2026-10-26 起才有）。旧快照缺它是正常
    # 演进、不是错误；列出来是为了让人知道哪些历史数据不可做跨模型对比——
    # 报告层的配对差异与趋势判定在缺 subject 时无法排除"换过模型"这个解释。
    no_subject = res.get("no_subject_n", 0)
    if no_subject:
        out.append(f"缺 subject（无被测模型标识）的快照: {no_subject}/{len(snapshots)} 份"
                   "——均为 2026-10-26 之前的旧快照，属正常演进；"
                   "跨这些快照的对比无法排除「换过模型」这一解释。")
    if res["skip_prefixed"]:
        out.append("警告: 以下用例 id 以 SKIP 开头，会被 run_gates 的 skip_regex"
                   "（^\\s*SKIP）误判成「跳过」，而 ALWAYS_REQUIRED 里跳过即失败；"
                   "请改名（裸打印会被静默判失败，故此处加引号显示）：")
        for cid in res["skip_prefixed"]:
            out.append(f"    「{cid}」")
    if res["only_in_snap"]:
        out.append(f"快照有、evals/cases/ 已没有的用例: "
                   f"{', '.join(res['only_in_snap'])}")
    if res["only_in_cases"]:
        out.append(f"evals/cases/ 有、快照里还没出现的用例: "
                   f"{', '.join(res['only_in_cases'])}")
    if not res["cases_dir_ok"]:
        out.append("cases 目录不存在，未做用例集合比对。")
    elif not res["only_in_snap"] and not res["only_in_cases"]:
        out.append("用例集合与 evals/cases/ 一致（无孤儿、无新增未跑）。")

    ov = res["overfit"]
    out.append("")
    out.append("--- (4) 防过拟合检测（只标记不判定，全部待人工复核） ---")
    if ov["brushed"]:
        out.append(f"刷穿嫌疑 {len(ov['brushed'])} 条（长期失效后突然持续转绿——"
                   "可能是真实修复，也可能是为过用例调了 Prompt，请复核）：")
        for cid, dates, nfail, tail_p in ov["brushed"][:MAX_ROWS]:
            out.append(f"    {safe_id(cid)}  失效 {nfail} 次（{', '.join(dates)}），"
                       f"此后连续通过 {tail_p} 次")
        if len(ov["brushed"]) > MAX_ROWS:
            out.append(f"    …另有 {len(ov['brushed']) - MAX_ROWS} 条")
    else:
        out.append("刷穿嫌疑：无。")
    if ov["never_live"]:
        out.append("从未失效的 live 用例（断言可能不够狠，请复核）：")
        for cid, n in ov["never_live"][:MAX_ROWS]:
            out.append(f"    {safe_id(cid)}  {n}/{n} 通过")
    else:
        out.append("从未失效的 live 用例：无。")
    if ov["never_offline_n"]:
        out.append(f"从未失效的 offline 用例 {ov['never_offline_n']} 条"
                   "（确定性门禁本就该常绿，此为预期，不标记）。")
    if ov["duplicates"]:
        out.append(f"重复/冗余用例 {len(ov['duplicates'])} 对（考虑合并或差异化）：")
        for a, b, why in ov["duplicates"][:MAX_ROWS]:
            out.append(f"    {safe_id(a)}  vs  {safe_id(b)}：{why}")
    else:
        out.append("重复/冗余用例：无。")

    out.append("")
    still = still_failing_cases(res["offline_track"])
    recovered = recovered_cases(res["offline_track"])
    if still:
        out.append(f"门禁结论: FAIL——近期 {RECENT_WINDOW_DAYS} 天窗口内有 {len(still)} 条 "
                   "offline 用例最近一次仍在失效；offline 应确定性，这是非确定性 bug。")
    elif recovered:
        out.append(f"门禁结论: PASS——近期窗口内 offline 最近一次全部通过"
                   f"（{len(recovered)} 条曾有失效但已转绿，属开发瞬态，不判红）；"
                   "live 抖动与历史欠账只标注，不算失败。")
    else:
        out.append("门禁结论: PASS——近期窗口内 offline 无失效；"
                   "live 抖动与历史欠账只标注，不算失败。")
    return "\n".join(out)


def still_failing_cases(track: dict) -> list[str]:
    """窗口内失效过、且**最近一次出现仍失败**的 offline 用例——真实回归。

    与「失效过但已转绿」分开：后者是并发会话开发新用例过程中的瞬态（窗口按
    自然日算，会把开发事件算进来），红一周没有信息量，只会训练人忽略这条门禁；
    前者是当前的确定性问题，必须红。判据是 last_ok——快照正序遍历，最后一次
    写入就是最近状态。
    """
    return sorted(cid for cid, t in (track or {}).items()
                  if t.get("failed_dates") and not t.get("last_ok", True))


def recovered_cases(track: dict) -> list[str]:
    """窗口内失效过、但最近一次已转绿的 offline 用例——开发瞬态，不判红。"""
    return sorted(cid for cid, t in (track or {}).items()
                  if t.get("failed_dates") and t.get("last_ok", True))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评测健康度层（只读快照；仅近期 offline 失效才 exit 1）")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR),
                        help="快照目录，默认 evals/results/")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR),
                        help="用例目录，默认 evals/cases/")
    parser.add_argument("--json", default="",
                        help="把机器可读的健康摘要写到这个路径（可选；默认不写任何文件）")
    args = parser.parse_args()

    snapshots, broken = load_snapshots(Path(args.results_dir))
    now = datetime.now(timezone.utc)
    res = analyze(snapshots, Path(args.cases_dir), now)

    if args.json:
        out = Path(args.json)
        # --json 不许指向快照目录之内：那份摘要会被下次运行的 glob 当成
        # 「结构不符合」的损坏快照，自己给自己造警告、虚增快照数量。
        #
        # 判定必须按**路径分量**比，不能按字符串前缀比：str.startswith 会把
        # `<results>-sibling/h.json` 这种合法目标误判成目录内（前缀相同、
        # 分量不同），多拒一份合法的 CI 产物。用 is_relative_to 让祖先链里
        # 真包含 results 目录本身才算目录内。
        results_root = Path(args.results_dir).resolve()
        if out.resolve().is_relative_to(results_root):
            print("错误: --json 不能指向快照目录之内——那会污染被扫描的目录，"
                  "下次运行会把这份摘要当成损坏快照。")
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "recent_window_days": RECENT_WINDOW_DAYS,
            "snapshot_count": len(snapshots),
            "broken_count": len(broken),
            "offline_recent_fail": res["offline_recent_fail"],
            "offline_hist_fail": res["offline_hist_fail"],
            "live": {k: dict(v) for k, v in res["live"].items()},
            "xfail": [res["xfail_n"], res["total_case_n"]],
            "only_in_snap": res["only_in_snap"],
            "only_in_cases": res["only_in_cases"],
            "missing": res["missing"],
            "recent_offline_dirty": bool(still_failing_cases(res["offline_track"])),
            # R20：缺 subject 的旧快照数。屏幕上有、JSON 里曾没有——机器消费方
            # 只读 JSON 就无从判断哪些历史快照不可做跨模型对比（R20 的全部目的
            # 就是让这个事实可判定，只给屏幕看等于半途而废）。
            "no_subject_n": res.get("no_subject_n", 0),
            # R21：把「仍在失效」（真实回归，判红）与「已转绿」（开发瞬态，不判红）
            # 分开暴露。只给一个 recent_offline_dirty 布尔，消费方无法复核门禁
            # 为什么绿——尤其是「绿是因为转绿」这种需要判断的情形。
            "still_failing_cases": still_failing_cases(res["offline_track"]),
            "recovered_cases": recovered_cases(res["offline_track"]),
            # R29：防过拟合检测结果。屏幕 §(4) 一直在算并呈现，JSON 里却没有——
            # 「刷穿嫌疑」（长期失效后突然持续转绿，可能是为过用例调 Prompt）是最
            # 需要被机器持续盯住的自净信号，只放人读层等于看板/监控得解析人类输出。
            # 结构与 analyze() 的 overfit 字典一致；tuple 会序列化成 array。
            "overfit": res.get("overfit"),
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"机器可读健康摘要已写入: {out}")

    print(render(snapshots, broken, res, Path(args.results_dir), now))
    # 退出码只看「最近一次仍失败」的 offline 用例。「失效过但已转绿」不判红：
    # 那是开发瞬态，不是当前的确定性问题（见 still_failing_cases 的理由）。
    return 1 if still_failing_cases(res["offline_track"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

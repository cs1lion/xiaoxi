"""自进化初审室：L1 客观校验 + L2 审美判例，双层打回与判例自进化。

契约见 docs/PROTOCOL.md §21。两条设计底线：

1. 两层都是确定性的。L1 查真实回执（交付物 ref、自检结果、真实 span 的 token 数），
   L2 查已登记判例的触发词。都不靠模型心情，因此同一次交差永远得到同一个结论。
2. 新判例只从老板退回里长出来。`distill()` 是唯一新增入口，输入必须带
   漏审点 Delta；没有 Delta 就不落库，绝不为了让判例库"看起来在成长"而编规则。

打回上限 MAX_RETRY = 2：第 3 次仍不过转 BLOCKED 请示老板，不无限重试。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

MAX_RETRY = 2
MAX_FINDINGS = 8
MAX_RULE_CHARS = 240
MAX_EVIDENCE_CHARS = 600

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_BLOCKER = re.compile(r"严禁|绝不能|绝对不行|不许|重新做|推倒")
_L1_HINT = re.compile(r"token|预算|没接|空跑|没接线|不存在|路径|报错|失败|超了")


# ---------- 幕后训话模板 ----------
# 兜底文案；有真实 findings 时以 findings 为主，模板只定语气。
SCOLDING_TEMPLATES: dict[str, str] = {
    "l1_error": "（把卡片推回去）{detail}这是客观不过关，不是风格问题。按上面的条目逐条改，改完再交，我不替你补。",
    "l2_aesthetic": "（皱眉）{detail}这条判例库里早就写着，是我亲手记的。重新做，别让我说第三遍。",
    "spinning_warning": "（盯着副屏）{detail}它开始在原地打转了，我先把闸拉了。这笔账算它的，不算你的，但你得告诉我下一步。",
    "retry_exhausted": "（合上本子）两次都不过，我不再往它身上压你的时间。{detail}你决定：换将，还是这单就算了。",
    "accepted": "（合上本子）这版我过过手，能交。{detail}",
}


def scold(kind: str, detail: str = "") -> str:
    template = SCOLDING_TEMPLATES.get(kind) or SCOLDING_TEMPLATES["l1_error"]
    text = template.format(detail=f"{detail}\n" if detail else "")
    return text.replace("\n\n", "\n").strip()


def extract_triggers(text: str, limit: int = 8) -> list[str]:
    """Pull auditable trigger terms out of a boss rejection.

    Prefers latin words (stable identifiers), then 3-grams, then 2-grams. A trigger
    is only a hint for later L2 matching; every learned rubric keeps its evidence
    and can be deleted, so over-matching stays correctable.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    latin = [match.group(0).lower() for match in _LATIN.finditer(text)]
    three: list[str] = []
    two: list[str] = []
    for run in _CJK_RUN.findall(text):
        clipped = run[:12]
        three.append(clipped)
        for index in range(len(clipped) - 1):
            gram = clipped[index:index + 2]
            (three if index + 3 <= len(clipped) else two).append(gram)
    picked: list[str] = []
    for candidate in latin + three + two:
        if candidate not in picked:
            picked.append(candidate)
        if len(picked) >= limit:
            break
    return picked


def _finding(rule_id: str, severity: str, detail: str) -> dict[str, Any]:
    return {"rule_id": rule_id, "severity": severity, "detail": detail[:240]}


def _surface(artifact: dict[str, Any]) -> str:
    """What the worker declared it *did*.

    `intercepted` is deliberately excluded: that field is where the chief of
    staff records what she refused on the boss's behalf, so "我在顶栏加了个按钮，
    我拦下了" must not read as a violation. Judging refusals as delivery is how a
    rubric library starts rejecting correct work.
    """
    parts = [str(artifact.get("summary") or "")]
    for item in artifact.get("artifacts") or []:
        if isinstance(item, dict):
            parts.extend(str(item.get(key) or "") for key in ("kind", "ref", "preview", "notes"))
    parts.extend(str(item) for item in (artifact.get("touched_ui") or []))
    return "\n".join(parts)


def _check_required_fields(artifact, packet, usage, thresholds):
    findings = []
    summary = artifact.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        findings.append(_finding("required_fields", "blocker", "交付卡没有 summary：没说做了什么。"))
    elif len(summary) > int(thresholds.get("max_summary_chars", 400)):
        findings.append(_finding("required_fields", "major",
                                 f"summary 超过 {thresholds.get('max_summary_chars', 400)} 字，先砍再交。"))
    intercepted = artifact.get("intercepted")
    if not isinstance(intercepted, str) or not intercepted.strip():
        findings.append(_finding("required_fields", "major",
                                 "交付卡没有 intercepted：总管替你拦了什么，这一栏不许空着。"))
    if not isinstance(artifact.get("artifacts"), list) or not artifact.get("artifacts"):
        findings.append(_finding("required_fields", "blocker", "交付卡没有 artifacts：一件成果都没列。"))
    return findings


def _check_artifacts_exist(artifact, packet, usage, thresholds):
    findings = []
    for index, item in enumerate(artifact.get("artifacts") or []):
        if not isinstance(item, dict):
            findings.append(_finding("no_unwired_dry_run", "blocker", f"第 {index + 1} 项成果不是对象。"))
            continue
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            findings.append(_finding("no_unwired_dry_run", "blocker",
                                     f"第 {index + 1} 项成果没有可达 ref（文件路径/接口/便笺版本）。"))
    return findings


def _check_wired(artifact, packet, usage, thresholds):
    if artifact.get("wired") is True:
        return []
    return [_finding("no_unwired_dry_run", "blocker",
                     "未接线空跑：wired 不为 true。声称做完就得有真实接线，不能只描述做了。")]


def _check_no_topbar_button(artifact, packet, usage, thresholds):
    touched = [str(item) for item in (artifact.get("touched_ui") or [])]
    if any("topbar" in item.lower() or "顶栏" in item for item in touched):
        return [_finding("no_topbar_button", "blocker", "动了主舞台顶栏：新入口只能落在侧滑工作台抽屉里。")]
    return []


def _check_token_budget(artifact, packet, usage, thresholds):
    budget = int(((packet or {}).get("budget") or {}).get("max_prompt_tokens") or 0)
    spent = int((usage or {}).get("prompt_tokens") or 0)
    if not budget or not (usage or {}).get("calls"):
        return []  # 没有真实 span 就无从判断；不猜，也不因此判不合格
    ratio = float(thresholds.get("token_bloat_ratio", 1.0))
    if spent > budget * ratio:
        return [_finding("prompt_token_bloat", "major",
                         f"Prompt Tokens {spent} 超出预算 {budget}：先砍上下文再谈质量。")]
    return []


L1_CHECKS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "required_fields": _check_required_fields,
    "artifacts_exist": _check_artifacts_exist,
    "wired_not_dry": _check_wired,
    "no_topbar_button": _check_no_topbar_button,
    "token_budget": _check_token_budget,
}


class EvalRubricStore:
    """`eval_rubrics`：种子铁律 + 老板退回提炼的新判例。只追加与置否，不改写历史。"""

    def __init__(self, path="data/executive.db", clock=time.time):
        self.path = str(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._closed = False
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS eval_rubrics (
            id TEXT PRIMARY KEY, layer TEXT NOT NULL, severity TEXT NOT NULL,
            rule TEXT NOT NULL, triggers TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        """)

    def add(self, rubric):
        record = {
            "id": str(rubric.get("id") or "")[:64],
            "layer": rubric.get("layer") if rubric.get("layer") in ("l1", "l2") else "l2",
            "severity": rubric.get("severity") if rubric.get("severity") in ("blocker", "major", "minor") else "major",
            "rule": str(rubric.get("rule") or "")[:MAX_RULE_CHARS],
            "triggers": json.dumps([str(t) for t in (rubric.get("triggers") or [])][:12], ensure_ascii=False),
            "source": str(rubric.get("source") or "seed")[:40],
            "evidence": str(rubric.get("evidence") or "")[:MAX_EVIDENCE_CHARS],
            "created_at": float(rubric.get("created_at") or self.clock()),
            "active": 1 if rubric.get("active", True) else 0,
        }
        if not record["id"] or not record["rule"]:
            raise ValueError("rubric requires id and rule")
        with self._lock:
            if self._closed:
                raise ValueError("rubric store is closed")
            self._db.execute(
                "INSERT OR REPLACE INTO eval_rubrics (id, layer, severity, rule, triggers,"
                " source, evidence, created_at, active) VALUES (?,?,?,?,?,?,?,?,?)",
                (record["id"], record["layer"], record["severity"], record["rule"],
                 record["triggers"], record["source"], record["evidence"],
                 record["created_at"], record["active"]))
        record["triggers"] = json.loads(record["triggers"])
        record["active"] = bool(record["active"])
        return record

    def list(self, *, active_only=True):
        clause = "WHERE active=1" if active_only else ""
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM eval_rubrics {clause} ORDER BY created_at, id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["triggers"] = json.loads(item["triggers"] or "[]")
            item["active"] = bool(item["active"])
            result.append(item)
        return result

    def deactivate(self, rubric_id):
        with self._lock:
            cursor = self._db.execute("UPDATE eval_rubrics SET active=0 WHERE id=? AND active=1",
                                      (str(rubric_id)[:64],))
        return cursor.rowcount == 1

    def has_evidence(self, evidence):
        if not evidence:
            return False
        with self._lock:
            row = self._db.execute("SELECT 1 FROM eval_rubrics WHERE evidence=? LIMIT 1",
                                   (evidence[:MAX_EVIDENCE_CHARS],)).fetchone()
        return row is not None

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True


class Evaluator:
    """双层初审 + 判例自进化。不代替 Worker 重做，不无限重试。"""

    MAX_RETRY = MAX_RETRY

    def __init__(self, *, rubrics=None, roster=None, clock=time.time, path="data/executive.db"):
        self.rubrics = rubrics if rubrics is not None else EvalRubricStore(path, clock=clock)
        self.roster = roster if roster is not None else load_roster()
        self.clock = clock
        self.thresholds = dict(self.roster.get("eval_thresholds") or {})

    def seed(self):
        """Install roster seed rules once; re-running never duplicates or rewrites them."""
        created = 0
        known = {item["id"] for item in self.rubrics.list(active_only=False)}
        for rule in self.roster.get("seed_rules") or []:
            if not isinstance(rule, dict) or not rule.get("id") or rule["id"] in known:
                continue
            self.rubrics.add({
                "id": str(rule["id"]), "layer": rule.get("layer") or "l2",
                "severity": rule.get("severity") or "major", "rule": str(rule.get("rule") or ""),
                "triggers": [str(t) for t in (rule.get("triggers") or [])],
                "source": "seed", "evidence": "", "active": True,
            })
            created += 1
        return created

    def review(self, *, commission, artifact, packet=None, usage=None, spinning=None):
        packet = packet or {}
        usage = usage or {}
        findings = []
        if isinstance(spinning, dict) and spinning.get("tripped"):
            findings.append(_finding("spinning_guard", "blocker",
                                     f"鬼打墙：同一步骤签名连续重复 {spinning.get('repeats')} 次，已熔断。"))
        commands = list((packet.get("budget") or {}).get("eval_commands")
                        or packet.get("eval_commands") or ["required_fields", "artifacts_exist"])
        for name in commands:
            check = L1_CHECKS.get(name)
            if check is None:
                continue
            findings.extend(check(artifact if isinstance(artifact, dict) else {}, packet, usage, self.thresholds))
        layer = "l1" if findings else "none"
        if not findings:
            findings = self._l2(artifact)
            layer = "l2" if findings else "none"
        findings = findings[:MAX_FINDINGS]
        passed = not findings
        return {
            "passed": passed,
            "layer": layer,
            "findings": findings,
            "retry_count": int((commission or {}).get("retry_count") or 0),
            "scolding": scold("accepted", self._accept_note(artifact)) if passed else self._scold(findings, layer),
            "created_at": self.clock(),
        }

    def _l2(self, artifact):
        surface = _surface(artifact if isinstance(artifact, dict) else {}).lower()
        if not surface.strip():
            return []
        findings = []
        for rubric in self.rubrics.list(active_only=True):
            if rubric["layer"] != "l2":
                continue
            hit = next((str(t) for t in rubric["triggers"] if str(t) and str(t).lower() in surface), None)
            if hit is None:
                continue
            findings.append(_finding(rubric["id"], rubric["severity"],
                                     f"{rubric['rule']}（命中触发词「{hit}」）"))
        return findings

    @staticmethod
    def _accept_note(artifact):
        count = len(artifact.get("artifacts") or []) if isinstance(artifact, dict) else 0
        return f"{count} 项成果都在，ref 我核过了。" if count else ""

    @staticmethod
    def _scold(findings, layer):
        detail = "\n".join(f"- [{item['severity']}] {item['rule_id']}：{item['detail']}" for item in findings)
        return scold("l2_aesthetic" if layer == "l2" else "l1_error", detail)

    def distill(self, *, commission, boss_feedback, verdict=None):
        """Turn one boss rejection into one new rubric. Returns None when there is
        nothing genuinely new to learn (empty feedback, or the same Delta twice)."""
        feedback = (boss_feedback or "").strip()
        if len(feedback) < 4:
            return None
        evidence = f"老板退回：{feedback[:MAX_EVIDENCE_CHARS]}｜初审漏审：{self._delta(verdict)[:MAX_EVIDENCE_CHARS]}"
        if self.rubrics.has_evidence(evidence):
            return None
        layer = "l1" if _L1_HINT.search(feedback) else "l2"
        severity = "blocker" if _BLOCKER.search(feedback) else "major"
        digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:8]
        return self.rubrics.add({
            "id": f"boss_{digest}", "layer": layer, "severity": severity,
            "rule": f"老板挑过的刺：{feedback[:MAX_RULE_CHARS]}",
            "triggers": extract_triggers(feedback), "source": "boss_rejection",
            "evidence": evidence, "active": True,
        })

    @staticmethod
    def _delta(verdict):
        """Why the first pass let this through; empty means the audit had no say."""
        if not isinstance(verdict, dict) or verdict.get("passed"):
            return "初审放行了这版，判例库当时没有对应条目。"
        rules = ", ".join(item.get("rule_id", "") for item in verdict.get("findings") or [])
        return f"初审曾以 {rules or '未知规则'} 拦过，老板仍退回，说明拦的理由不是重点。"

    def close(self):
        self.rubrics.close()


def load_roster(path=None):
    """Read config/agents_roster.yaml; a missing or broken file yields an empty
    roster so the executive path degrades to "no workers" instead of breaking the app."""
    import yaml

    # 故意不 import server.config：那会在 import 期读 .env / 建目录，把 assistant
    # service 的导入链拖出一堆副作用（隔离测试里直接炸）。花名册就在仓库根的
    # config/ 下，按文件位置解析就够了。
    target = Path(path) if path is not None else (
        Path(__file__).resolve().parents[2] / "config" / "agents_roster.yaml")
    empty = {"workers": [], "seed_rules": [], "dispatch": {}, "selection": {},
             "trivia_policy": [], "eval_thresholds": {}}
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return empty
    if not isinstance(data, dict):
        return empty
    for key, default in empty.items():
        data.setdefault(key, default)
    # v1 花名册用 workers:，v2 起改叫 agents: 以便和 adapter 绑定对齐。
    # 两种键都认，旧配置不必改写； agents 优先。
    if not data.get("agents") and data.get("workers"):
        data["agents"] = data["workers"]
    data.setdefault("agents", [])
    data.setdefault("adapter_types", [])
    return data


__all__ = ["L1_CHECKS", "MAX_RETRY", "SCOLDING_TEMPLATES", "EvalRubricStore", "Evaluator",
           "extract_triggers", "load_roster", "scold"]

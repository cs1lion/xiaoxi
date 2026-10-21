"""契约 v2 · §3.1 数据结构。

规范来源：`小汐 × 像素工坊：契约、状态机与事件协议 v2 规范.md` §3.1。
只实现 §0–§3.7 范围内的东西；§4 以后（状态机、Verifier、事件、权限、评测集）
一律不在本包内，见 docs/CONTRACT_V2_SCOPE.md。

设计取向
--------
1. **纯数据 + 纯校验**。这里不做 IO、不读库、不发网络、不跑命令。
   §3.7 明确要求路径与命令「必须能在契约范围内解析」，但解析不等于执行；
   执行属于 §4.9 交付与 Verifier，已冻结。
2. **不用 pydantic**。规范里的结构有几处需要「保留原始 dict 再逐字段报错」
   （lint 要一次报全部问题，而不是遇到第一个就抛异常）。用 dataclass +
   显式 `from_mapping` 更好控制错误累积，也和本仓库现有风格一致
   （`server/` 里 12 处 dataclass，pydantic 只出现在一个外部 API 适配文件）。
3. **字段名与规范逐字一致**，包括下划线风格（`contract_id` / `side_effects`），
   方便与规范对照审查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------- §3.1 枚举值 ----------

RISK_LEVELS = ("low", "medium", "high")
RISK_RANK = {name: index for index, name in enumerate(RISK_LEVELS)}

SLOT_SOURCE_KINDS = ("user_utterance", "memory", "default", "user_confirmed")

INPUT_TYPES = ("file", "dir", "url", "text", "memory_ref")

ARTIFACT_KINDS = (
    "file", "report_md", "csv", "code_patch", "message", "chart", "other",
)

SIDE_EFFECT_TYPES = (
    "send_message", "http_write", "overwrite_file", "write_outside_workspace",
    "delete", "spend_money", "install", "other",
)

SIDE_EFFECT_TARGET_CLASSES = ("self", "third_party", "local")

CRITERION_KINDS = ("deterministic", "semantic", "human")

CHECK_TYPES = (
    "file_exists_nonempty", "schema_valid", "syntax_ok", "exit_code_zero",
    "csv_assert", "rubric", "ask_user",
)

# §3.7：semantic 必须 rubric；kind 与 check.type 的匹配关系是 lint L1 的依据。
DETERMINISTIC_CHECKS = (
    "file_exists_nonempty", "schema_valid", "syntax_ok", "exit_code_zero", "csv_assert",
)
SEMANTIC_CHECKS = ("rubric",)
HUMAN_CHECKS = ("ask_user",)

# §3.1 promotion_weight 默认：deterministic 1.0、semantic 0.4、human 1.0
DEFAULT_PROMOTION_WEIGHT = {"deterministic": 1.0, "semantic": 0.4, "human": 1.0}


class ContractShapeError(ValueError):
    """结构不合法（字段缺失 / 类型不对 / 枚举越界）。

    与「校验不通过」区分开：shape 错误是说这个 dict 根本不是契约，
    而 lint / gate 不通过是说契约读懂了但内容不合格。
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "invalid contract shape")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _is_number(value: Any) -> bool:
    # bool 是 int 的子类，必须排除，否则 True 会被当成 1。
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ---------- §3.1 Slot / SlotSource ----------


@dataclass(frozen=True)
class SlotSource:
    """槽位来源。kind 决定还带哪些字段（见 §3.1 的 SlotSource 联合类型）。"""

    kind: str
    turn_id: str = ""
    memory_id: str = ""
    confidence: float | None = None
    reason: str = ""

    @staticmethod
    def from_mapping(raw: Any, *, where: str, problems: list[str]) -> "SlotSource | None":
        data = _as_mapping(raw)
        if not data:
            problems.append(f"{where}: source 必须是对象")
            return None
        kind = _text(data.get("kind"))
        if kind not in SLOT_SOURCE_KINDS:
            problems.append(
                f"{where}: source.kind 必须是 {SLOT_SOURCE_KINDS} 之一，实际 {kind!r}")
            return None
        turn_id = _text(data.get("turn_id"))
        memory_id = _text(data.get("memory_id"))
        reason = _text(data.get("reason"))
        confidence = data.get("confidence")
        if confidence is not None:
            if not _is_number(confidence):
                problems.append(f"{where}: source.confidence 必须是数字")
                confidence = None
            elif not 0.0 <= float(confidence) <= 1.0:
                problems.append(f"{where}: source.confidence 必须在 [0,1]，实际 {confidence}")
                confidence = None
        # 各 kind 的必填项（§3.1 的联合类型里 user_utterance 带 turn_id、
        # memory 带 memory_id+confidence、default 带 reason）
        if kind == "user_utterance" and not turn_id:
            problems.append(f"{where}: source.kind=user_utterance 必须带 turn_id")
        if kind == "user_confirmed" and not turn_id:
            problems.append(f"{where}: source.kind=user_confirmed 必须带 turn_id")
        if kind == "memory":
            if not memory_id:
                problems.append(f"{where}: source.kind=memory 必须带 memory_id")
            if confidence is None:
                problems.append(f"{where}: source.kind=memory 必须带 confidence")
        if kind == "default" and not reason:
            problems.append(f"{where}: source.kind=default 必须带 reason")
        return SlotSource(kind=kind, turn_id=turn_id, memory_id=memory_id,
                          confidence=float(confidence) if confidence is not None else None,
                          reason=reason)


@dataclass(frozen=True)
class Slot:
    """契约中的一个字段值：值 + 来源 + 是否已确认 + 是否需要高亮。

    `confirmed` 是「用户是否在契约卡上确认过」。§3.4 的显式确认策略据此判断——
    high 风险要对高亮槽位逐项点头，靠的就是这个标记。
    """

    value: Any
    source: SlotSource
    confirmed: bool = False
    highlight: bool = False

    @staticmethod
    def from_mapping(raw: Any, *, where: str, problems: list[str],
                     value_parser: Any = None) -> "Slot | None":
        data = _as_mapping(raw)
        if not data:
            problems.append(f"{where}: 槽位必须是对象")
            return None
        if "value" not in data:
            problems.append(f"{where}: 槽位缺少 value")
            return None
        source = SlotSource.from_mapping(data.get("source"), where=where + ".source",
                                        problems=problems)
        value = data.get("value")
        if value_parser is not None:
            value = value_parser(value, where=where + ".value", problems=problems)
            if value is None:
                return None
        if source is None:
            return None
        return Slot(value=value, source=source,
                    confirmed=data.get("confirmed") is True,
                    highlight=data.get("highlight") is True)


# ---------- §3.1 InputRef ----------


@dataclass(frozen=True)
class InputRef:
    """一条输入引用。`uri` 放路径或 URL；敏感内容用 `secret_ref:`，不放明文。

    `exists` 由拦截器校验后填写（§3.2 G2）。本模块只搬运，不做 stat/探测——
    那需要 IO，而 IO 属于调用方（见 scope 文档「不做 IO」）。
    """

    type: str
    uri: str
    resolved_by: str = ""
    exists: bool | None = None

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str]) -> "InputRef | None":
        data = _as_mapping(value)
        if not data:
            problems.append(f"{where}: 输入必须是对象")
            return None
        kind = _text(data.get("type"))
        if kind not in INPUT_TYPES:
            problems.append(f"{where}: type 必须是 {INPUT_TYPES} 之一，实际 {kind!r}")
            return None
        uri = _text(data.get("uri"))
        if not uri:
            problems.append(f"{where}: uri 不能为空")
            return None
        exists = data.get("exists")
        if exists is not None and not isinstance(exists, bool):
            problems.append(f"{where}: exists 必须是布尔值")
            exists = None
        return InputRef(type=kind, uri=uri, resolved_by=_text(data.get("resolved_by")),
                        exists=exists)


# ---------- §3.1 ArtifactSpec ----------


@dataclass(frozen=True)
class ArtifactSpec:
    kind: str
    format: str = ""
    location: str = ""

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str]) -> "ArtifactSpec | None":
        data = _as_mapping(value)
        if not data:
            problems.append(f"{where}: artifact 必须是对象")
            return None
        kind = _text(data.get("kind"))
        if kind not in ARTIFACT_KINDS:
            problems.append(f"{where}: kind 必须是 {ARTIFACT_KINDS} 之一，实际 {kind!r}")
            return None
        return ArtifactSpec(kind=kind, format=_text(data.get("format")),
                            location=_text(data.get("location")))


# ---------- §3.1 Budget ----------


@dataclass(frozen=True)
class Budget:
    """预算。规范说「所有阈值和上限都是配置项」，所以这里的默认值只是兜底，
    真实值应当由 `config/contract.yaml` 提供（见 `contract` 的默认配置）。
    """

    max_retries: int = 3
    max_step_retries: int = 2
    max_wall_s: int = 900
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_tool_calls: int | None = None

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str],
              defaults: "Budget | None" = None) -> "Budget | None":
        base = defaults or Budget()
        data = _as_mapping(value)
        if value is not None and not data:
            problems.append(f"{where}: budget 必须是对象")
            return None

        def pick_int(key: str, fallback: int | None) -> int | None:
            if key not in data or data.get(key) is None:
                return fallback
            raw = data.get(key)
            if not _is_int(raw):
                problems.append(f"{where}.{key}: 必须是整数")
                return fallback
            if raw < 0:
                problems.append(f"{where}.{key}: 不能为负，实际 {raw}")
                return fallback
            return raw

        max_wall = pick_int("max_wall_s", base.max_wall_s)
        if max_wall is not None and max_wall <= 0:
            problems.append(f"{where}.max_wall_s: 必须为正数，实际 {max_wall}")
        cost = data.get("max_cost_usd")
        if cost is not None:
            if not _is_number(cost):
                problems.append(f"{where}.max_cost_usd: 必须是数字")
                cost = None
            elif float(cost) <= 0:
                problems.append(f"{where}.max_cost_usd: 必须为正数，实际 {cost}")
                cost = None
        return Budget(
            max_retries=pick_int("max_retries", base.max_retries),
            max_step_retries=pick_int("max_step_retries", base.max_step_retries),
            max_wall_s=max_wall if max_wall is not None else base.max_wall_s,
            max_tokens=pick_int("max_tokens", base.max_tokens),
            max_cost_usd=float(cost) if cost is not None else base.max_cost_usd,
            max_tool_calls=pick_int("max_tool_calls", base.max_tool_calls),
        )


# ---------- §3.1 SideEffect ----------


@dataclass(frozen=True)
class SideEffect:
    """一条副作用声明。§3.2 要求「必须穷举；运行中出现未声明的副作用即违规」——
    穷举的审计发生在运行时，属于 §4，不在本包；本包只校验声明本身。
    """

    id: str
    type: str
    target: str
    target_class: str
    reversible: bool
    has_backup: bool = False
    idempotency_key: str = ""

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str]) -> "SideEffect | None":
        data = _as_mapping(value)
        if not data:
            problems.append(f"{where}: 副作用必须是对象")
            return None
        effect_id = _text(data.get("id"))
        if not effect_id:
            problems.append(f"{where}: id 不能为空")
        kind = _text(data.get("type"))
        if kind not in SIDE_EFFECT_TYPES:
            problems.append(f"{where}: type 必须是 {SIDE_EFFECT_TYPES} 之一，实际 {kind!r}")
        target = _text(data.get("target"))
        if not target:
            problems.append(f"{where}: target 不能为空")
        target_class = _text(data.get("target_class"))
        if target_class not in SIDE_EFFECT_TARGET_CLASSES:
            problems.append(
                f"{where}: target_class 必须是 {SIDE_EFFECT_TARGET_CLASSES} 之一，实际 {target_class!r}")
        reversible = data.get("reversible")
        if not isinstance(reversible, bool):
            problems.append(f"{where}: reversible 必须是布尔值")
            reversible = False
        backup = data.get("has_backup")
        if backup is not None and not isinstance(backup, bool):
            problems.append(f"{where}: has_backup 必须是布尔值")
            backup = False
        key = _text(data.get("idempotency_key"))
        if not key:
            problems.append(f"{where}: idempotency_key 不能为空")
        if problems and any(item.startswith(where + ":") for item in problems):
            return None
        return SideEffect(id=effect_id, type=kind, target=target,
                          target_class=target_class, reversible=bool(reversible),
                          has_backup=bool(backup), idempotency_key=key)


# ---------- §3.1 RiskAssessment ----------


@dataclass(frozen=True)
class RiskAssessment:
    level: str
    reasons: tuple[str, ...] = ()

    def rank(self) -> int:
        return RISK_RANK.get(self.level, 0)


# ---------- §3.1 Criterion / CheckSpec ----------


@dataclass(frozen=True)
class CsvRule:
    name: str
    args: dict = field(default_factory=dict)

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str]) -> "CsvRule | None":
        data = _as_mapping(value)
        if not data:
            problems.append(f"{where}: 规则必须是对象")
            return None
        name = _text(data.get("name"))
        if not name:
            problems.append(f"{where}: name 不能为空")
            return None
        args = data.get("args")
        if args is not None and not isinstance(args, dict):
            problems.append(f"{where}: args 必须是对象")
            args = None
        return CsvRule(name=name, args=dict(args or {}))


@dataclass(frozen=True)
class CheckSpec:
    """检查方式。**只做结构与 lint 校验，不执行。**

    `exit_code_zero` 的 `cmd` 只检查是非空字符串，不跑命令——执行侧属于
    §4.9 交付与 Verifier，已冻结（见 docs/CONTRACT_V2_SCOPE.md §二.4）。
    """

    type: str
    path: str = ""
    format: str = ""
    schema_ref: str = ""
    lang: str = ""
    cmd: str = ""
    cwd: str = ""
    timeout_s: int | None = None
    min_bytes: int | None = None
    rules: tuple[CsvRule, ...] = ()
    rubric: tuple[str, ...] = ()
    evidence_required: bool = False
    question: str = ""
    options: tuple[str, ...] = ()

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str]) -> "CheckSpec | None":
        data = _as_mapping(value)
        if not data:
            problems.append(f"{where}: check 必须是对象")
            return None
        kind = _text(data.get("type"))
        if kind not in CHECK_TYPES:
            problems.append(f"{where}: type 必须是 {CHECK_TYPES} 之一，实际 {kind!r}")
            return None

        before = len(problems)
        out = CheckSpec(type=kind)
        if kind == "file_exists_nonempty":
            out = CheckSpec(type=kind, path=_text(data.get("path")),
                            min_bytes=_int_or_none(data.get("min_bytes"), where=where,
                                                  field_name="min_bytes", problems=problems))
        elif kind == "schema_valid":
            fmt = _text(data.get("format"))
            if fmt not in ("json", "csv"):
                problems.append(f"{where}: format 必须是 json 或 csv，实际 {fmt!r}")
            out = CheckSpec(type=kind, path=_text(data.get("path")), format=fmt,
                            schema_ref=_text(data.get("schema_ref")))
        elif kind == "syntax_ok":
            lang = _text(data.get("lang"))
            if lang not in ("python", "ts", "json", "yaml"):
                problems.append(
                    f"{where}: lang 必须是 python/ts/json/yaml 之一，实际 {lang!r}")
            out = CheckSpec(type=kind, path=_text(data.get("path")), lang=lang)
        elif kind == "exit_code_zero":
            out = CheckSpec(type=kind, cmd=_text(data.get("cmd")),
                            cwd=_text(data.get("cwd")),
                            timeout_s=_int_or_none(data.get("timeout_s"), where=where,
                                                   field_name="timeout_s",
                                                   problems=problems))
        elif kind == "csv_assert":
            rules = []
            for index, raw in enumerate(_as_list(data.get("rules"))):
                parsed = CsvRule.parse(raw, where=f"{where}.rules[{index}]", problems=problems)
                if parsed is not None:
                    rules.append(parsed)
            out = CheckSpec(type=kind, path=_text(data.get("path")), rules=tuple(rules))
        elif kind == "rubric":
            rubric = tuple(_text(item) for item in _as_list(data.get("rubric")) if _text(item))
            out = CheckSpec(type=kind, rubric=rubric,
                            evidence_required=data.get("evidence_required") is True)
        elif kind == "ask_user":
            options = tuple(_text(item) for item in _as_list(data.get("options")) if _text(item))
            out = CheckSpec(type=kind, question=_text(data.get("question")),
                            options=options)
        if len(problems) > before:
            return None
        return out


def _int_or_none(value: Any, *, where: str, field_name: str,
                 problems: list[str]) -> int | None:
    if value is None:
        return None
    if not _is_int(value):
        problems.append(f"{where}.{field_name}: 必须是整数")
        return None
    if value < 0:
        problems.append(f"{where}.{field_name}: 不能为负，实际 {value}")
        return None
    return value


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str
    kind: str
    check: CheckSpec
    required: bool = True
    promotion_weight: float = 1.0

    @staticmethod
    def parse(value: Any, *, where: str, problems: list[str]) -> "Criterion | None":
        data = _as_mapping(value)
        if not data:
            problems.append(f"{where}: 验收标准必须是对象")
            return None
        criterion_id = _text(data.get("id"))
        if not criterion_id:
            problems.append(f"{where}: id 不能为空")
        text = _text(data.get("text"))
        if not text:
            problems.append(f"{where}: text 不能为空")
        kind = _text(data.get("kind"))
        if kind not in CRITERION_KINDS:
            problems.append(f"{where}: kind 必须是 {CRITERION_KINDS} 之一，实际 {kind!r}")
            return None
        check = CheckSpec.parse(data.get("check"), where=where + ".check", problems=problems)
        weight = data.get("promotion_weight")
        if weight is None:
            weight = DEFAULT_PROMOTION_WEIGHT[kind]
        elif not _is_number(weight):
            problems.append(f"{where}: promotion_weight 必须是数字")
            weight = DEFAULT_PROMOTION_WEIGHT[kind]
        elif not 0.0 <= float(weight) <= 1.0:
            problems.append(f"{where}: promotion_weight 必须在 [0,1]，实际 {weight}")
            weight = DEFAULT_PROMOTION_WEIGHT[kind]
        if check is None:
            return None
        return Criterion(id=criterion_id, text=text, kind=kind, check=check,
                         required=data.get("required") is not False,
                         promotion_weight=float(weight))


# ---------- §3.1 TaskContract ----------


def slot_to_mapping(item: "Slot | None") -> dict[str, Any] | None:
    """把槽位回写成规范形状的 dict。

    **这里必须把嵌套值序列化**：InputRef 与 ArtifactSpec 是 dataclass，
    直接塞进 dict 会让 json.dumps 报 "Object of type ArtifactSpec is not
    JSON serializable"，也会让 parse_contract(to_mapping()) 失败。
    早期 TaskContract.to_mapping 就漏了这一步（只有标量槽位能过），
    而 gate._draft_payload 是对的——两处各写一遍才出的这个 bug。
    现在两处共用本函数。
    """
    if item is None:
        return None
    value = item.value
    if isinstance(value, InputRef):
        payload: dict[str, Any] = {"type": value.type, "uri": value.uri}
        if value.resolved_by:
            payload["resolved_by"] = value.resolved_by
        if value.exists is not None:
            payload["exists"] = value.exists
        value = payload
    elif isinstance(value, ArtifactSpec):
        payload = {"kind": value.kind}
        if value.format:
            payload["format"] = value.format
        if value.location:
            payload["location"] = value.location
        value = payload
    source = {"kind": item.source.kind}
    if item.source.turn_id:
        source["turn_id"] = item.source.turn_id
    if item.source.memory_id:
        source["memory_id"] = item.source.memory_id
    if item.source.confidence is not None:
        source["confidence"] = item.source.confidence
    if item.source.reason:
        source["reason"] = item.source.reason
    out: dict[str, Any] = {"value": value, "source": source,
                           "confirmed": item.confirmed}
    if item.highlight:
        out["highlight"] = True
    return out

@dataclass(frozen=True)
class TaskContract:
    """冻结后的结构化任务单。

    `version` 的迁移守卫（SPEC_LOCKED 后只读、任何改动回到 PROPOSED 并递增）
    属于 §4 状态机，本包只校验「是正整数」。
    """

    contract_id: str
    version: int
    created_at: str
    utterance: str
    goal: Slot
    inputs: tuple[Slot, ...]
    artifact: Slot
    acceptance: tuple[Criterion, ...]
    risk: RiskAssessment
    side_effects: tuple[SideEffect, ...]
    permissions_required: tuple[str, ...]
    budget: Budget
    deliver_to: Slot | None = None

    def to_mapping(self) -> dict[str, Any]:
        """回写成规范形状的 dict，可被 parse_contract 再读回去。

        嵌套的 InputRef / ArtifactSpec 会被序列化成 §3.1 的对象形状
        （由 slot_to_mapping 负责），所以 round-trip 成立。
        """
        slot = slot_to_mapping

        return {
            "contract_id": self.contract_id,
            "version": self.version,
            "created_at": self.created_at,
            "utterance": self.utterance,
            "goal": slot(self.goal),
            "inputs": [slot(item) for item in self.inputs],
            "artifact": slot(self.artifact),
            "deliver_to": slot(self.deliver_to),
            "acceptance": [
                {
                    "id": item.id, "text": item.text, "kind": item.kind,
                    "check": _check_to_mapping(item.check),
                    "required": item.required,
                    "promotion_weight": item.promotion_weight,
                }
                for item in self.acceptance
            ],
            "risk": {"level": self.risk.level, "reasons": list(self.risk.reasons)},
            "side_effects": [
                {
                    "id": item.id, "type": item.type, "target": item.target,
                    "target_class": item.target_class, "reversible": item.reversible,
                    "has_backup": item.has_backup,
                    "idempotency_key": item.idempotency_key,
                }
                for item in self.side_effects
            ],
            "permissions_required": list(self.permissions_required),
            "budget": {
                "max_retries": self.budget.max_retries,
                "max_step_retries": self.budget.max_step_retries,
                "max_wall_s": self.budget.max_wall_s,
                "max_tokens": self.budget.max_tokens,
                "max_cost_usd": self.budget.max_cost_usd,
                "max_tool_calls": self.budget.max_tool_calls,
            },
        }


def _check_to_mapping(check: CheckSpec) -> dict[str, Any]:
    out: dict[str, Any] = {"type": check.type}
    for name in ("path", "format", "schema_ref", "lang", "cmd", "cwd"):
        value = getattr(check, name)
        if value:
            out[name] = value
    if check.timeout_s is not None:
        out["timeout_s"] = check.timeout_s
    if check.min_bytes is not None:
        out["min_bytes"] = check.min_bytes
    if check.rules:
        out["rules"] = [{"name": rule.name, **({"args": rule.args} if rule.args else {})}
                        for rule in check.rules]
    if check.rubric:
        out["rubric"] = list(check.rubric)
        out["evidence_required"] = check.evidence_required
    if check.question:
        out["question"] = check.question
    if check.options:
        out["options"] = list(check.options)
    return out


def parse_contract(raw: Any) -> TaskContract:
    """把规范形状的 dict 读成 TaskContract。

    结构性错误一次性全部抛出（ContractShapeError.problems），不遇到第一个就停——
    lint 与 gate 都需要看到完整问题列表才能给出有用的追问。

    **`risk` 会被重算覆盖。** §3.2 写明「risk 由规则（3.4）计算，不允许手填降级」。
    早期实现只校验传入 risk 的**形状**，于是直接调本函数就能把手填的
    `risk.level='low'` 塞进契约（而真实规则值是 high）——公开 API 上没守住这条。

    另注：这是「读入并规范化」，**不是**「已验收可执行」。要冻结一份契约请走
    `gate.freeze()`，它跑完整 G1–G6 与 §3.7 lint。
    """
    problems: list[str] = []
    data = _as_mapping(raw)
    if not data:
        raise ContractShapeError(["contract 必须是对象"])

    contract_id = _text(data.get("contract_id"))
    if not contract_id:
        problems.append("contract_id 不能为空")

    version = data.get("version")
    if not _is_int(version) or version <= 0:
        problems.append("version 必须是正整数")

    created_at = _text(data.get("created_at"))
    if not created_at:
        problems.append("created_at 不能为空")

    utterance = _text(data.get("utterance"))
    if not utterance:
        problems.append("utterance 不能为空")

    goal = Slot.from_mapping(data.get("goal"), where="goal", problems=problems,
                             value_parser=lambda value, *, where, problems: _goal_text(
                                 value, where=where, problems=problems))
    artifact = Slot.from_mapping(data.get("artifact"), where="artifact",
                                 problems=problems,
                                 value_parser=lambda value, *, where, problems:
                                 ArtifactSpec.parse(value, where=where, problems=problems))
    deliver_to_raw = data.get("deliver_to")
    deliver_to = None
    if deliver_to_raw is not None:
        deliver_to = Slot.from_mapping(deliver_to_raw, where="deliver_to",
                                       problems=problems,
                                       value_parser=lambda value, *, where, problems: _text(value)
                                       or (problems.append(f"{where}: 必须是文本") or None))

    inputs = []
    for index, item in enumerate(_as_list(data.get("inputs"))):
        parsed = Slot.from_mapping(item, where=f"inputs[{index}]", problems=problems,
                                   value_parser=lambda value, *, where, problems:
                                   InputRef.parse(value, where=where, problems=problems))
        if parsed is not None:
            inputs.append(parsed)

    acceptance = []
    for index, item in enumerate(_as_list(data.get("acceptance"))):
        parsed = Criterion.parse(item, where=f"acceptance[{index}]", problems=problems)
        if parsed is not None:
            acceptance.append(parsed)

    side_effects = []
    for index, item in enumerate(_as_list(data.get("side_effects"))):
        parsed = SideEffect.parse(item, where=f"side_effects[{index}]", problems=problems)
        if parsed is not None:
            side_effects.append(parsed)

    permissions: list[str] = []
    raw_permissions = data.get("permissions_required")
    if not isinstance(raw_permissions, list):
        problems.append("permissions_required 必须是数组")
    else:
        for index, item in enumerate(raw_permissions):
            text = _text(item)
            if not text:
                problems.append(f"permissions_required[{index}]: 必须是非空字符串")
                continue
            permissions.append(text)
        # 注意：这里**不校验等级合法性**。§3.2 G6 要求「权限在 7.1 的可授予范围内」，
        # 但 §7 缺失，等级定义不存在。见 docs/CONTRACT_V2_SCOPE.md §二.2。

    budget = Budget.parse(data.get("budget"), where="budget", problems=problems)

    # risk 字段仍然必须存在（§3.1 的 TaskContract 有它），但值一律重算覆盖。
    risk_raw = _as_mapping(data.get("risk"))
    if not risk_raw:
        problems.append("risk 字段必须存在（其值会被 §3.4 规则重算覆盖）")
    else:
        level = _text(risk_raw.get("level"))
        if level and level not in RISK_LEVELS:
            problems.append(f"risk.level 必须是 {RISK_LEVELS} 之一，实际 {level!r}")

    if problems:
        raise ContractShapeError(problems)

    # §3.2：不允许手填降级。重算覆盖，不采信传入值。
    from .risk import compute_risk as _compute_risk

    risk = _compute_risk(tuple(side_effects))

    assert goal is not None and artifact is not None and budget is not None
    return TaskContract(
        contract_id=contract_id, version=version, created_at=created_at,
        utterance=utterance, goal=goal, inputs=tuple(inputs), artifact=artifact,
        acceptance=tuple(acceptance), risk=risk, side_effects=tuple(side_effects),
        permissions_required=tuple(permissions), budget=budget, deliver_to=deliver_to,
    )


def _goal_text(value: Any, *, where: str, problems: list[str]) -> str | None:
    text = _text(value)
    if not text:
        problems.append(f"{where}: 目标不能为空")
        return None
    return text

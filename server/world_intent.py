"""像素世界的意图端点：把一句人话翻成世界能执行的 JSON。

**这是规则解析器的兜底，不是主路径。** 世界侧先用 `parseOracle*` 三连匹配，
认得出就根本不发这个请求（`intent-router.ts` 的 `source: 'rule'`）。到这里来的
句子，一定是规则词表覆盖不到的同义改写——「把那份季度报告弄一下」「种棵树」。

设计上有三条与主产品不同的选择，都是为了这个原型的**可解释性**：

1. **无 Key 时返回 503，不返回猜的。** 主产品的 `LLM._mock` 会给一句闲聊，
   那对对话是对的（总比不说话强），但对意图是灾难——世界会照着一句闲聊动。
   这里宁可让前端显示"没听懂"。

2. **校验在世界侧（TypeScript），这里只保证 JSON 形状。** 世界的能力表在
   前端（模板来自 `prefabs.json`）；Python 侧抄一份必然漂移。所以这里把
   模板表从请求里接过来拼提示词，而不是自己维护一份。

3. **温度 0、max_tokens 小。** 意图解析要的是稳定与便宜，不是文采。
   同一句话两次问出不同答案的系统没法测。

协议见 docs/WORLD_INTENT.md。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .llm import LLM

logger = logging.getLogger("aqi.world_intent")

# 意图解析不该等太久：世界侧会显示"正在问模型"，超过这个时间就当没听懂。
INTENT_TIMEOUT_SECONDS = 8.0
# 预算给足。实测 200 不够：StepFun 的推理模型会先思考再写正文，
# 思考吃掉的预算无法从外部预估。一份意图 JSON 本身只有几十 token，
# 多给的部分是留给思考的——被截断的代价是一次失败的往返，比多花几 token 贵得多。
INTENT_MAX_TOKENS = 1024
# 显式压低推理强度。意图解析是分类任务，不需要长思考链；
# 留空表示不指定，由模型自己决定（不同网关对未知字段的容忍度不同）。
INTENT_REASONING_EFFORT = "low"


class TemplateIn(BaseModel):
    """世界告诉服务端"我能建什么"。由前端从蓝图注册表现场发来。"""

    id: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    boundTask: str | None = None


class BuiltEntryIn(BaseModel):
    """一栋已有的建筑。只要 id 与显示名，不要坐标、不要占地。"""

    templateId: str = Field(min_length=1)
    name: str = Field(min_length=1)


class BuiltIn(BaseModel):
    town: list[BuiltEntryIn] = Field(default_factory=list)
    wild: list[BuiltEntryIn] = Field(default_factory=list)


class CapacityIn(BaseModel):
    taskKind: str = Field(min_length=1)
    count: int = Field(ge=0)


class BusyIn(BaseModel):
    name: str = Field(min_length=1)
    taskKind: str = Field(min_length=1)


class ContextIn(BaseModel):
    """世界现状。**可选**：旧客户端不发这个字段，服务端按「没有现状」处理。

    这里刻意不校验 taskKind 的取值域：那是世界的枚举，服务端抄一份必然漂移。
    它只被拼进提示词，不参与任何判定——模型收到什么就描述什么。
    """

    built: BuiltIn = Field(default_factory=BuiltIn)
    capacity: list[CapacityIn] = Field(default_factory=list)
    busy: list[BusyIn] = Field(default_factory=list)
    queued: int = Field(default=0, ge=0)
    weather: str = "clear"
    exhausted: list[str] = Field(default_factory=list)


class IntentIn(BaseModel):
    utterance: str = Field(min_length=1)
    templates: list[TemplateIn] = Field(default_factory=list)
    context: ContextIn | None = None


class IntentOut(BaseModel):
    intent: dict[str, Any]
    model: str
    source: str = "model"


TASK_HINTS = {
    "code": "写代码、写脚本、跑程序、爬虫",
    "research": "查资料、搜东西、调研、研究",
    "writing": "写文稿、整理便签、改稿、草拟",
    "quick": "顺手的小事，不值得开一间铺子",
}

# 纸面说法。与 pixel-world 的 `TASK_PROFILES[kind].label` 同源，
# 改一处要改两处——但它是给模型读的中文，不是枚举，抄一份比跨语言取更简单。
TASK_LABEL = {
    "code": "写代码",
    "research": "查资料",
    "writing": "整理便笺",
    "quick": "顺手的活",
}


def build_prompt(
    templates: list[TemplateIn],
    context: ContextIn | None = None,
) -> str:
    """拼系统提示词。

    与 pixel-world 的 `intent-prompt.ts` 是**同一份契约的两处实现**：
    前者给测试与离线推演用，这里给线上调用用。两边都由模板表驱动，
    所以模板变了不会漏改——但措辞改动要两边一起改。

    注意这里刻意不写"你能建任何东西"：模板表为空时必须如实说"不能建"。
    """
    task_lines = "\n".join(f"  - {kind}：{hint}" for kind, hint in TASK_HINTS.items())
    if templates:
        template_lines = "\n".join(
            f"  - {t.id}（{t.displayName}，"
            + ("不接活" if t.boundTask is None else f"接{TASK_HINTS.get(t.boundTask, t.boundTask)}")
            + "）"
            for t in templates
        )
    else:
        template_lines = "  （这个世界现在没有可建的模板，不要输出 build）"

    context_section = _context_section(context)

    return f"""你是像素世界里那个世界的**意图解析器**。用户对世界说一句话，你把它翻成一条命令。

只输出 JSON 对象，不要解释、不要 Markdown 代码块、不要任何前后缀。

## 能表达的命令（只有这八种）

- {{"kind":"dispatch","taskKind":"<类>","summary":"<要做的事>"}} 派一件活
- {{"kind":"complete"}} 交付
- {{"kind":"cancelPending"}} 撤销排队中的神谕
- {{"kind":"setWeather","weather":"clear"|"rain"}} 改天气
- {{"kind":"build","templateId":"<模板>"}} 建一栋楼，可带 "at":{{"x":整数,"y":整数}}
- {{"kind":"demolish","templateId":"<模板>"}} 拆一栋楼
- {{"kind":"miracle","miracle":"light"|"tree"|"cat"}} 点灯 / 种树 / 召唤猫，可带 "at"
- {{"kind":"unknown","reason":"<为什么听不出>"}} 这句话不是在要求世界做事

## 活分四类

{task_lines}

## 能建的模板

{template_lines}
{context_section}
## 三条纪律

1. **听不出就答 unknown。** 闲聊、提问、感叹、抱怨——一律 unknown，并在 reason 里说清是什么。
   世界宁可不动，也不替用户做他没说的事。猜错比不猜伤害大得多。
2. **只输出 JSON。** 不要解释。
3. **不要编造字段。** 每种命令能带的字段固定，多一个都会被世界拒掉。

## 判断分寸：分不清细节 = 派活，不是 unknown

「把那份季度报告弄一下」「帮我看看这块代码」这类话，**动作词模糊但意图明确**：
用户是要派一件活给镇上的铺子。具体是写、是查、还是整理，**由世界自己按关键词归类**，
不需要你判断。

所以：**当一句话是"让某件东西被处理"时，答 dispatch，把用户原话放进 summary。**
不要因为「弄」「看看」「处理一下」不够具体就答 unknown——那样用户会觉得你没听懂，
而他明明说清了要处理什么。

**同一条也管建造。「再盖一间」「给我整个新铺子」没说盖什么，仍要答 build。**
挑哪个模板是世界的判断（省略 `templateId` 之外的细节，由世界挑），不是你的。
只有当用户要在**具体坐标**建某样东西时才需要 `at` 与确切的 `templateId`。

只有**完全没有要处理的对象**时才是 unknown：
- 「今天好累啊」——没有对象
- 「你觉得该怎么办」——在征求意见
- 「外面在下雨吗」——在提问
- 「这个 bug 怎么写都不对」——在抱怨，没有要求

对照表：

| 用户说 | 答 | 为什么 |
|---|---|---|
| 把那份季度报告弄一下 | dispatch（summary 用原话） | 有对象，要派活；具体类型世界自己归类 |
| 帮我看看这块代码为啥跑不起来 | dispatch | 有对象（代码），要派活 |
| 今天好累啊 | unknown | 没有对象 |
| 你觉得该怎么办 | unknown | 征求意见 |

例：「帮我写个爬虫」→ dispatch/code；「建个工坊」→ build；「外面在下雨吗」→ unknown（提问）；
「这个 bug 怎么写都不对」→ unknown（抱怨）；「今天好累啊」→ unknown（闲聊）；
「把那份季度报告弄一下」→ dispatch（有对象，要派活）。"""


def _context_section(context: ContextIn | None) -> str:
    """把世界现状渲染成提示词里的一段。

    与 `intent-context.ts` 的 `renderIntentContext` 是同一份渲染的两处实现，
    措辞改动要两边一起改。省略时不出现整段，而不是写「世界状态未知」——
    一句"未知"会让模型以为它该去猜，而它猜不出来。
    """
    if context is None:
        return ""

    if not context.built.town and not context.built.wild:
        lines = ["世界是空的，一栋楼都没有。"]
    else:
        lines = [
            f"镇内已有：{_names(context.built.town)}",
            f"郊野已有：{_names(context.built.wild)}",
        ]

    total = sum(entry.count for entry in context.capacity)
    per_kind = " / ".join(
        f"{TASK_LABEL.get(entry.taskKind, entry.taskKind)} {entry.count}"
        for entry in context.capacity
    )
    lines.append(f"能接活的铺子 {total} 栋（{per_kind}）。" if per_kind else f"能接活的铺子 {total} 栋。")

    if context.busy:
        doing = "、".join(
            f"{item.name}在做{TASK_LABEL.get(item.taskKind, item.taskKind)}"
            for item in context.busy
        )
        lines.append(f"正在忙：{doing}。")
    else:
        lines.append("现在没人干活。")

    if context.queued > 0:
        lines.append(f"还有 {context.queued} 条神谕在排队。")
    lines.append("外面在下雨。" if context.weather == "rain" else "天晴。")

    if context.exhausted:
        lines.append(f"这些模板已经建过，不能再建第二栋：{'、'.join(context.exhausted)}。")

    return f"""
## 世界现在长什么样

{chr(10).join(lines)}

## 看到现状之后怎么判断

**现状不改变你输出的命令。** 它是背景，不是判据。

- **已经有了某栋楼，仍然照常输出 build。** 「建个工坊」这句话的意思就是要建，
  世界会拒绝并说明已经有了。你替世界提前判 unknown，用户会以为你没听懂。
- **已经有活干着，仍然照常输出 dispatch。**
- **看到「没人干活」不要自己补一句派活。** 用户没说要派，就不要派。
- **只有用户在问现状时才答 unknown。** 「镇上现在有几栋楼」「已经有个工坊了吧」
  是在打听，不是在要求改。这两者靠**有没有要求**区分，不靠**世界现在什么样**区分。

判断口诀：看用户**要你做什么**，不看世界**现在是什么样**。
"""


def _names(entries: list[BuiltEntryIn]) -> str:
    if not entries:
        return "空着"
    return "、".join(entry.name for entry in entries)


def create_world_intent_router(llm: LLM | None = None) -> APIRouter:
    """构造路由。`llm` 可注入，方便测试不联网。"""
    router = APIRouter(prefix="/api/world", tags=["world"])
    client = llm or LLM(request_timeout=INTENT_TIMEOUT_SECONDS)

    @router.post("/intent", response_model=IntentOut)
    async def classify(payload: IntentIn) -> IntentOut:
        # 没有 Key 时明确拒绝。返回一句猜的意图会让世界照着闲聊动。
        if client.offline:
            raise HTTPException(
                status_code=503,
                detail="未配置 LLM_API_KEY，意图模型不可用；规则解析仍可离线工作。",
            )

        utterance = payload.utterance.strip()
        if not utterance:
            raise HTTPException(status_code=422, detail="utterance 不能为空")

        messages = [
            {"role": "system", "content": build_prompt(payload.templates, payload.context)},
            {"role": "user", "content": utterance},
        ]
        try:
            result = await client.complete_raw(
                messages,
                temperature=0.0,
                max_tokens=INTENT_MAX_TOKENS,
                json_mode=True,
                reasoning_effort=INTENT_REASONING_EFFORT,
            )
        except Exception as error:  # noqa: BLE001 - 上游任何失败都等于"没听懂"
            logger.warning("intent model failed: %s", error)
            raise HTTPException(status_code=502, detail=f"意图模型调用失败：{error}") from error

        text = result.get("text", "")
        finish = result.get("finish_reason", "")

        # 空输出与截断要分开报，否则线上看起来都是"模型没返回 JSON"，
        # 分不清是预算太小还是模型真没话说。实测两种都出现过：
        #   - StepFun 的推理模型会把预算全花在思考上，正文为空（finish=length）
        #   - 预算刚好够思考、不够写正文时，正文被截成 '{"kind":"dispatch'
        # 先判空再判截断：空正文比"被截断"更具体，也更能指向真正的病因——
        # 推理模型可能思考完就没预算写正文了，这时报"截断"会误导排查方向。
        if not text.strip():
            raise HTTPException(
                status_code=502,
                detail=f"意图模型没有产出正文（finish_reason={finish or '未知'}）",
            )
        if finish == "length":
            raise HTTPException(
                status_code=502,
                detail=(
                    f"意图模型的输出被截断（max_tokens={INTENT_MAX_TOKENS}，"
                    f"正文 {len(text)} 字符）。推理模型需要更大的预算。"
                ),
            )

        try:
            intent = _loose_load(text)
        except (json.JSONDecodeError, ValueError) as error:
            logger.warning("intent model returned non-JSON: %r", text[:200])
            raise HTTPException(
                status_code=502,
                detail=f"意图模型没有返回 JSON（正文 {len(text)} 字符）",
            ) from error

        if not isinstance(intent, dict):
            raise HTTPException(status_code=502, detail="意图模型返回的不是 JSON 对象")

        return IntentOut(intent=intent, model=settings.llm_model)

    return router


def _loose_load(text: str) -> Any:
    """从模型输出里抠出 JSON。

    模型偶尔会裹一层 ```json 或加一句前言。这里只做最小限度的宽容——
    抠不出就报错，不做别的猜测。
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # 去掉首行 ``` / ```json 与末行 ```
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            stripped = "\n".join(lines[1:-1])
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])

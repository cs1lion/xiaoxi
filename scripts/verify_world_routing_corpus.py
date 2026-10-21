# -*- coding: utf-8 -*-
r"""只读验证脚本：Agent⇄像素世界桥接的意图分流与派单正则边界语料（ADR §4.4/§4.4.1）。

不 import server 模块、不连库、不写任何项目文件；正则逐字转写自：
  server/dialogue/orchestrator.py:60-76（_DELEGATION/_PROGRESS/_TELEMETRY/_ACCEPT/_REJECT）
  server/assistant/service.py:95（should_route 显式正则）、:99-101（_resume_request）
  server/assistant/loop.py:29-47（Tool.validate 语义，用于契约级 mock 决策断言）

两类断言（Chief 2026-10-20 要求）：
  A 类·当前分流：正则可判定——今天落普通对话 / 命中快环 / 命中 should_route。
  B 类·扩词后进 AgentLoop：构造级——should_route 增加世界短语后正例改判 True；
     并用契约级 mock（非真实 AgentLoop）验证 world.dispatch 工具可被选中、
     receipt DTO 形状符合 ADR §4.2、mutation 先过确认门。

用法：.\.venv\Scripts\python.exe scripts\verify_world_routing_corpus.py
退出码 0 = 全 PASS；输出可直接贴回 docs/ADR_AGENT_BRIDGE.md。
"""
import re
import sys

# ---------- 转写自 server/dialogue/orchestrator.py:60-76 ----------
_DELEGATION = re.compile(
    r"(?:去(?:把|帮我|替我)\s*[^，。！？]{1,24}(?:做|改|整理|查|弄|搞|处理|安排|写)|"
    r"(?:安排|派|交)\s*(?:人|给|到|一下)?\s*[^，。！？]{0,12}(?:做|处理|干|弄|搞)|"
    r"(?:交给|丢给|扔给|托给|委托)\s*[^，。！？]{1,16}|"
    r"(?:让|叫)\s*[^，。！？]{1,12}\s*(?:去|来)\s*(?:做|处理|干|弄|搞|查)|"
    r"(?:盯着|盯一下|跟进一下|跟进|看着办|做完再叫我|有结论再叫我))")
_PROGRESS = re.compile(
    r"(?:进度|怎么样了|做得怎么样|做完没|做完了吗|有结论了吗|还在跑|还在做|"
    r"等到什么时候|什么时候能好|交差了没|好了吗)")
_TELEMETRY = re.compile(
    r"(?:API|api|接口|调用|token|Token|TOKEN|延迟|TTFT|战况|烧了|花了多少|"
    r"消耗|额度|用量|监控副屏|副屏)")
_ACCEPT = re.compile(
    r"^\s*(?:收工|可以了|行吧|就这样|没问题|不错|挺好|成交|通过|收下|存档吧|归档吧|"
    r"这单结了|结了|留下吧)\s*[。！!？?\s]*$")
_REJECT = re.compile(
    r"(?:不行|打回|重做|退回|再改|改一下|换个|重来|不满意|不对|不是这样|漏了|少了|退回去)")
# ---------- 转写自 server/assistant/service.py:95,99-101 ----------
_SHOULD_ROUTE = re.compile(
    r'提醒|待办|任务|帮我(?:整理|写|安排|计划)|取消.*提醒|延后|(?:搜索|查找|读取)(?:已保存的)?(?:笔记|便笺)')
_RESUME = re.compile(
    r'\s*(?:(?:继续|接着)(?:昨天|上次)(?:的工作|那份笔记|那份便笺|那份稿子)?|'
    r'回到上次的工作|继续这份(?:笔记|便笺|稿子))[。！!？?\s]*')

# ADR §4.4 选项 A：只扩"显式点名像素世界"的短语（不能用裸"世界"，会误伤日常句）
WORLD_PHRASES = ('像素世界', '世界里', '小镇', 'pixel world', 'Pixel World')
WORLD_PHRASE_RX = '像素世界|世界里|小镇|pixel world|Pixel World'
_SHOULD_ROUTE_EXPANDED = re.compile(
    _SHOULD_ROUTE.pattern + '|' + WORLD_PHRASE_RX)   # 构造级：扩词后的 should_route

POS_QUERY = [
    '去像素世界里看看她在干什么', '像素世界现在什么情况', '世界里的铺子都开着吗',
    '去看看像素世界', '像素世界里有新的活吗', '把 pixel world 的状态报给我',
    '刷新一下像素世界的画面', '像素世界需不需要我帮忙', '帮我去像素世界那边瞅瞅',
    '像素世界 offline 了吗',
]
POS_DISPATCH = [
    '让像素世界接一单把脚本写完', '派一单给像素世界里的她：整理便笺',
    '像素世界接一单，把便笺整理成三版标题',
]
NEGATIVE = [
    ('安排人把便笺做成三版', '快环 delegation'),
    ('让她去把这份稿子改了', '快环 delegation'),
    ('盯着这个单做完再叫我', '快环 delegation'),
    ('进度怎么样了', '快环 progress'),
    ('战况烧了多少 token', '快环 telemetry'),
    ('收工', '快环 accept'),
    ('不行打回重做', '快环 reject'),
    ('建个任务明天下午三点评审', '显式 should_route 命中，task 原路'),
    ('取消提醒', '显式 should_route 命中，reminder 原路'),
    ('便笺整理一下', '显式正则未命中（便笺会话路径未纳入本次核验）'),
    ('世界杯开始了吗', '日常句·无关'),
    ('你的世界观是什么', '日常句·无关'),
    ('昨晚世界杯看了没', '日常句·无关'),
    ('她最近世界观变了不少', '日常句·无关'),
]
EVERYDAY = ('世界杯开始了吗', '你的世界观是什么', '昨晚世界杯看了没', '她最近世界观变了不少')
DISPATCH_HIT = ('你去把便笺整理成三版标题', '去把脚本写完', '安排人把便笺做成三版', '让她去把稿子改了')
DISPATCH_SILENT = ('帮我把便笺整理成三版标题', '整理一下便笺', '把这堆便笺弄成三版',
                   '辛苦你把便笺整理一下', '麻烦整理下便笺', '便笺整理成三版标题')

failures = []
count = 0


def check(group, text, ok, detail=''):
    global count
    count += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {group} | {text!r} {detail}")
    if not ok:
        failures.append((group, text))


def fast_loop_hit(text):
    for name, rx in (('delegation', _DELEGATION), ('progress', _PROGRESS),
                     ('telemetry', _TELEMETRY), ('accept', _ACCEPT), ('reject', _REJECT)):
        if rx.search(text):
            return name
    return None


def should_route_hit(text):
    return bool(_SHOULD_ROUTE.search(text) or _RESUME.fullmatch(text))


def should_route_hit_expanded(text):
    return bool(_SHOULD_ROUTE_EXPANDED.search(text) or _RESUME.fullmatch(text))


def world_phrase_hit(text):
    return any(p in text for p in WORLD_PHRASES)


# ---------- A 类·当前分流 ----------
print('== A1 正例·查询类（当前：无快环命中、无 should_route → 落普通对话）==')
for t in POS_QUERY:
    fl, sr, wp = fast_loop_hit(t), should_route_hit(t), world_phrase_hit(t)
    check('A1', t, fl is None and not sr and wp, f'fast={fl} should_route={sr} world={wp}')

print('== A2 正例·派单类（当前：不被 _DELEGATION 命中 + 含世界短语）==')
for t in POS_DISPATCH:
    fl, wp = fast_loop_hit(t), world_phrase_hit(t)
    check('A2', t, fl != 'delegation' and wp, f'fast={fl} world={wp}')

print('== A3 反例（不含世界短语 → 扩词按构造不改变其分流；记录当前分流）==')
for t, claim in NEGATIVE:
    fl, sr, wp = fast_loop_hit(t), should_route_hit(t), world_phrase_hit(t)
    check('A3', t, not wp, f'fast={fl or "-"} should_route={sr} world={wp}（{claim}）')

print('== A4 日常句严格断言（世界杯/世界观：当前落普通对话，扩词后仍须不变）==')
for t in EVERYDAY:
    fl, sr, wp = fast_loop_hit(t), should_route_hit(t), world_phrase_hit(t)
    check('A4', t, fl is None and not sr and not wp, f'fast={fl} should_route={sr} world={wp}')

print('== A5 派单正则边界·建单组（_DELEGATION 命中 → executive_route 派单）==')
for t in DISPATCH_HIT:
    check('A5', t, fast_loop_hit(t) == 'delegation', f'fast={fast_loop_hit(t)}')

print('== A6 派单正则边界·静默组（不命中 _DELEGATION + should_route 不命中 → route=None → 不建单、无提示）==')
for t in DISPATCH_SILENT:
    fl, sr, wp = fast_loop_hit(t), should_route_hit(t), world_phrase_hit(t)
    # 三者俱全才叫"静默落普通对话"：只验快环不命中会漏掉本应进 should_route 的句子
    check('A6', t, fl != 'delegation' and not sr and not wp,
          f'fast={fl or "-"} should_route={sr} world={wp}')

# ---------- B 类·扩词后进 AgentLoop（构造级 + 契约级 mock） ----------
print('== B1 扩词后 should_route（构造级：正例改判 True；反例逐条一致 after==before）==')
for t in POS_QUERY + POS_DISPATCH:
    before, after = should_route_hit(t), should_route_hit_expanded(t)
    check('B1', t, (not before) and after, f'before={before} after={after}')
for t, _ in NEGATIVE:
    before, after = should_route_hit(t), should_route_hit_expanded(t)
    # 差分铁律：扩词不得改变任何既有句子的路由输出（本来就命中的仍命中，本来不命中的仍不命中）
    check('B1', t, after == before, f'before={before} after={after}（须一致）')

print('== B2 契约级 mock 决策（非真实 AgentLoop：工具可被选中 + receipt DTO + 确认门 + stub handler）==')


def tool_validate(name, parameters, required, arguments):
    """语义同 server/assistant/loop.py:29-47 的最小复刻（类型/必填/未知键）。"""
    if not isinstance(arguments, dict) or set(arguments) - set(parameters):
        raise ValueError('invalid_tool_arguments')
    if any(key not in arguments for key in required):
        raise ValueError('missing_tool_arguments')
    for key, value in arguments.items():
        expected = parameters[key]
        valid = type(value) in (int, float) if expected is float else type(value) is expected
        if not valid:
            raise ValueError(f'invalid_argument_type:{key}')
    return arguments


WORLD_DISPATCH = {'parameters': {'title': str, 'brief': str}, 'required': ('title',),
                  'mutation': True}


def world_dispatch_stub(title, brief=""):
    """ADR §4.2 形状的 stub handler（实现期由 service.py 落地）。

    存在只为证一件事：`_execute` 用 `handler(**arguments)` 展开（loop.py:493），
    模型只传 title 时，缺省参数 brief="" 是 TypeError 的唯一屏障。
    """
    begun = {'commission_id': 'c_mock00000001', 'status': 'running',
             'agent_id': 'scribe', 'agent_name': '笔录官'}
    return {'id': begun['commission_id'], 'commission_id': begun['commission_id'],
            'status': begun['status'], 'agent_name': begun['agent_name'],
            'world_visible': 'unknown'}


def world_dispatch_no_default(title, brief):
    """反事实签名（ADR §4.2 之前的写法）：缺省必炸，用于证明默认值的必要性。"""
    return world_dispatch_stub(title, brief)


def mock_run(user_text):
    """mock 模型：派单正例 → world.dispatch；其余 → final。返回 (决策, 回执流水)。"""
    if user_text in POS_DISPATCH:
        decision = {'action': 'tool_call', 'tool': 'world.dispatch',
                    'arguments': {'title': '把便笺整理成三版标题', 'brief': user_text}}
        trace = []
        if WORLD_DISPATCH['mutation']:
            trace.append('waiting_confirmation')          # E3：mutation 先过确认门
        args = tool_validate('world.dispatch', WORLD_DISPATCH['parameters'],
                             WORLD_DISPATCH['required'], decision['arguments'])
        receipt = world_dispatch_stub(**args)              # 经 stub handler 走完整链路
        trace.append('executed')
        return decision, args, receipt, trace
    return {'action': 'final', 'text': '（普通对话）'}, None, None, []


for t in POS_DISPATCH:
    decision, args, receipt, trace = mock_run(t)
    ok = (decision['action'] == 'tool_call' and decision['tool'] == 'world.dispatch'
          and args is not None and receipt is not None
          and receipt['id'] == receipt['commission_id']
          and receipt['world_visible'] == 'unknown'
          and trace[0] == 'waiting_confirmation' and trace[-1] == 'executed')
    check('B2', t, ok, f'trace={trace} receipt={receipt}')

# 缺省 brief：经 ADR 形状 stub handler 以 **arguments 方式只传 title，不得 TypeError
try:
    receipt = world_dispatch_stub(**{'title': '把便笺整理成三版标题'})
    ok = (receipt['id'] == receipt['commission_id']
          and receipt['world_visible'] == 'unknown')
    check('B2', '仅传 title（brief 缺省）经 stub handler', ok, f'receipt={receipt}')
except TypeError as exc:
    check('B2', '仅传 title（brief 缺省）经 stub handler', False, f'TypeError: {exc}')

# 反事实：无默认值签名同样只传 title，必须 TypeError——证明默认值是必要屏障
try:
    world_dispatch_no_default(**{'title': '把便笺整理成三版标题'})
    check('B2', '反事实：无默认值签名仅传 title', False, '未抛 TypeError（断言失败）')
except TypeError:
    check('B2', '反事实：无默认值签名仅传 title', True, '确认缺省必炸 → ADR §4.2 的 brief="" 必要')

print()
if failures:
    print(f'结果：{len(failures)}/{count} 条 FAIL -> {failures}')
    sys.exit(1)
print(f'结果：全部 PASS（{count} 条）。A 类=当前分流（正则级，已证）；'
      'B 类=扩词后进 AgentLoop（B1 构造级已证；B2 契约级 mock 已证，非真实 AgentLoop 运行——'
      '真实 AgentLoop 内的工具选中仍属 S5 实现期验收）。')

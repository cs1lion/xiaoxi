"""离线漏斗：只读扫描本地 messages，按确定性启发式提名评测候选（T3a）。

硬约束：
  - **纯 sqlite3**，以 `mode=ro` 只读打开 data/memory.db；绝不 import 任何
    server 模块——那会按 server/config.py 的 settings 打开**活库**并触发单例。
  - 无网络、无 Key、不写 evals/cases/；只把候选 JSON 写到 eval/research/。
  - 库不存在时打印说明并 exit 0（正常态，不是错误）。

messages 表结构抄自 server/memory/store.py:80-88；时间窗查询范式照
store.py:1090-1100 的 messages_between（created_at >= ? AND created_at < ?）。
四类启发式都是确定性正则，不调模型：
  1. coreference_or_ellipsis         共指或省略追问
  2. suspected_hallucination_surface 疑似幻觉面（助手断言“你之前说过…”）
  3. fact_challenge                  事实挑战（用户纠正/反驳）
  4. negative_emotion_boundary       负面情绪边界
输出前做凭据与个人信息正则脱敏（思路复用 scripts/eval_memory.py 的 _CREDENTIAL）。
只读打开先例：scripts/archive_durable.py:38、scripts/backup_local_databases.py:100。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

# Windows GBK 控制台曾让同类报告脚本崩过（run_gates 的 evolution_quality_report
# 注释记有此事）；强制 UTF-8 输出，宁可替换字符也不要崩——「库缺失必须 exit 0」
# 这条契约不能死在打印上。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老解释器没有 reconfigure
    pass

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'memory.db'
DEFAULT_OUTPUT_DIR = ROOT / 'eval' / 'research'
DEFAULT_DAYS = 30

# messages 表列（与 server/memory/store.py:80-88 一致；仅作文档与存在性自检）。
MESSAGES_COLUMNS = ('id', 'session_id', 'role', 'text', 'mode', 'created_at')

# 凭据脱敏：逐字复用 scripts/eval_memory.py:32-35 的 _CREDENTIAL。
_CREDENTIAL = re.compile(
    r'(?i)(?:\bBearer\s+\S+|\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+)|'
    r'''(?:api[_ -]?key|password|secret|authorization|密码|口令|密钥|令牌)["'\s]*[:=：]\s*\S+)'''
)
# 个人信息脱敏：邮箱 / 大陆手机号 / 18 位身份证 / 16-19 位银行卡。
_PII = re.compile(
    r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
    r'|(?<!\d)1[3-9]\d{9}(?!\d)'
    r'|(?<!\d)\d{17}[\dXx](?!\d)'
    r'|(?<!\d)\d{16,19}(?!\d)'
)

# 四类确定性启发式。field 决定匹配 user 还是 assistant 文本。
HEURISTICS = {
    'coreference_or_ellipsis': {
        'label': '共指或省略追问',
        'field': 'user',
        'suggested_layer': 'offline',
        'suggested_origin': 'retrieval_disambiguation',
        'pattern': re.compile(
            r'(刚才|刚刚|前面|之前|上一条|上一个|那条|那个|这个|它|他|她|这回事|那回事)'
            r'[^\n]{0,6}(呢|吗|如何|怎么样|怎样|好不好|行不行|对不对)'
            r'|(然后呢|后来呢|接着呢|继续说|刚说到哪|刚才那个|刚刚那个|前面那个)'
            r'|^(然后|后来|接着|那么|所以)[。！!？?，, ]*$'
        ),
    },
    'suspected_hallucination_surface': {
        'label': '疑似幻觉面',
        'field': 'assistant',
        'suggested_layer': 'live',
        'suggested_origin': 'hallucination_guard',
        'pattern': re.compile(
            r'(你(之前|曾经|以前|过往)(说|提|讲)|你(说过|提到过|提起过|告诉过我)|'
            r'我记得你|按你说的|根据你(之前|以前)|你上次(说|提到)|你自己说过)'
        ),
    },
    'fact_challenge': {
        'label': '事实挑战',
        'field': 'user',
        'suggested_layer': 'offline',
        'suggested_origin': 'correction_semantics',
        'pattern': re.compile(
            r'(不是|不对|错了|说错|记错|搞错|纠正|更正|实际上|才不是|并非|并没有|'
            r'你搞错|你说错|不是这样|你记反了|你弄错)'
        ),
    },
    'negative_emotion_boundary': {
        'label': '负面情绪边界',
        'field': 'user',
        'suggested_layer': 'live',
        'suggested_origin': 'emotion_boundary',
        'pattern': re.compile(
            r'(讨厌|烦死|崩溃|绝望|难受|痛苦|生气|愤怒|失望|委屈|焦虑|抑郁|不想活|'
            r'闭嘴|够了|受不了|心累|难过|伤心|气死|想哭|大哭)'
        ),
    },
}


def normalize(text):
    return ' '.join(str(text if text is not None else '').split())


def redact(text):
    """先脱凭据再脱个人信息；替换为带原因占位符，绝不回显原值。"""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ''
    return _PII.sub('[REDACTED:pii]', _CREDENTIAL.sub('[REDACTED:credential]', text))


def iso_utc(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def open_read_only(path):
    """只读打开（照 archive_durable.py:38 / backup_local_databases.py:100 先例）。"""
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)


def build_turns(rows):
    """把有序 messages 切成 (user, assistant|None) 轮。每个 user 都成轮，
    后面紧跟的同 session assistant 作为其回复；孤立 assistant 忽略。"""
    turns = []
    pending = None
    for row in rows:
        role = row['role']
        if role == 'user':
            if pending is not None:
                turns.append((pending, None))
            pending = row
        elif role == 'assistant':
            if pending is not None and pending['session_id'] == row['session_id']:
                turns.append((pending, row))
                pending = None
    if pending is not None:
        turns.append((pending, None))
    return turns


def classify(user_text, assistant_text):
    hits = []
    for key, spec in HEURISTICS.items():
        source = user_text if spec['field'] == 'user' else (assistant_text or '')
        if source and spec['pattern'].search(source):
            hits.append(key)
    return hits


def candidates_from_turns(turns):
    seen = set()
    out = []
    for user_row, assistant_row in turns:
        user_text = normalize(user_row['text'])
        if not user_text:
            continue
        assistant_text = normalize(assistant_row['text']) if assistant_row is not None else ''
        red_user = redact(user_text)
        red_assistant = redact(assistant_text)
        for key in classify(user_text, assistant_text):
            # R59：去重键按用户场景 (key, red_user)，**不把 assistant 回复纳入**。
            # 曾经的 (key, red_user, red_assistant) 让「同一用户措辞配 N 种 bot 回复」
            # 算作 N 个不同候选——R58 真实数据上「别调侃了，我难受。」因此重复 14 次，
            # 18 个候选里 distinct 用户措辞仅 3 种，候选列表被噪声淹没。
            # 评测用例以**用户场景**为粒度（一个 user 措辞一条用例），assistant 回复
            # 只是证据；此处保留首个见到的回复作样本即可，频次不单列字段（避免破坏
            # 下游对候选字段集的契约）。assistant-field 启发式
            # （suspected_hallucination_surface）同理按用户场景归一：同一问题的多条
            # 可疑回复塌成一条用例——去噪，不丢覆盖。
            signature = (key, red_user)
            if signature in seen:
                continue
            seen.add(signature)
            spec = HEURISTICS[key]
            out.append({
                'heuristic': key,
                'session_id': user_row['session_id'],
                'created_at': iso_utc(user_row['created_at']),
                'user_text': red_user,
                'assistant_text': red_assistant,
                'suggested_layer': spec['suggested_layer'],
                'suggested_origin': spec['suggested_origin'],
            })
    return out


def mine(db_path, *, since, until, limit=None):
    path = Path(db_path)
    report = {
        'database': str(path),
        'mode': 'ro',
        'window': {'since': since, 'until': until},
        'counts': {key: 0 for key in HEURISTICS},
        'candidates': [],
    }
    if not path.is_file():
        report['error'] = 'database_missing'
        return report
    try:
        connection = open_read_only(path)
    except sqlite3.Error as exc:
        report['error'] = f'open_failed:{type(exc).__name__}'
        return report
    try:
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()
        if table is None:
            report['error'] = 'messages_table_missing'
            return report
        rows = connection.execute(
            """SELECT id, session_id, role, text, mode, created_at FROM messages
               WHERE created_at >= ? AND created_at < ? ORDER BY id ASC""",
            (since, until),
        ).fetchall()
    except sqlite3.Error as exc:
        report['error'] = f'query_failed:{type(exc).__name__}'
        return report
    finally:
        connection.close()
    candidates = candidates_from_turns(build_turns(rows))
    if limit is not None:
        candidates = candidates[:max(0, int(limit))]
    for candidate in candidates:
        report['counts'][candidate['heuristic']] += 1
    report['candidates'] = candidates
    return report


def _parse_epoch(value):
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if not math.isfinite(number):
            raise argparse.ArgumentTypeError(f'时间越界 {value!r}')
        return number
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise argparse.ArgumentTypeError(f'无法解析时间 {value!r}（用 epoch 秒或 ISO-8601）')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def conversion_stats(research_dir: Path, cases_dir: Path) -> dict:
    """漏斗转化率：累计提名 vs 已转成 evals/cases 用例。

    为什么需要：漏斗每跑只报「本次提名 N 条」，至于提名过多少、其中多少真的
    写成了 evals/cases/*.yaml，全无统计——R15 完备性审查称之为「漏斗有、
    转化率不可见」。没有这个数，就无法判断漏斗是在产出价值还是在空转。

    口径（必须随数字一起读，否则会被误读）：
    - **累计提名**按 user_text 去重跨全部历史候选文件——同一条日志会被多次
      提名，不去重会把重复计成多次转化机会；
    - **已转化**按 user_text 与用例 turns 首轮**精确匹配**。所以报出来的是
      **下限**：用例一旦改写措辞（人工改写是常态），就匹配不上、被算作未转化。
      真实转化率只会比这个数高，不会低。

    只读，不写任何地方。research_dir 不存在或没有候选文件时返回空统计（不崩）。
    """
    nominated: dict[str, set] = defaultdict(set)
    for path in sorted(research_dir.glob('case_candidates_*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue  # 半截写入的候选文件不该让统计崩
        for item in data.get('candidates') or []:
            text = normalize(str(item.get('user_text') or ''))
            if text:
                nominated[str(item.get('heuristic') or '?')].add(text)

    case_turns: set[str] = set()
    if cases_dir.is_dir():
        for path in sorted(cases_dir.glob('*.yaml')):
            try:
                data = yaml.safe_load(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            turns = data.get('turns') or []
            if turns and isinstance(turns[0], str):
                case_turns.add(normalize(turns[0]))

    by_heuristic = {}
    total_n = converted_n = 0
    for heuristic in sorted(nominated):
        texts = nominated[heuristic]
        hit = sum(1 for t in texts if t in case_turns)
        by_heuristic[heuristic] = {"nominated": len(texts), "converted": hit}
        total_n += len(texts)
        converted_n += hit
    return {
        "nominated_total": total_n,
        "converted_total": converted_n,
        "rate": (converted_n / total_n) if total_n else None,
        "by_heuristic": by_heuristic,
        "basis": "user_text 精确匹配用例 turns 首轮；用例改写措辞后即匹配不上，"
                 "故本数是转化率的下限，真实值只会更高",
    }


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--db', default=str(DEFAULT_DB))
    result.add_argument('--days', type=int, default=DEFAULT_DAYS, help='回溯天数（默认 30）')
    result.add_argument('--since', default=None, help='覆盖起始（epoch 秒或 ISO-8601）')
    result.add_argument('--until', default=None, help='覆盖结束（epoch 秒或 ISO-8601）')
    result.add_argument('--output', default=None)
    result.add_argument('--limit', type=int, default=None)
    result.add_argument('--no-conversion', action='store_true',
                        help='不输出漏斗转化率统计（默认输出）')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    path = Path(args.db)
    if not path.is_file():
        print(json.dumps({'ok': True, 'skipped': 'database_missing', 'database': str(path),
                          'detail': '本地记忆库不存在，未提名任何候选（正常态，非错误）。'},
                         ensure_ascii=False))
        return 0
    now = time.time()
    until = _parse_epoch(args.until) if args.until else now
    since = _parse_epoch(args.since) if args.since else now - max(0, args.days) * 86400
    report = mine(path, since=since, until=until, limit=args.limit)
    report['generated_at'] = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(args.output) if args.output else (
        DEFAULT_OUTPUT_DIR / f'case_candidates_{stamp}_{uuid4().hex[:8]}.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                      encoding='utf-8')
    summary = {'ok': True, 'candidates': len(report['candidates']),
               'counts': report['counts'], 'output': str(output)}
    if report.get('error'):
        summary['error'] = report['error']
    # 漏斗转化率（R35）：不接进输出就只是死代码。默认开，--no-conversion 可关。
    if not args.no_conversion:
        summary['conversion'] = conversion_stats(DEFAULT_OUTPUT_DIR,
                                                 ROOT / 'evals' / 'cases')
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

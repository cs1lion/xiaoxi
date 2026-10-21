/** 台账帧契约测试（S1-9）：用真实响应录下的 fixture 直接喂给 parse / project。
 *
 * fixture 来源：2026-10-19 13:05 从本机 8000 `GET /api/executive/commissions?limit=100`
 * 只读录制（见 fixtures/commissions.json 的 captured_at 与 note）。录制时台账里只有
 * done；blocked / running 两条只把状态相关字段按 PROTOCOL.md §19 状态机派生，
 * 字段集合与真实 items 完全一致。字段改名/删除会让本文件失败。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { project, parseCommissionRow } from '../src/world/projection.ts';
import { LedgerClient, type LedgerView } from '../src/world/ledger.ts';
import type { AgentRow, CommissionRow } from '../src/world/projection.ts';

interface CommissionFixture {
  readonly captured_at: string;
  readonly source: string;
  readonly note: string;
  readonly items: readonly CommissionRow[];
}

const fixture = JSON.parse(
  readFileSync(new URL('./fixtures/commissions.json', import.meta.url), 'utf8'),
) as CommissionFixture;
const pushFrame = JSON.parse(
  readFileSync(new URL('./fixtures/commission_update.json', import.meta.url), 'utf8'),
) as { readonly type: string; readonly commission: CommissionRow };

/** 花名册能力关键词（契约字段来自 config/agents_roster.yaml:48-52、:90-102）。 */
const AGENTS: AgentRow[] = [
  {
    id: 'scribe',
    name: '笔录官',
    strengths: ['写作', '整理', '标题', '摘要', '便笺', '笔记', '文案', '改写', '润色'],
  },
  {
    id: 'claude',
    name: '真机 Claude',
    strengths: ['实现', '代码', '文件', '写入', '修改', '脚本', '测试', '重构', '命令', '修复'],
  },
];

/** PROTOCOL.md §19「一条 Commission 的形状」。 */
const CONTRACT_FIELDS = [
  'id', 'thread_id', 'title', 'brief', 'agent_id', 'status', 'step_idx', 'retry_count',
  'blocked_question', 'delivery', 'audit', 'rubric_ids', 'created_at', 'updated_at', 'closed_at',
];

test('fixture 字段集合锁定 PROTOCOL §19 契约', () => {
  assert.equal(fixture.items.length, 3);
  for (const item of fixture.items) {
    // packet 仅在运行时由 _project 解析出来，真实响应里也有；这里按实际录制保留。
    const expected = [...CONTRACT_FIELDS, 'packet'].sort();
    assert.deepEqual(Object.keys(item).sort(), expected);
  }
});

test('fixture 覆盖 blocked / running / done 三种状态，逐条可解析', () => {
  const statuses = fixture.items.map((item) => item.status).sort();
  assert.deepEqual(statuses, ['blocked', 'done', 'running']);
  for (const item of fixture.items) {
    const parsed = parseCommissionRow(item);
    assert.equal(parsed.id, item.id);
    assert.equal(parsed.title, item.title);
    assert.equal(parsed.agent_id, item.agent_id);
    assert.equal(parsed.status, item.status);
    assert.equal(parsed.step_idx, item.step_idx);
    assert.equal(parsed.blocked_question, item.blocked_question);
  }
});

test('fixture 的 closed_at/updated_at 是真实 epoch 秒，completedAt 按 ADR 取 closed_at ?? updated_at', () => {
  type RawTimes = { readonly closed_at?: number | null; readonly updated_at?: number | null };
  const done = fixture.items.find((item) => item.status === 'done');
  const blocked = fixture.items.find((item) => item.status === 'blocked');
  assert.ok(done !== undefined && blocked !== undefined);
  const doneRaw = done as unknown as RawTimes;
  const blockedRaw = blocked as unknown as RawTimes;

  for (const raw of [doneRaw, blockedRaw]) {
    const stamp = raw.closed_at ?? raw.updated_at ?? 0;
    assert.ok(stamp > 1e9 && stamp < 1e12, `fixture 时间戳必须是 epoch 秒，实际 ${stamp}`);
  }
  assert.equal(parseCommissionRow(done).completedAt, doneRaw.closed_at, 'done 用 closed_at');
  assert.equal(blockedRaw.closed_at, null, '派生行 closed_at 为 null');
  assert.equal(parseCommissionRow(blocked).completedAt, blockedRaw.updated_at, 'blocked 退 updated_at');
});

test('fixture 经 project 映射：blocked 进 active，done 进 finished', () => {
  // 生产链路是先 parseCommissionRow 再 project；fixture 是原始响应，必须先解析。
  const rows = fixture.items.map((item) => parseCommissionRow(item));
  const result = project({ commissions: rows, agents: AGENTS });
  const blocked = rows.find((item) => item.status === 'blocked');
  const running = rows.find((item) => item.status === 'running');
  const finished = rows.find((item) => item.status === 'done');
  assert.ok(blocked !== undefined && running !== undefined && finished !== undefined);

  assert.equal(result.active?.commissionId, blocked.id, 'blocked 要请示，优先级最高');
  assert.deepEqual(
    result.tasks.map((task) => task.commissionId).sort(),
    [blocked.id, running.id].sort(),
  );
  assert.deepEqual(result.finished.map((task) => task.commissionId), [finished.id]);

  const blockedTask = result.tasks.find((task) => task.commissionId === blocked.id);
  assert.equal(blockedTask?.agentName, '笔录官');
  assert.equal(blockedTask?.kind, 'writing');
  assert.equal(blockedTask?.title, '把便笺整理成三版标题');
  assert.equal(blockedTask?.blockedQuestion, '先按短句还是完整句式？');
  assert.equal(blockedTask?.completedAt, blocked.completedAt, 'blocked 用 updated_at');
  assert.equal(blockedTask?.finished, false);

  const runningTask = result.tasks.find((task) => task.commissionId === running.id);
  assert.equal(runningTask?.agentName, '真机 Claude');
  assert.equal(runningTask?.kind, 'code');

  const finishedTask = result.finished[0];
  assert.equal(finishedTask?.kind, 'writing', '笔录官的能力关键词落到写作');
  assert.equal(finishedTask?.finished, true);
  assert.equal(finishedTask?.completedAt, finished.completedAt, 'done 用 closed_at');
});

test('没有花名册时按真实 API 现状回退：agentName=agent_id，kind 靠标题关键词', () => {
  // /api/executive/adapters 不返回 id/strengths（D4），这里锁住回退行为。
  const result = project({ commissions: fixture.items, agents: [] });
  const blocked = result.tasks.find((task) => task.commissionId === 'c_fixture_blocked');
  assert.equal(blocked?.agentName, 'scribe');
  assert.equal(blocked?.kind, 'writing', '标题含「整理」');
});

test('commission_update 帧与 REST item 同形，走同一套解析', () => {
  assert.equal(pushFrame.type, 'commission_update');
  const restItem = fixture.items.find((item) => item.id === pushFrame.commission.id);
  assert.ok(restItem !== undefined, '帧里的 commission 必须能在 REST items 里找到同 id 的一条');
  assert.deepEqual(
    Object.keys(pushFrame.commission).sort(),
    Object.keys(restItem).sort(),
    '帧与 REST 字段集合必须一致（协议 §19：不另立一套字段）',
  );
  assert.deepEqual(parseCommissionRow(pushFrame.commission), parseCommissionRow(restItem));
});

test('WS 帧经 LedgerClient.applyPush 进入世界视图', () => {
  const views: LedgerView[] = [];
  const client = new LedgerClient({
    onChange: (view) => views.push(view),
    location: { protocol: 'http:', host: '127.0.0.1:5188' },
  });
  client.applyPush(pushFrame.commission);
  const last = views.at(-1);
  assert.equal(last?.connected, true);
  assert.deepEqual(last?.tasks.map((task) => task.commissionId), ['c_fixture_running']);
  client.stop();
});

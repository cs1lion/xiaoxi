import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  REJECT_FEEDBACK_MIN,
  createCommandClient,
  parseBlockers,
  parseReview,
} from '../src/world/commands.ts';

/** 假 fetch：按调用顺序回放预设响应，并把每次请求记下来供断言。 */
function fakeFetch(responses: { status?: number; body?: unknown; throws?: Error }[]) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (next === undefined) throw new Error('假 fetch 没有更多响应了');
    if (next.throws !== undefined) throw next.throws;
    const status = next.status ?? 200;
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => next.body,
    } as unknown as Response;
  }) as unknown as typeof fetch;
  return { impl, calls };
}

const okReview = {
  commission_id: 'c1',
  status: 'ready_to_report',
  reviewable: true,
  passed: true,
  layer: 'l2',
  blockers: [],
  findings: [
    { rule_id: 'r1', severity: 'minor', detail: '第二段样本较小', layer: 'l2', rule: '数据要标样本量' },
  ],
  recommendation: 'accept',
  can_accept: true,
  can_reject: true,
  reason: '',
  delivery_summary: '已核对 3 家竞品',
  artifacts_verified: true,
  artifact_unverified_count: 0,
};

test('reading a review turns the server shape into the drawer shape', async () => {
  const { impl, calls } = fakeFetch([{ body: okReview }]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.review('c1');
  assert.equal(result.ok, true);
  if (!result.ok || !('review' in result)) return assert.fail('应当拿到 review');
  assert.equal(result.review.canAccept, true);
  assert.equal(result.review.canReject, true);
  assert.equal(result.review.recommendation, 'accept');
  assert.equal(result.review.deliverySummary, '已核对 3 家竞品');
  assert.equal(result.review.findings.length, 1);
  assert.equal(result.review.findings[0]?.rule, '数据要标样本量');
  assert.equal(calls[0]?.url, '/api/executive/commissions/c1/review');
  assert.equal(calls[0]?.init?.method, 'GET');
});

test('a commission that is not ready yet says so instead of reporting a fault', async () => {
  const { impl } = fakeFetch([{ status: 409, body: { detail: 'commission_not_ready_to_report' } }]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.review('c1');
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.message, /还没到验收环节/);
});

test('accepting sends the boss decision and reads the row back', async () => {
  const { impl, calls } = fakeFetch([{ body: { id: 'c1', status: 'done' } }]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.accept('c1');
  assert.equal(result.ok, true);
  if (!result.ok || !('settled' in result)) return assert.fail('应当拿到 settled');
  assert.equal(result.settled.status, 'done');
  assert.equal(calls[0]?.init?.method, 'POST');
});

test('a refused accept carries the blockers back to the drawer', async () => {
  const { impl } = fakeFetch([
    {
      status: 409,
      body: {
        code: 'accept_not_allowed',
        blockers: [{ rule_id: 'r9', severity: 'blocker', detail: '成果文件不见了', layer: 'l1', rule: '' }],
      },
    },
  ]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.accept('c1');
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.message, /accept_not_allowed/);
  assert.equal(result.blockers.length, 1);
  assert.equal(result.blockers[0]?.detail, '成果文件不见了');
});

test('rejecting too briefly never reaches the server', async () => {
  const { impl, calls } = fakeFetch([]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.reject('c1', '太'.repeat(REJECT_FEEDBACK_MIN - 1));
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.message, new RegExp(`${REJECT_FEEDBACK_MIN}`));
  assert.equal(calls.length, 0, '短批注不该发出请求');
});

test('a reject brings back the rule the server learned from it', async () => {
  const { impl } = fakeFetch([
    {
      body: {
        commission: { id: 'c1', status: 'running' },
        rubric: { id: 'r_new', rule: '价格统一换算成人民币' },
        boss_reject_count: 2,
      },
    },
  ]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.reject('c1', '价格请统一换算成人民币');
  assert.equal(result.ok, true);
  if (!result.ok || !('reject' in result)) return assert.fail('应当拿到 reject');
  assert.equal(result.reject.status, 'running');
  assert.equal(result.reject.rubric?.text, '价格统一换算成人民币');
  assert.equal(result.reject.bossRejectCount, 2);
});

test('a reject without a learned rule reports null rather than inventing one', async () => {
  const { impl } = fakeFetch([{ body: { commission: { status: 'running' }, rubric: null } }]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.reject('c1', '这里写得不对，重来一遍');
  assert.equal(result.ok, true);
  if (!result.ok || !('reject' in result)) return assert.fail('应当拿到 reject');
  assert.equal(result.reject.rubric, null);
});

test('answering a blocked commission moves it on and keeps the reply', async () => {
  const { impl, calls } = fetchWithAnswer();
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.answer('c1', '放行');
  assert.equal(result.ok, true);
  if (!result.ok || !('settled' in result)) return assert.fail('应当拿到 settled');
  assert.equal(result.settled.status, 'running');
  assert.equal(result.settled.note, '已放行，继续。');
  const body = JSON.parse(String(calls[0]?.init?.body));
  assert.equal(body.answer, '放行');
});

function fetchWithAnswer() {
  return fakeFetch([
    { body: { commission: { status: 'running' }, reply: '已放行，继续。', auto_answered: false } },
  ]);
}

test('a network failure names the reason instead of going silent', async () => {
  const { impl } = fakeFetch([{ throws: new TypeError('fetch failed') }]);
  const client = createCommandClient({ fetchImpl: impl });
  const result = await client.answer('c1', '放行');
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.message, /连不上服务端/);
});

test('a timeout is reported as a timeout, not as a generic failure', async () => {
  const slow = (async (_url: string, init?: RequestInit) => {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, 5_000);
      init?.signal?.addEventListener('abort', () => {
        clearTimeout(timer);
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      });
    });
    return { ok: true, status: 200, json: async () => ({}) } as unknown as Response;
  }) as unknown as typeof fetch;
  const client = createCommandClient({ fetchImpl: slow, timeoutMs: 10 });
  const result = await client.review('c1');
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.match(result.message, /请求超时/);
});

test('unreadable review shapes are refused rather than half-trusted', () => {
  assert.equal(parseReview(null), null);
  assert.equal(parseReview('nope'), null);
  // reviewable:false 是 200 但不是可用的初审。
  assert.equal(parseReview({ ...okReview, reviewable: false }), null);
});

test('blocker parsing accepts both the object shape and plain strings', () => {
  const parsed = parseBlockers([
    { rule_id: 'r1', severity: 'blocker', detail: '缺文件', layer: 'l1', rule: '' },
    'artifact_missing',
    { nonsense: true },
  ]);
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0]?.severity, 'blocker');
  assert.equal(parsed[1]?.severity, 'blocker');
  assert.equal(parsed[1]?.detail, 'artifact_missing');
});

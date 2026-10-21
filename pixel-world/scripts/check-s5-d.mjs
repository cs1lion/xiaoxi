#!/usr/bin/env node
/**
 * S5 D 腿（完成/交付）验收检查器。
 *
 * 它只读 e2e 证据 JSON，不跑模型、不连服务；真正的 live 复跑需要 LLM_API_KEY 和隔离网关。
 * 目的是把“D 到底算不算过”从口头判断变成可复跑的 pass/fail，避免把 blocked 当交付。
 *
 * 证据形状来自 pixel-world/eval/screens/s5-e2e-drive-r10.mjs：
 *   { terminal, approved, timeline, verdict }
 * 其中 terminal.audit.usage 必须是 c9ef30a 之后的 per-call / total 双字段。
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

export const PER_CALL_LIMIT = 120_000;
export const TOTAL_LIMIT = 400_000;

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

/**
 * @param {any} evidence
 * @returns {{name: string, ok: boolean, detail: string}[]}
 */
export function checkEvidence(evidence) {
  const checks = [];
  const add = (name, ok, detail = '') => checks.push({ name, ok, detail });
  const terminal = evidence?.terminal ?? {};
  const verdict = evidence?.verdict ?? {};
  const approved = evidence?.approved ?? verdict.approved ?? null;
  const world = verdict.worldFinal ?? evidence?.worldFinal ?? {};
  const usage = terminal.audit?.usage ?? verdict.auditUsage ?? {};
  const findings = asArray(terminal.audit?.findings);
  const timeline = asArray(evidence?.timeline);

  const commissionId = String(verdict.commissionId ?? terminal.id ?? '');
  add('有 commission_id', commissionId.length > 0, commissionId || '(empty)');

  const finalStatus = String(approved?.status ?? verdict.terminalStatus ?? terminal.status ?? '');
  add(
    '终态为 done / ready_to_report',
    finalStatus === 'done' || finalStatus === 'ready_to_report',
    finalStatus || '(empty)',
  );
  if (finalStatus === 'ready_to_report') {
    add(
      'ready_to_report 后老板已批准',
      String(approved?.status ?? '') === 'done',
      String(approved?.status ?? '(missing)'),
    );
  }

  add(
    '世界侧 ledger 交付 ≥ 1',
    Number(world.ledgerDeliveries ?? 0) >= 1,
    `ledgerDeliveries=${world.ledgerDeliveries ?? '(missing)'}`,
  );

  const modes = [
    ...asArray(world.modes),
    ...timeline.flatMap((entry) => asArray(entry?.modes)),
  ].map(String);
  // 判据是 AND，不是 OR：这条要证明的是「她干完活真的走到祭坛交付」，
  // 两个动作缺一个都不完整。旧版用 OR，于是 toAltar 从未出现也照样绿——
  // 缺口被自己的判据掩盖掉了（issue #9）。
  const sawDelivering = modes.some((mode) => mode.includes(':delivering'));
  const sawToAltar = modes.some((mode) => mode.includes(':toAltar'));
  add(
    '世界出现 delivering',
    sawDelivering,
    sawDelivering ? 'seen in timeline' : 'not seen',
  );
  add(
    '世界出现 toAltar',
    sawToAltar,
    sawToAltar ? 'seen in timeline' : 'not seen（采集窗口可能在 delivering 后立即结束）',
  );

  // issue #10：S5 合同要求「真实 WS，禁止注入帧」，但旧版检查器完全不看台账连接态，
  // 于是「这单是真实台账下行」只能靠 source=ledger + URL 推断。这里要求证据自己留痕。
  // 兼容两处采集点：verdict.ledger（末态）与 timeline 每帧的 ledger 字段。
  const ledgerFinal = verdict.ledger ?? evidence?.ledger ?? null;
  const ledgerSamples = [
    ...(ledgerFinal ? [ledgerFinal] : []),
    ...timeline.map((entry) => entry?.ledger).filter(Boolean),
  ];
  if (ledgerSamples.length === 0) {
    add('台账连接态有留证', false, '证据里没有 ledger.connected / mock 字段（见 issue #10）');
  } else {
    const wasConnected = ledgerSamples.some((sample) => sample.connected === true);
    add('台账连接态有留证', true, `sampled=${ledgerSamples.length}`);
    add('台账曾连上（connected=true）', wasConnected, wasConnected ? 'yes' : 'never connected');
    // mock 只允许出现在中途断线；末态若仍是 mock，说明这单不是真台账跑出来的。
    const finalMock = ledgerFinal ? ledgerFinal.mock === true : null;
    add(
      '末态不是 Mock（mock=false）',
      finalMock === false,
      finalMock === null ? '末态无 ledger 记录（无法判定）' : `mock=${finalMock}`,
    );
  }

  const calls = Number(usage.calls ?? 0);
  const perCall = Number(
    usage.max_prompt_tokens_per_call ?? (calls === 1 ? usage.prompt_tokens : NaN),
  );
  add(
    `单次调用 prompt ≤ ${PER_CALL_LIMIT}`,
    Number.isFinite(perCall) && perCall > 0 && perCall <= PER_CALL_LIMIT,
    Number.isFinite(perCall) ? `max_prompt_tokens_per_call=${perCall}` : 'missing per-call usage',
  );

  const bloat = findings.some((finding) => finding?.rule_id === 'prompt_token_bloat');
  add(
    '无 prompt_token_bloat 打回',
    !bloat,
    bloat ? 'audit still returned prompt_token_bloat' : 'no token-bloat finding',
  );

  const total = Number(usage.prompt_tokens ?? 0);
  if (Number.isFinite(total) && total > TOTAL_LIMIT) {
    checks.push({
      name: '整单成本告警（不阻断交付）',
      ok: true,
      detail: `prompt_tokens=${total} > ${TOTAL_LIMIT}`,
    });
  }

  return checks;
}

function report(checks) {
  let failed = 0;
  for (const check of checks) {
    console.log(`  ${check.ok ? 'ok  ' : 'FAIL'} ${check.name}${check.detail ? `  (${check.detail})` : ''}`);
    if (!check.ok) failed += 1;
  }
  console.log(`\n=== ${checks.length - failed}/${checks.length} passed, ${failed} failed ===`);
  return failed === 0;
}

function selfTest() {
  const good = {
    terminal: {
      id: 'c_good',
      status: 'done',
      audit: {
        usage: { calls: 1, prompt_tokens: 110_000, max_prompt_tokens_per_call: 110_000 },
        findings: [],
      },
    },
    approved: { status: 'done' },
    timeline: [{ modes: ['v0:working', 'v0:delivering'], ledger: { connected: true, mock: false } }],
    verdict: {
      commissionId: 'c_good', terminalStatus: 'done',
      worldFinal: { ledgerDeliveries: 1, modes: ['v0:toAltar'] },
      ledger: { connected: true, mock: false },
    },
  };
  // 交付动作缺一半的证据：有 delivering、没有 toAltar，且台账连接态没留痕。
  // 旧版 OR 判据 + 不查 ledger 会让它通过——这正是 issue #9 / #10 的形态。
  const halfDelivery = {
    terminal: {
      id: 'c_half', status: 'done',
      audit: { usage: { calls: 1, prompt_tokens: 90_000, max_prompt_tokens_per_call: 90_000 }, findings: [] },
    },
    approved: { status: 'done' },
    timeline: [{ modes: ['v0:working', 'v0:delivering'] }],
    verdict: { commissionId: 'c_half', terminalStatus: 'done', worldFinal: { ledgerDeliveries: 1, modes: ['v0:idle'] } },
  };
  const bad = {
    terminal: {
      id: 'c_bad',
      status: 'blocked',
      blocked_question: 'Prompt Tokens 421413 超出上限 120000',
      audit: {
        usage: { calls: 3, prompt_tokens: 1_017_073, max_prompt_tokens_per_call: 421_413 },
        findings: [{ rule_id: 'prompt_token_bloat', severity: 'major' }],
      },
    },
    approved: null,
    timeline: [{ modes: ['v0:working'] }],
    verdict: { commissionId: 'c_bad', terminalStatus: 'blocked', worldFinal: { ledgerDeliveries: 0, modes: ['v0:working'] } },
  };
  console.log('self-test good evidence:');
  const goodPass = report(checkEvidence(good));
  console.log('\nself-test blocked evidence (must fail):');
  const badChecks = checkEvidence(bad);
  const badPass = report(badChecks);
  const badHasFailure = badChecks.some((check) => !check.ok);
  console.log('\nself-test half-delivery evidence (must fail: no toAltar, no ledger trace):');
  const halfChecks = checkEvidence(halfDelivery);
  const halfPass = report(halfChecks);
  const halfHasFailure = halfChecks.some((check) => !check.ok);
  if (!goodPass || badPass || !badHasFailure || halfPass || !halfHasFailure) {
    console.error('\nself-test failed: checker does not separate good, blocked and half-delivery evidence');
    process.exit(1);
  }
  console.log('\nself-test passed');
}

const arg = process.argv[2];
if (arg === '--self-test') {
  selfTest();
} else {
  const file = arg ?? fileURLToPath(new URL('../eval/screens/s5-e2e-evidence-d3.json', import.meta.url));
  let data;
  try {
    data = JSON.parse(readFileSync(file, 'utf8'));
  } catch (error) {
    console.error(`读不到 D 证据：${file}\n${error instanceof Error ? error.message : error}`);
    process.exit(2);
  }
  console.log(`D 证据：${file}`);
  const passed = report(checkEvidence(data));
  process.exit(passed ? 0 : 1);
}

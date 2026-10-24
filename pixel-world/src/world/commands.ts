/** 批复动作客户端：把老板在抽屉里的决定送进台账。
 *
 * 只做运输与形状校验，不做判断：
 *   - 不知道"这一单该不该准奏"——那是服务端初审的事；
 *   - 不重试——重试会让「点了一次」变成「批了两次」；
 *   - 不猜错误——服务端的 409 带着 blockers，原样交回去给抽屉展示。
 *
 * 世界仍然不写台账库。它调的是服务端公开的老板动作端点，写库的永远是服务端
 * 那一侧。这条边界与「世界只读投影」不冲突：读的是库，写的是 API。
 *
 * 端点形状（server/assistant/executive_api.py）：
 *   GET  /api/executive/commissions/{id}/review   只读初审
 *   POST /api/executive/commissions/{id}/accept
 *   POST /api/executive/commissions/{id}/reject   feedback 至少 4 字
 *   POST /api/executive/commissions/{id}/answer
 */

/** 打回批注的下限，与服务端 `feedback_too_short_to_learn_from` 同一条线。 */
export const REJECT_FEEDBACK_MIN = 4;

const REQUEST_TIMEOUT_MS = 10_000;
const BASE = '/api/executive';

/** 初审里的一条问题。`blocker` 严重度的那几条会挡住准奏。 */
export interface ReviewBlocker {
  readonly ruleId: string;
  readonly severity: string;
  readonly detail: string;
  readonly layer: string;
  /** 命中的那条准则原文。空串表示这条不是撞准则，是硬性问题。 */
  readonly rule: string;
}

export interface Review {
  readonly commissionId: string;
  readonly canAccept: boolean;
  readonly canReject: boolean;
  /** `accept` / `reject`，服务端给的推荐。世界只展示，不替它决定。 */
  readonly recommendation: string;
  readonly passed: boolean;
  readonly blockers: readonly ReviewBlocker[];
  readonly findings: readonly ReviewBlocker[];
  readonly deliverySummary: string;
  /** 初审给了什么批评。空串表示没话说。 */
  readonly reason: string;
  readonly artifactsVerified: boolean;
  readonly artifactUnverifiedCount: number;
}

export interface RejectResult {
  readonly commissionId: string;
  readonly status: string;
  /** 服务端从这次打回里归纳出的准则。没有就 null，不编。 */
  readonly rubric: { readonly id: string; readonly text: string } | null;
  readonly bossRejectCount: number;
}

/** 一次动作之后台账说这一单现在是什么状态。 */
export interface Settled {
  readonly status: string;
  /** 服务端给的下一句提示（answer 会带 reply）。没有就是空串。 */
  readonly note: string;
}

/** 一次动作的结果。失败带着话说清为什么，绝不留空。 */
export type CommandResult =
  | { readonly ok: true; readonly review: Review }
  | { readonly ok: true; readonly reject: RejectResult }
  | { readonly ok: true; readonly settled: Settled }
  | { readonly ok: false; readonly message: string; readonly blockers: readonly ReviewBlocker[] };

export interface CommandClient {
  readonly review: (commissionId: string) => Promise<CommandResult>;
  readonly accept: (commissionId: string) => Promise<CommandResult>;
  readonly reject: (commissionId: string, feedback: string) => Promise<CommandResult>;
  readonly answer: (commissionId: string, text: string) => Promise<CommandResult>;
}

type FetchLike = typeof fetch;

interface RawEnvelope {
  readonly ok: boolean;
  readonly status: number;
  readonly body: unknown;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function readString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === 'string' ? value : '';
}

/**
 * 初审的问题条目。服务端给的是 `{rule_id, severity, detail, layer, rule}`；
 * 纯字符串也认（错误响应里的 blockers 就是这个形状）。认不出的一律跳过，不猜。
 */
export function parseBlockers(raw: unknown): ReviewBlocker[] {
  if (!Array.isArray(raw)) return [];
  const out: ReviewBlocker[] = [];
  for (const item of raw) {
    if (typeof item === 'string') {
      out.push({ ruleId: '', severity: 'blocker', detail: item, layer: '', rule: '' });
      continue;
    }
    const record = asRecord(item);
    if (record === null) continue;
    const code = readString(record, 'code');
    const detail = readString(record, 'detail');
    const ruleId = readString(record, 'rule_id') || code;
    // 一条既没 id 也没正文的条目是垃圾数据，收进来只会让抽屉显示一张空卡。
    if (ruleId === '' && detail === '') continue;
    out.push({
      ruleId,
      severity: readString(record, 'severity') || (code !== '' ? 'blocker' : ''),
      detail,
      layer: readString(record, 'layer'),
      rule: readString(record, 'rule'),
    });
  }
  return out;
}

export function parseReview(raw: unknown): Review | null {
  const body = asRecord(raw);
  if (body === null) return null;
  // `reviewable === false` 时服务端仍回 200，但这不是一份可用的初审。
  if (body.reviewable === false) return null;
  return {
    commissionId: readString(body, 'commission_id'),
    canAccept: body.can_accept === true,
    canReject: body.can_reject === true,
    recommendation: readString(body, 'recommendation'),
    passed: body.passed === true,
    blockers: parseBlockers(body.blockers),
    findings: parseBlockers(body.findings),
    deliverySummary: readString(body, 'delivery_summary'),
    reason: readString(body, 'reason'),
    artifactsVerified: body.artifacts_verified === true,
    artifactUnverifiedCount:
      typeof body.artifact_unverified_count === 'number' ? body.artifact_unverified_count : 0,
  };
}

/** 打回结果。rubric 的正文在服务端叫 `rule`，不是 `text`——曾按 `text` 写过，取到空串。 */
export function parseRejectResult(raw: unknown, commissionId: string): RejectResult | null {
  const body = asRecord(raw);
  if (body === null) return null;
  const commission = asRecord(body.commission);
  const rubricRecord = asRecord(body.rubric);
  const rubricText = rubricRecord === null ? '' : readString(rubricRecord, 'rule');
  return {
    commissionId,
    status: commission === null ? '' : readString(commission, 'status'),
    rubric:
      rubricRecord === null || rubricText === ''
        ? null
        : { id: readString(rubricRecord, 'id'), text: rubricText },
    bossRejectCount: typeof body.boss_reject_count === 'number' ? body.boss_reject_count : 0,
  };
}

export function createCommandClient(
  options: {
    readonly fetchImpl?: FetchLike;
    readonly timeoutMs?: number;
  } = {},
): CommandClient {
  const fetchImpl = options.fetchImpl ?? ((...args: Parameters<FetchLike>) => fetch(...args));
  const timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS;

  const send = async (path: string, method: string, body?: unknown): Promise<RawEnvelope> => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(`${BASE}/${path}`, {
        method,
        headers:
          body === undefined
            ? { accept: 'application/json' }
            : { accept: 'application/json', 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      let parsed: unknown = null;
      try {
        parsed = await response.json();
      } catch {
        // 错误页可能不是 JSON。留 null，由下面的分支带上状态码说话。
      }
      return { ok: response.ok, status: response.status, body: parsed };
    } catch (error) {
      const reason = error instanceof Error && error.name === 'AbortError' ? '请求超时' : '连不上服务端';
      return { ok: false, status: 0, body: { code: reason } };
    } finally {
      clearTimeout(timer);
    }
  };

  /** 失败时把手上的话说全：状态码 + 服务端 code + 已解析出的 blockers。 */
  const failure = (envelope: RawEnvelope, what: string): CommandResult => {
    const body = asRecord(envelope.body);
    const code = body === null ? '' : readString(body, 'code');
    const blockers = body === null ? [] : parseBlockers(body.blockers);
    const parts = [what];
    if (envelope.status !== 0) parts.push(`HTTP ${envelope.status}`);
    if (code !== '') parts.push(code);
    return { ok: false, message: parts.join(' · '), blockers };
  };

  return {
    review: async (commissionId) => {
      const envelope = await send(`commissions/${encodeURIComponent(commissionId)}/review`, 'GET');
      // 还没到 ready_to_report 时服务端回 409，这不是故障，是"现在还不该批"。
      if (envelope.status === 409) {
        return {
          ok: false,
          message: '这一单还没到验收环节。',
          blockers: [],
        };
      }
      if (!envelope.ok) return failure(envelope, '读不到初审');
      const review = parseReview(envelope.body);
      if (review === null) return { ok: false, message: '初审返回的形状看不懂。', blockers: [] };
      return { ok: true, review };
    },

    accept: async (commissionId) => {
      const envelope = await send(`commissions/${encodeURIComponent(commissionId)}/accept`, 'POST', {});
      // 409 带着 blockers 回来：那一单确实还不能准奏，把那几条原样交给抽屉。
      if (!envelope.ok) return failure(envelope, '准奏没成功');
      // accept 直接回整行督办（不是信封）。
      const body = asRecord(envelope.body);
      return { ok: true, settled: { status: body === null ? '' : readString(body, 'status'), note: '' } };
    },

    reject: async (commissionId, feedback) => {
      const trimmed = feedback.trim();
      // 服务端删掉的批注没法拿来归纳准则，提前拦下比发出去挨 400 好。
      if ([...trimmed].length < REJECT_FEEDBACK_MIN) {
        return {
          ok: false,
          message: `批注至少 ${REJECT_FEEDBACK_MIN} 个字，服务端才归纳得出准则。`,
          blockers: [],
        };
      }
      const envelope = await send(`commissions/${encodeURIComponent(commissionId)}/reject`, 'POST', {
        feedback: trimmed,
      });
      if (!envelope.ok) return failure(envelope, '打回没成功');
      const result = parseRejectResult(envelope.body, commissionId);
      if (result === null) return { ok: false, message: '打回返回的形状看不懂。', blockers: [] };
      return { ok: true, reject: result };
    },

    answer: async (commissionId, text) => {
      const envelope = await send(`commissions/${encodeURIComponent(commissionId)}/answer`, 'POST', {
        answer: text,
      });
      if (!envelope.ok) return failure(envelope, '答复没送到');
      // answer 回 `{commission, reply, auto_answered}`。
      const body = asRecord(envelope.body);
      const commission = body === null ? null : asRecord(body.commission);
      return {
        ok: true,
        settled: {
          status: commission === null ? '' : readString(commission, 'status'),
          note: body === null ? '' : readString(body, 'reply'),
        },
      };
    },
  };
}

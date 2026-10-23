/** 意图模型客户端：把一句话发给主服务，拿回一份（尚未校验的）意图 JSON。
 *
 * 只做运输，不做判断：
 *   - 不校验形状（那是 `intent-schema.ts` 的事）
 *   - 不猜意图（失败就是失败）
 *   - 不重试（世界等不起；重试会让"没听懂"变成"过一会儿突然做了"）
 *
 * 与 `ledger.ts` 的 `getJson` 同一套超时纪律：超时按失败处理。
 * 区别是这里**失败需要把原因带回世界**，所以抛出的错误带状态码与 detail，
 * 由 `intent-router.ts` 落到 unknown 的 `modelError` 上。
 */

import type { IntentModel } from './intent-router.ts';
import type { PrefabBlueprint } from './types.ts';
import type { IntentContext } from './intent-context.ts';

/** 超过这个时间就当没听懂。服务端自己有 8s，前端多给 2s 余量。 */
export const INTENT_TIMEOUT_MS = 10_000;

export const INTENT_URL = '/api/world/intent';

export class IntentClientError extends Error {
  /** HTTP 状态码；网络层失败时为 null。 */
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.status = status;
  }
}

/**
 * 造一个真实客户端。
 *
 * `templates` 在调用时从蓝图注册表取，不是构造时快照——这样世界里加了新模板，
 * 模型下一次问就知道了，不需要重建客户端。
 *
 * `context` 同理：每次调用现取世界现状（几栋楼、谁在忙），不是构造时快照。
 * 省略时请求体里不带这个字段，服务端按「没有现状」处理——旧客户端不会因此报错。
 */
export function createIntentClient(
  templates: () => ReadonlyMap<string, PrefabBlueprint>,
  options: {
    readonly fetchImpl?: typeof fetch;
    readonly timeoutMs?: number;
    readonly context?: () => IntentContext | undefined;
  } = {},
): IntentModel {
  const fetchImpl = options.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
  const timeoutMs = options.timeoutMs ?? INTENT_TIMEOUT_MS;
  const contextOf = options.context;

  return {
    async classify(utterance: string): Promise<unknown> {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      // 每次调用现取：世界在两次问之间可能建了楼、有人开工了。
      const context = contextOf?.();
      try {
        let response: Response;
        try {
          response = await fetchImpl(INTENT_URL, {
            method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({
              utterance,
              templates: [...templates().values()].map((template) => ({
                id: template.id,
                displayName: template.displayName,
                boundTask: template.boundTask,
              })),
              // 没有现状时整个字段省略，不发 null：服务端把它当「没有」处理，
              // 而 null 会让 pydantic 把可选字段判成显式的 null 再走一遍。
              ...(context === undefined ? {} : { context }),
            }),
            signal: controller.signal,
          });
        } catch (error) {
          // 网络层失败（断网、超时、CORS）。带原因回去，世界会显示"没听懂"。
          const why = controller.signal.aborted ? `超时 ${timeoutMs}ms` : errorText(error);
          throw new IntentClientError(`意图服务不可达：${why}`);
        }

        if (!response.ok) {
          // 服务端把原因放在 detail 里（503 无 Key / 502 模型失败 / 422 参数错）。
          const detail = await readDetail(response);
          throw new IntentClientError(detail, response.status);
        }

        let body: unknown;
        try {
          body = await response.json();
        } catch {
          throw new IntentClientError('意图服务返回的不是 JSON', response.status);
        }

        // 服务端的信封是 { intent, model, source }。取出 intent 交给校验器；
        // 形状不对就当作失败，不让 undefined 混进校验器。
        if (!isRecord(body) || !('intent' in body)) {
          throw new IntentClientError('意图服务的响应缺少 intent 字段', response.status);
        }
        return body.intent;
      } finally {
        clearTimeout(timer);
      }
    },
  };
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === 'string') return body.detail;
  } catch {
    // 不是 JSON 就退回状态码。
  }
  return `意图服务返回 ${response.status}`;
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

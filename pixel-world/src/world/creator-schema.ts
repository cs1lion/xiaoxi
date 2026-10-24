/** 造物主的输出 schema 与校验器。
 *
 * 这是 L3（LLM 生成器）的边界。模型输出 JSON，**这里决定它能不能进世界**。
 *
 * 三条设计原则：
 *
 * 1. **只接受离散的选择，不接受自由创作。**
 *    模板 id 必须是注册表里有的，颜色必须是 24 色表里的 token 名。
 *    十六进制字面量一律拒绝——那是绕开调色板预算，而 24 色是已批准的 ADR。
 *
 * 2. **未知字段直接拒绝，不静默忽略。**
 *    模型多写一个 `furniture` 字段，说明它以为世界有家具层。静默丢掉会让它
 *    一直以为自己写对了。拒绝并指名，反馈回路才成立。
 *
 * 3. **只管形状，不管空间。**
 *    「这个位置能不能建」是 `checkPlacement` 的事（迷雾、占地、留白、门口）。
 *    这里只回答「这份 JSON 是不是一份合法的造物意图」。
 *
 * 与 `blueprints.ts` 的差异是**故意的**：蓝图解析器接受 `#rrggbb` 旧格式
 * （带警告）以兼容历史数据；造物主是新的输入口，从一开始就不该允许它。
 */

import { WORLD } from './palette-data.ts';
import type { GridPoint, PrefabBlueprint } from './types.ts';

export class CreatorSchemaError extends Error {}

/** 造物意图：用哪个模板、放哪、什么配色。 */
export interface CreationPlan {
  readonly templateId: string;
  readonly at: GridPoint;
  /** 已经解析成色值的调色板。 */
  readonly palette: PrefabBlueprint['palette'];
}

/** 允许的顶层字段。多一个就拒。 */
const ALLOWED_KEYS = new Set(['templateId', 'at', 'palette']);
/** 允许的调色板槽位。 */
const PALETTE_SLOTS = ['body', 'roof', 'trim', 'window'] as const;
type PaletteSlot = (typeof PALETTE_SLOTS)[number];

/**
 * 校验一份造物意图。
 *
 * `templates` 是允许的模板表：模型不能发明新模板。
 */
export function parseCreationPlan(
  raw: unknown,
  templates: ReadonlyMap<string, PrefabBlueprint>,
  worldSize: { readonly width: number; readonly height: number },
): CreationPlan {
  const fail = (why: string): never => {
    throw new CreatorSchemaError(why);
  };

  if (!isRecord(raw)) fail('造物意图必须是对象');
  const record = raw as Record<string, unknown>;

  // 先查未知字段：它最能暴露"模型以为世界有这个能力"。
  for (const key of Object.keys(record)) {
    if (!ALLOWED_KEYS.has(key)) {
      fail(`未知字段 "${key}"：造物意图只接受 ${[...ALLOWED_KEYS].join(' / ')}`);
    }
  }

  const rawTemplateId = record.templateId;
  if (typeof rawTemplateId !== 'string' || rawTemplateId.trim() === '') {
    fail('templateId 必须是非空字符串');
  }
  const templateId = rawTemplateId as string;
  const template = templates.get(templateId);
  if (template === undefined) {
    fail(`未知模板 "${templateId}"：可选 ${[...templates.keys()].join(' / ')}`);
  }
  const resolvedTemplate = template as PrefabBlueprint;

  const at = readPoint(record.at, worldSize, fail);
  const palette = readPalette(record.palette, resolvedTemplate.palette, fail);

  return { templateId, at, palette };
}

function readPoint(
  value: unknown,
  worldSize: { readonly width: number; readonly height: number },
  fail: (why: string) => never,
): GridPoint {
  if (!isRecord(value)) fail('at 必须是 { x, y }');
  const x = value.x;
  const y = value.y;
  if (typeof x !== 'number' || !Number.isInteger(x)) fail('at.x 必须是整数格坐标');
  if (typeof y !== 'number' || !Number.isInteger(y)) fail('at.y 必须是整数格坐标');
  // 只判"在不在世界范围里"。能不能建（迷雾、占地、留白、门口）由 checkPlacement 判。
  if (x < 0 || y < 0 || x >= worldSize.width || y >= worldSize.height) {
    fail(`at (${x}, ${y}) 在世界之外：范围是 0..${worldSize.width - 1} × 0..${worldSize.height - 1}`);
  }
  return { x, y };
}

function readPalette(
  value: unknown,
  fallback: PrefabBlueprint['palette'],
  fail: (why: string) => never,
): PrefabBlueprint['palette'] {
  // 省略调色板是合法的：用模板自带的配色。
  if (value === undefined) return { ...fallback };
  if (!isRecord(value)) fail('palette 必须是对象');

  for (const key of Object.keys(value)) {
    if (!(PALETTE_SLOTS as readonly string[]).includes(key)) {
      fail(`未知调色板槽 "${key}"：只接受 ${PALETTE_SLOTS.join(' / ')}`);
    }
  }

  const resolved = { ...fallback };
  for (const slot of PALETTE_SLOTS) {
    const raw = value[slot];
    if (raw === undefined) continue; // 没给就回落模板
    if (typeof raw !== 'string') fail(`palette.${slot} 必须是色名 token`);
    const token = raw.trim();
    if (token.startsWith('#')) {
      fail(
        `palette.${slot} 不接受十六进制 "${token}"：只能用 24 色表的 token 名，` +
          '否则会绕开调色板预算',
      );
    }
    if (!Object.prototype.hasOwnProperty.call(WORLD, token)) {
      fail(
        `palette.${slot} 的 "${token}" 不在 24 色表里：可选 ${Object.keys(WORLD).join(' / ')}`,
      );
    }
    resolved[slot as PaletteSlot] = WORLD[token as keyof typeof WORLD];
  }
  return resolved;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

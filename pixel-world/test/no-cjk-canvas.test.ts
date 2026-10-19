import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** D5-A 守卫：世界内中文不得画进 canvas（不再有 add.text 中文文本）。 */
function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (path.endsWith('.ts')) out.push(path);
  }
  return out;
}

const SRC = fileURLToPath(new URL('../src', import.meta.url));
const files = walk(SRC);

test('src 内不再有 canvas 文本调用（D5-A）', () => {
  const offenders: string[] = [];
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    const matches = text.match(/\.add\.text\(|\.text\(/g);
    if (matches !== null) offenders.push(`${file} → ${matches.join(',')}`);
  }
  assert.deepEqual(offenders, [], `canvas 文本调用必须清零：\n${offenders.join('\n')}`);
});

test('canvas 相关源码里不出现新增 CJK 字面量（overlay 文案除外）', () => {
  const allowed = new Set([
    'src/ui/world-overlay.ts',
    'src/render/world-overlay.ts',
    'src/render/labels.ts',
  ]);
  const offenders: string[] = [];
  for (const file of files) {
    const rel = file.slice(SRC.length + 1).replaceAll('\\', '/');
    if (allowed.has(rel) || rel.startsWith('ui/')) continue;
    const text = readFileSync(file, 'utf8');
    if (/[\u4e00-\u9fff]/.test(text)) {
      // 注释/字符串里的中文允许存在；这里只拦“像 canvas 文本”的旧写法。
      if (/add\.text|setText\(/.test(text)) offenders.push(rel);
    }
  }
  assert.deepEqual(offenders, []);
});

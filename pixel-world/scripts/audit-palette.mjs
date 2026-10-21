#!/usr/bin/env node
/**
 * 调色板审计（只报告，不阻断构建）。
 *
 * 口径（用户明确要求）：
 *   1. 只统计源码里的“源色值”，不模拟运行时色调/透明度叠加；
 *   2. 世界层与 UI 层分开统计；
 *   3. 来源：世界 = src/main.ts、src/render/*.ts（含 palette.ts 的 token 定义）、
 *            src/scenes/*.ts、src/world/prefabs.json 的 palette 槽位；
 *            UI   = index.html 的 <style>（CSS 变量与字面量）、src/ui/*.ts 的内联颜色；
 *   4. 除 0xRRGGBB 外，Phaser 文本颜色以 '#rrggbb' 字符串出现，同样计入（否则会漏 5 个画布文字色）；
 *   5. alpha/透明度不单独算颜色；rgba() 只取 RGB 三分量归一为 #rrggbb。
 *
 * 计数口径：
 *   - “使用次数”= 应用/引用站点数：字面量、UI.xxx token 引用、var(--x) 引用、prefab palette 槽位；
 *   - palette.ts 的 token 定义、CSS 变量的定义不计为使用，单独统计；
 *   - 每文件“字面量统计”用于手算核对，含 palette.ts 的 22 个 0x 定义。
 *
 * 用法：
 *   node scripts/audit-palette.mjs            # 人读报告（stdout）
 *   node scripts/audit-palette.mjs --json     # 机器可读 JSON（stdout）
 *   node scripts/audit-palette.mjs --strict   # 超预算时退出码 1（默认只报告，退出码 0）
 */

import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BUDGET = { world: 24, ui: 8 };

/** 提案：24 色世界调色板。前 16 色为原材料表，W17–W24 为 2026-10-21 ADR 新增材料/光色。 */
const PROPOSED_WORLD = [
  { hex: '#14110d', role: '最暗/轮廓/阴影/眼睛（合并 ink、shadow、sky）' },
  { hex: '#1b211c', role: '深土/树冠暗部/街巷暗（当前 soil 定义未引用，保留槽）' },
  { hex: '#2d3b2e', role: '草地基色' },
  { hex: '#334234', role: '草地亮部/树冠亮部' },
  { hex: '#3e433f', role: '石/灰泥基色' }, // 2026-10-20 明度重排方案 A：原 #3a3f3b
  { hex: '#4a4f42', role: '广场/道路/石材亮部' },
  { hex: '#3b3423', role: '农田土' },
  { hex: '#8a5a44', role: '木/砖墙基色（建筑主体）' },
  { hex: '#4a3f39', role: '木/屋顶暗部（屋顶、树干、伞杆）' },
  { hex: '#c9a86a', role: '暖色饰边/脚手架/旗帜/浅木' },
  { hex: '#e3a13c', role: '焦点琥珀（窗光、进度、路灯、祭坛符号）' },
  { hex: '#e0b48c', role: '皮肤' },
  { hex: '#30506b', role: '斗篷基色' },
  { hex: '#223a4d', role: '斗篷暗部' },
  { hex: '#c06a3e', role: '围巾/强调橙（同时给故障与暖色点缀）' },
  { hex: '#d8dad3', role: '烟/纸片/雨/高光浅色' },
  { hex: '#c2b39a', role: '灰泥墙/浅色纸面（书阁主体）' },
  { hex: '#9c4f3c', role: '陶瓦/砖（工坊屋顶、干草垛）' },
  { hex: '#5f8a74', role: '铜绿屋顶（书阁塔楼）' },
  { hex: '#3d6b7a', role: '水（水洼、雾、冷反光）' },
  { hex: '#4e6241', role: '苔（石缝、树冠、绿化带）' },
  { hex: '#18242b', role: '冷影（檐下、底部、雾的冷暗层）' },
  { hex: '#f2c879', role: '暖高光（窗芯、灯芯、祭坛符文）' },
  { hex: '#5a463e', role: '炭黑（暗瓦、烟囱、铁器）' },
];

/** 提案：8 色 UI 调色板。 */
const PROPOSED_UI = [
  { hex: '#e8e2d2', role: '纸面/深底亮文字' },
  { hex: '#24211b', role: '纸上墨字' },
  { hex: '#8e968c', role: '次要文字（深底）' },
  { hex: '#171b18', role: '面板底' },
  { hex: '#2b322c', role: '分隔线/边框' },
  { hex: '#e3a13c', role: '强调/焦点（与琥珀同值）' },
  { hex: '#0c0f0d', role: '页面底/投影' },
  { hex: '#c8876a', role: '警告/错误' },
];

const PROPOSED_WORLD_HEX = PROPOSED_WORLD.map((item) => item.hex);
const PROPOSED_UI_HEX = PROPOSED_UI.map((item) => item.hex);

// ---------------------------------------------------------------- file lists

function read(rel) {
  return readFileSync(path.join(ROOT, rel), 'utf8');
}

function listFiles(relDir, ext) {
  const dir = path.join(ROOT, relDir);
  return readdirSync(dir)
    .filter((name) => name.endsWith(ext))
    .sort()
    .map((name) => path.posix.join(relDir, name));
}

const WORLD_FILES = [
  'src/main.ts',
  ...listFiles('src/render', '.ts'),
  ...listFiles('src/scenes', '.ts'),
];
const UI_TS_FILES = listFiles('src/ui', '.ts');
const PREFABS_FILE = 'src/world/prefabs.json';
const HTML_FILE = 'index.html';
const PALETTE_FILE = 'src/world/palette-data.ts';

// ---------------------------------------------------------------- color utils

const clamp255 = (value) => Math.max(0, Math.min(255, Math.round(value)));

function hexToRgb(hex) {
  const raw = hex.replace('#', '');
  return {
    r: parseInt(raw.slice(0, 2), 16),
    g: parseInt(raw.slice(2, 4), 16),
    b: parseInt(raw.slice(4, 6), 16),
  };
}

function rgbToHex(r, g, b) {
  return `#${[r, g, b].map((value) => clamp255(value).toString(16).padStart(2, '0')).join('')}`;
}

function normalizeHex(raw, digits) {
  const value = raw.toLowerCase();
  if (digits === 3) return `#${value[0]}${value[0]}${value[1]}${value[1]}${value[2]}${value[2]}`;
  return `#${value}`;
}

function rgbToLab({ r, g, b }) {
  const linear = [r, g, b].map((value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  const x = (linear[0] * 0.4124 + linear[1] * 0.3576 + linear[2] * 0.1805) / 0.95047;
  const y = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  const z = (linear[0] * 0.0193 + linear[1] * 0.1192 + linear[2] * 0.9505) / 1.08883;
  const f = (value) => (value > 0.008856 ? Math.cbrt(value) : 7.787 * value + 16 / 116);
  return { L: 116 * f(y) - 16, a: 500 * (f(x) - f(y)), b: 200 * (f(y) - f(z)) };
}

function deltaE76(hexA, hexB) {
  const a = rgbToLab(hexToRgb(hexA));
  const b = rgbToLab(hexToRgb(hexB));
  return Math.hypot(a.L - b.L, a.a - b.a, a.b - b.b);
}

function luminance(hex) {
  const { r, g, b } = hexToRgb(hex);
  const [rl, gl, bl] = [r, g, b].map((value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl;
}

function contrast(hexA, hexB) {
  const [bright, dark] = [luminance(hexA), luminance(hexB)].sort((a, b) => b - a);
  return (bright + 0.05) / (dark + 0.05);
}

// ---------------------------------------------------------------- text utils

function stripBlockComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, (match) => match.replace(/[^\n]/g, ' '));
}

function stripLineComments(text) {
  return text
    .split('\n')
    .map((line) => {
      if (line.includes('://')) return line;
      const index = line.indexOf('//');
      return index >= 0 ? line.slice(0, index) : line;
    })
    .join('\n');
}

/** 容忍 '#abc' / '#aabbcc' / '0xRRGGBB' / 'rgb(...)' / 'rgba(...)'。 */
function parseColorValue(raw) {
  const text = raw.trim();
  const hex6 = /^0x([0-9a-fA-F]{6})$/.exec(text);
  if (hex6 !== null) return normalizeHex(hex6[1], 6);
  const hash = /^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$/.exec(text);
  if (hash !== null) return normalizeHex(hash[1], hash[1].length);
  const rgb = /^rgba?\(([^)]*)\)$/.exec(text);
  if (rgb !== null) {
    const parts = rgb[1]
      .split(/[,/\s]+/)
      .map((part) => part.trim())
      .filter(Boolean);
    const channels = parts.slice(0, 3).map(Number);
    if (channels.length !== 3 || channels.some((value) => !Number.isFinite(value))) return null;
    return rgbToHex(channels[0], channels[1], channels[2]);
  }
  return null;
}

const JS_COLOR_RE = /0x([0-9a-fA-F]{6})\b|#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\(([^)]*)\)/g;
const CSS_COLOR_RE = /#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\(([^)]*)\)/g;
const CSS_VAR_DEF_RE = /--([a-zA-Z0-9_-]+)\s*:\s*(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}|rgba?\([^)]*\)|0x[0-9a-fA-F]{6})\s*;/g;
const CSS_VAR_USE_RE = /var\(--([a-zA-Z0-9_-]+)\)/g;

/** JS/TS 源码里的颜色字面量站点。 */
function scanJsColorSites(text, file) {
  const sites = [];
  for (const [index, line] of text.split('\n').entries()) {
    const regex = new RegExp(JS_COLOR_RE.source, JS_COLOR_RE.flags);
    for (;;) {
      const match = regex.exec(line);
      if (match === null) break;
      let hex = null;
      if (match[1] !== undefined) hex = normalizeHex(match[1], 6);
      else if (match[2] !== undefined) hex = normalizeHex(match[2], match[2].length);
      else if (match[3] !== undefined) hex = parseColorValue(`rgba(${match[3]})`);
      if (hex !== null) sites.push({ hex, file, line: index + 1, alpha: null });
    }
  }
  return sites;
}

/** CSS 片段里的颜色字面量站点（不含 var 定义里的颜色）。 */
function scanCssColorSites(text, file, offsetLine = 0) {
  const sites = [];
  for (const [index, line] of text.split('\n').entries()) {
    const remainder = line.replace(new RegExp(CSS_VAR_DEF_RE.source, CSS_VAR_DEF_RE.flags), ' ');
    const regex = new RegExp(CSS_COLOR_RE.source, CSS_COLOR_RE.flags);
    for (;;) {
      const match = regex.exec(remainder);
      if (match === null) break;
      let hex = null;
      if (match[1] !== undefined) hex = normalizeHex(match[1], match[1].length);
      else if (match[2] !== undefined) hex = parseColorValue(`rgba(${match[2]})`);
      if (hex !== null) sites.push({ hex, file, line: index + 1 + offsetLine, alpha: null });
    }
  }
  return sites;
}

// ---------------------------------------------------------------- palette.ts tokens

function parsePaletteTokens() {
  const source = stripLineComments(stripBlockComments(read(PALETTE_FILE)));
  const tokens = [];
  // D6-A 之后 token 定义在 WORLD（世界 24 色）；TERRAIN_COLORS/TERRAIN_SPECK 是指向 WORLD 的别名表。
  for (const objectName of ['WORLD', 'TERRAIN_COLORS', 'TERRAIN_SPECK']) {
    const startMatch = new RegExp(`export const ${objectName}\\s*=\\s*\\{`).exec(source);
    if (startMatch === null) continue;
    const start = startMatch.index + startMatch[0].length;
    let depth = 1;
    let end = start;
    while (end < source.length && depth > 0) {
      if (source[end] === '{') depth += 1;
      if (source[end] === '}') depth -= 1;
      end += 1;
    }
    const block = source.slice(start, end - 1);
    const lineOffset = source.slice(0, start).split('\n').length;
    for (const [index, line] of block.split('\n').entries()) {
      const match = /^\s*([a-zA-Z0-9_]+)\s*:\s*(0x[0-9a-fA-F]{6}|(?:WORLD|UI)\.[a-zA-Z0-9_]+)/.exec(line);
      if (match === null) continue;
      tokens.push({
        object: objectName,
        name: match[1],
        fullName: `${objectName}.${match[1]}`,
        raw: match[2],
        hex: match[2].startsWith('0x') ? normalizeHex(match[2].slice(2), 6) : null,
        aliasOf: match[2].includes('.') ? match[2] : null,
        file: PALETTE_FILE,
        line: lineOffset + index,
      });
    }
  }
  const byFullName = new Map(tokens.map((token) => [token.fullName, token]));
  for (const token of tokens) {
    if (token.aliasOf === null) continue;
    const target = byFullName.get(token.aliasOf);
    if (target !== undefined) token.hex = target.hex;
  }
  return tokens.filter((token) => token.hex !== null);
}

function scanPaletteTokenRefs(tokens) {
  const byFullName = new Map(tokens.map((token) => [token.fullName, token]));
  const sites = [];
  // palette.ts 内部的 TERRAIN_COLORS.grass: UI.grass 是定义别名，不算使用。
  for (const file of WORLD_FILES.filter((item) => item !== PALETTE_FILE)) {
    const text = stripLineComments(stripBlockComments(read(file)));
    for (const [index, line] of text.split('\n').entries()) {
      for (const objectName of ['WORLD', 'TERRAIN_COLORS', 'TERRAIN_SPECK']) {
        const direct = new RegExp(`\\b${objectName}\\.([a-zA-Z0-9_]+)\\b`, 'g');
        for (;;) {
          const match = direct.exec(line);
          if (match === null) break;
          const token = byFullName.get(`${objectName}.${match[1]}`);
          if (token === undefined) continue;
          sites.push({ hex: token.hex, token: token.fullName, file, line: index + 1, alpha: null });
        }
        // 动态访问 TERRAIN_COLORS[cell.terrain] / TERRAIN_SPECK[terrain]：整个对象被使用。
        if (new RegExp(`\\b${objectName}\\[`).test(line)) {
          for (const token of tokens.filter((item) => item.object === objectName)) {
            sites.push({ hex: token.hex, token: token.fullName, file, line: index + 1, alpha: null });
          }
        }
      }
    }
  }
  return sites;
}

// ---------------------------------------------------------------- prefabs / UI / CSS

function scanPrefabSites(worldTokens) {
  const byTokenName = new Map(
    worldTokens.filter((token) => token.object === 'WORLD').map((token) => [token.name, token.hex]),
  );
  const text = read(PREFABS_FILE);
  const lines = text.split('\n');
  const idLines = new Map();
  for (const [index, line] of lines.entries()) {
    // 只认顶层 prefab（两个空格缩进），避免把 footprint/door/palette 当成 prefab。
    const match = /^  "([a-zA-Z0-9_]+)"\s*:\s*\{/.exec(line);
    if (match !== null && !idLines.has(match[1])) idLines.set(match[1], index);
  }
  const order = [...idLines.entries()].sort((a, b) => a[1] - b[1]);
  const data = JSON.parse(text);
  const sites = [];
  for (const [position, [prefabId, startLine]] of order.entries()) {
    const endLine = position + 1 < order.length ? order[position + 1][1] : lines.length;
    const paletteLine = lines.findIndex((line, index) => index >= startLine && index < endLine && line.includes('"palette"'));
    const palette = data[prefabId]?.palette;
    if (palette === undefined || paletteLine < 0) continue;
    for (const [slot, value] of Object.entries(palette)) {
      // D6-A 之后槽位是 WORLD token 名；旧值（整数 / "#rrggbb"）仍然计入。
      if (typeof value === 'string' && !value.startsWith('#')) {
        const hex = byTokenName.get(value);
        if (hex === undefined) continue; // 非法 token 由蓝图解析器报错，审计只跳过
        sites.push({ hex, prefab: prefabId, slot, token: value, file: PREFABS_FILE, line: paletteLine + 1, alpha: null });
        continue;
      }
      if (typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value)) {
        sites.push({ hex: normalizeHex(value.slice(1), 6), prefab: prefabId, slot, file: PREFABS_FILE, line: paletteLine + 1, alpha: null });
        continue;
      }
      if (!Number.isInteger(value)) continue;
      sites.push({
        hex: normalizeHex(value.toString(16).padStart(6, '0'), 6),
        prefab: prefabId,
        slot,
        file: PREFABS_FILE,
        line: paletteLine + 1,
        alpha: null,
      });
    }
  }
  return sites;
}

/** 返回 index.html `<style>` 的 {line,text}；行号保留原文件行号。 */
function readStyleLines() {
  const lines = read(HTML_FILE).split('\n');
  const out = [];
  let inStyle = false;
  for (const [index, line] of lines.entries()) {
    if (!inStyle && /<style[^>]*>/.test(line)) inStyle = true;
    if (inStyle) out.push({ line: index + 1, text: line.replace(/\/\*[\s\S]*?\*\//g, ' ') });
    if (inStyle && /<\/style>/.test(line)) inStyle = false;
  }
  return out;
}

function scanUiCss() {
  const styles = readStyleLines();
  const defs = [];
  const vars = new Map();
  for (const item of styles) {
    const regex = new RegExp(CSS_VAR_DEF_RE.source, CSS_VAR_DEF_RE.flags);
    for (;;) {
      const match = regex.exec(item.text);
      if (match === null) break;
      const hex = parseColorValue(match[2]);
      if (hex === null) continue;
      defs.push({ hex, name: `--${match[1]}`, file: HTML_FILE, line: item.line });
      vars.set(match[1], hex);
    }
  }
  const refs = [];
  for (const item of styles) {
    const literalSites = scanCssColorSites(item.text, HTML_FILE, item.line - 1);
    for (const site of literalSites) refs.push({ ...site, kind: 'literal' });
    const useRegex = new RegExp(CSS_VAR_USE_RE.source, CSS_VAR_USE_RE.flags);
    for (;;) {
      const match = useRegex.exec(item.text);
      if (match === null) break;
      const hex = vars.get(match[1]);
      if (hex === undefined) continue;
      refs.push({ hex, varName: `--${match[1]}`, file: HTML_FILE, line: item.line, alpha: null, kind: 'css-var-use' });
    }
  }
  // 样式块外的内联 style=""（当前没有，保留扫描口）。
  for (const [index, line] of read(HTML_FILE).split('\n').entries()) {
    if (!/style\s*=/.test(line)) continue;
    for (const site of scanCssColorSites(line, HTML_FILE, index)) refs.push({ ...site, kind: 'literal' });
  }
  return { refs, defs };
}

function scanUiTs() {
  const refs = [];
  for (const file of UI_TS_FILES) {
    const text = stripLineComments(stripBlockComments(read(file)));
    for (const site of scanJsColorSites(text, file)) refs.push({ ...site, kind: 'literal' });
  }
  return refs;
}

// ---------------------------------------------------------------- aggregate

const paletteTokens = parsePaletteTokens();
const paletteRefs = scanPaletteTokenRefs(paletteTokens);
const prefabSites = scanPrefabSites(paletteTokens);
const uiCss = scanUiCss();
const uiTs = scanUiTs();

const colors = new Map();
function bucket(hex) {
  if (!colors.has(hex)) {
    colors.set(hex, { hex, world: { refs: [], defs: [] }, ui: { refs: [], defs: [] } });
  }
  return colors.get(hex);
}

for (const file of WORLD_FILES.filter((item) => item !== PALETTE_FILE)) {
  const text = stripLineComments(stripBlockComments(read(file)));
  for (const site of scanJsColorSites(text, file)) bucket(site.hex).world.refs.push({ ...site, kind: 'literal' });
}
for (const token of paletteTokens) {
  bucket(token.hex).world.defs.push({
    kind: 'palette-token',
    token: token.fullName,
    file: token.file,
    line: token.line,
  });
}
for (const site of paletteRefs) bucket(site.hex).world.refs.push({ ...site, kind: 'palette-ref' });
for (const site of prefabSites) bucket(site.hex).world.refs.push({ ...site, kind: 'prefab-slot' });

for (const site of uiCss.refs) bucket(site.hex).ui.refs.push(site);
for (const def of uiCss.defs) bucket(def.hex).ui.defs.push({ ...def, kind: 'css-var-def' });
for (const site of uiTs) bucket(site.hex).ui.refs.push(site);

const worldColors = [...colors.values()].filter(
  (color) => color.world.refs.length > 0 || color.world.defs.length > 0,
);
const uiColors = [...colors.values()].filter((color) => color.ui.refs.length > 0 || color.ui.defs.length > 0);
const sharedColors = worldColors.filter((color) => uiColors.some((other) => other.hex === color.hex));
const unionColors = [...colors.values()].filter(
  (color) => worldColors.includes(color) || uiColors.includes(color),
);

const sortByRefs = (list, layer) =>
  [...list].sort((a, b) => b[layer].refs.length - a[layer].refs.length || a.hex.localeCompare(b.hex));

function nearestProposed(hex, layer) {
  const list = layer === 'world' ? PROPOSED_WORLD : PROPOSED_UI;
  let best = null;
  for (const item of list) {
    const distance = deltaE76(hex, item.hex);
    if (best === null || distance < best.distance) best = { ...item, distance };
  }
  return best;
}

function suggestionFor(distance) {
  if (distance < 4) return '近似等价：改引用/换色值即可';
  if (distance < 12) return '需微调并重验轮廓';
  return '需重画或改材质';
}

function summarizeOut(list) {
  const summary = { equivalent: 0, adjust: 0, repaint: 0 };
  for (const item of list) {
    if (item.suggestion.startsWith('近似等价')) summary.equivalent += 1;
    else if (item.suggestion.startsWith('需微调')) summary.adjust += 1;
    else summary.repaint += 1;
  }
  return summary;
}

function proposedRows(layer) {
  const proposal = layer === 'world' ? PROPOSED_WORLD : PROPOSED_UI;
  return proposal.map((item) => {
    const color = colors.get(item.hex);
    const refs = color?.[layer]?.refs ?? [];
    const defs = color?.[layer]?.defs ?? [];
    const otherLayer = layer === 'world' ? 'ui' : 'world';
    const other = color?.[otherLayer];
    return {
      ...item,
      refs: refs.length,
      defs: defs.length,
      shared: (other?.refs.length ?? 0) + (other?.defs.length ?? 0) > 0,
      luminance: luminance(item.hex),
      samples: refs.slice(0, 3),
      reserved: refs.length === 0,
    };
  });
}

function outOfBudget(layer) {
  const proposal = new Set(layer === 'world' ? PROPOSED_WORLD_HEX : PROPOSED_UI_HEX);
  const list = layer === 'world' ? worldColors : uiColors;
  return list
    .filter((color) => !proposal.has(color.hex))
    .map((color) => {
      const refs = color[layer].refs;
      const nearest = nearestProposed(color.hex, layer);
      return {
        hex: color.hex,
        refs: refs.length,
        defs: color[layer].defs.length,
        luminance: luminance(color.hex),
        nearest,
        suggestion: suggestionFor(nearest.distance),
        // 只有定义没有引用的色（如 UI.street）也要能定位到文件:行号。
        sites: (refs.length > 0 ? refs : color[layer].defs).slice(0, 4),
      };
    })
    .sort((a, b) => b.refs - a.refs || a.hex.localeCompare(b.hex));
}

function fileLiteralStats() {  const rows = [];
  for (const file of WORLD_FILES) {
    const text = stripLineComments(stripBlockComments(read(file)));
    const sites = scanJsColorSites(text, file);
    rows.push({
      file,
      layer: 'world',
      kind: file === PALETTE_FILE ? 'palette 定义' : 'canvas 字面量',
      occurrences: sites.length,
      unique: new Set(sites.map((site) => site.hex)).size,
      hexes: [...new Set(sites.map((site) => site.hex))],
    });
  }
  rows.push({
    file: PREFABS_FILE,
    layer: 'world',
    kind: 'prefab palette 槽位',
    occurrences: prefabSites.length,
    unique: new Set(prefabSites.map((site) => site.hex)).size,
    hexes: [...new Set(prefabSites.map((site) => site.hex))],
  });
  // 手算口径：CSS 字面量“含”变量定义，便于和源码肉眼计数对齐。
  const uiSites = [];
  for (const item of readStyleLines()) {
    const regex = new RegExp(CSS_COLOR_RE.source, CSS_COLOR_RE.flags);
    for (;;) {
      const match = regex.exec(item.text);
      if (match === null) break;
      let hex = null;
      if (match[1] !== undefined) hex = normalizeHex(match[1], match[1].length);
      else if (match[2] !== undefined) hex = parseColorValue(`rgba(${match[2]})`);
      if (hex !== null) uiSites.push({ hex, line: item.line });
    }
  }
  rows.push({
    file: `${HTML_FILE} (style)`,
    layer: 'ui',
    kind: 'CSS 字面量/变量（含 var 定义）',
    occurrences: uiSites.length,
    unique: new Set(uiSites.map((site) => site.hex)).size,
    hexes: [...new Set(uiSites.map((site) => site.hex))],
  });
  return rows;
}

const usedTokenNames = new Set(paletteRefs.map((site) => site.token));
const usedTokenAliases = paletteTokens.filter((token) => token.aliasOf !== null && usedTokenNames.has(token.fullName));
for (const alias of usedTokenAliases) usedTokenNames.add(alias.aliasOf);
const unusedTokens = paletteTokens
  .map((token) => ({
    ...token,
    refs: paletteRefs.filter((site) => site.token === token.fullName).length,
    indirectlyUsed: token.aliasOf !== null ? usedTokenNames.has(token.aliasOf) : false,
  }))
  .filter((token) => token.refs === 0 && !usedTokenNames.has(token.fullName));

const report = {
  root: ROOT,
  budget: BUDGET,
  counts: {
    world: worldColors.length,
    ui: uiColors.length,
    shared: sharedColors.length,
    union: unionColors.length,
    worldRefs: worldColors.reduce((sum, color) => sum + color.world.refs.length, 0),
    uiRefs: uiColors.reduce((sum, color) => sum + color.ui.refs.length, 0),
    overBudgetWorld: Math.max(0, worldColors.length - BUDGET.world),
    overBudgetUi: Math.max(0, uiColors.length - BUDGET.ui),
  },
  shared: sharedColors.map((color) => color.hex).sort(),
  topWorld: sortByRefs(worldColors, 'world').slice(0, 24).map((color) => ({
    hex: color.hex,
    refs: color.world.refs.length,
    sites: color.world.refs.slice(0, 4),
    tokens: color.world.defs
      .filter((def) => def.kind === 'palette-token')
      .map((def) => def.token.replace(/^WORLD\./, '')),
  })),
  topUi: sortByRefs(uiColors, 'ui').slice(0, 10).map((color) => ({
    hex: color.hex,
    refs: color.ui.refs.length,
    sites: color.ui.refs.slice(0, 4),
    vars: color.ui.defs.filter((def) => def.kind === 'css-var-def').map((def) => def.name),
  })),
  unusedTokens: unusedTokens.map((token) => token.fullName),
  proposedWorld: proposedRows('world'),
  proposedUi: proposedRows('ui'),
  outWorld: outOfBudget('world'),
  outUi: outOfBudget('ui'),
  outSummary: {
    world: summarizeOut(outOfBudget('world')),
    ui: summarizeOut(outOfBudget('ui')),
  },
  files: fileLiteralStats(),
  contrast: {
    'outline/grass': contrast('#14110d', '#2d3b2e'),
    'cloak/grass': contrast('#30506b', '#2d3b2e'),
    'cloakShade/grass': contrast('#223a4d', '#2d3b2e'),
    'skin/grass': contrast('#e0b48c', '#2d3b2e'),
    'scarf/grass': contrast('#c06a3e', '#2d3b2e'),
    'amber/grass': contrast('#e3a13c', '#2d3b2e'),
    'smoke/night': contrast('#d8dad3', '#14110d'),
    'paper/ink': contrast('#e8e2d2', '#24211b'),
    'amber/panel': contrast('#e3a13c', '#171b18'),
    'dim/panel': contrast('#8e968c', '#171b18'),
    'paper/panel': contrast('#e8e2d2', '#171b18'),
  },
};

// ---------------------------------------------------------------- output

const useJson = process.argv.includes('--json');
const useMarkdown = process.argv.includes('--md');
const strict = process.argv.includes('--strict');

function fmtSite(site) {
  if (site.prefab !== undefined) {
    const detail = site.token !== undefined ? `${site.slot}=${site.token}` : site.slot;
    return `${site.file}:${site.line} (${site.prefab}.${detail})`;
  }
  if (site.token !== undefined) return `${site.file}:${site.line} (${site.token})`;
  if (site.varName !== undefined) return `${site.file}:${site.line} (${site.varName})`;
  return `${site.file}:${site.line}`;
}

function cell(value) {
  return String(value).replaceAll('|', '\\|');
}

function renderMarkdown(data) {
  const lines = [];
  const siteList = (sites) => sites.map((site) => fmtSite(site)).join('<br>');
  lines.push('# 调色板审计（只报告）', '');
  lines.push(`- 根目录：\`${data.root}\``);
  lines.push(`- 口径：只统计源码源色值；不模拟运行时 alpha/光照叠加；世界层与 UI 层分开。`);
  lines.push(`- 预算：世界 ${data.budget.world} 色 / UI ${data.budget.ui} 色。`);
  lines.push('');
  lines.push('## 计数');
  lines.push('');
  lines.push('| 层 | 唯一色 | 引用站点 | 预算 | 超出 |');
  lines.push('|---|---:|---:|---:|---:|');
  lines.push(`| 世界层 | ${data.counts.world} | ${data.counts.worldRefs} | ${data.budget.world} | ${data.counts.overBudgetWorld} |`);
  lines.push(`| UI 层 | ${data.counts.ui} | ${data.counts.uiRefs} | ${data.budget.ui} | ${data.counts.overBudgetUi} |`);
  lines.push('');
  lines.push(`- 共享色 ${data.counts.shared}：${data.shared.join(', ') || '无'}`);
  lines.push(`- 两层并集：${data.counts.union} 色`);
  lines.push(`- 越界收敛预估：世界层 近似等价 ${data.outSummary.world.equivalent} / 微调 ${data.outSummary.world.adjust} / 重画 ${data.outSummary.world.repaint}；UI 层 近似等价 ${data.outSummary.ui.equivalent} / 微调 ${data.outSummary.ui.adjust} / 重画 ${data.outSummary.ui.repaint}`);
  lines.push('');
  lines.push('## 世界层 Top（按使用次数）');
  lines.push('');
  lines.push('| # | 色值 | 使用次数 | token | 站点（前 4） |');
  lines.push('|---:|---|---:|---|---|');
  data.topWorld.forEach((item, index) => {
    lines.push(`| ${index + 1} | ${item.hex} | ${item.refs} | ${item.tokens.join('/')} | ${siteList(item.sites)} |`);
  });
  lines.push('');
  lines.push('## UI 层 Top（按使用次数）');
  lines.push('');
  lines.push('| # | 色值 | 使用次数 | CSS 变量 | 站点（前 4） |');
  lines.push('|---:|---|---:|---|---|');
  data.topUi.forEach((item, index) => {
    lines.push(`| ${index + 1} | ${item.hex} | ${item.refs} | ${item.vars.join('/')} | ${siteList(item.sites)} |`);
  });
  lines.push('');
  lines.push('## 越界色：世界层（不在提案 24 色内）');
  lines.push('');
  lines.push('| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |');
  lines.push('|---|---:|---:|---|---:|---|---|');
  for (const item of data.outWorld) {
    lines.push(`| ${item.hex} | ${item.refs} | ${(item.luminance * 100).toFixed(1)}% | ${item.nearest.hex} | ${item.nearest.distance.toFixed(1)} | ${item.suggestion} | ${siteList(item.sites)} |`);
  }
  lines.push('');
  lines.push('## 越界色：UI 层（不在提案 8 色内）');
  lines.push('');
  lines.push('| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |');
  lines.push('|---|---:|---:|---|---:|---|---|');
  for (const item of data.outUi) {
    lines.push(`| ${item.hex} | ${item.refs} | ${(item.luminance * 100).toFixed(1)}% | ${item.nearest.hex} | ${item.nearest.distance.toFixed(1)} | ${item.suggestion} | ${siteList(item.sites)} |`);
  }
  lines.push('');
  lines.push('## 提案 24 色世界');
  lines.push('');
  lines.push('| 色值 | 语义 | 当前使用 | L | 备注 |');
  lines.push('|---|---|---:|---:|---|');
  for (const item of data.proposedWorld) {
    lines.push(`| ${item.hex} | ${item.role} | ${item.refs} | ${(item.luminance * 100).toFixed(1)}% | ${[item.shared ? '与 UI 共享' : '', item.reserved ? '当前未引用/保留槽' : ''].filter(Boolean).join('；')} |`);
  }
  lines.push('');
  lines.push('## 提案 8 色 UI');
  lines.push('');
  lines.push('| 色值 | 语义 | 当前使用 | L | 备注 |');
  lines.push('|---|---|---:|---:|---|');
  for (const item of data.proposedUi) {
    lines.push(`| ${item.hex} | ${item.role} | ${item.refs} | ${(item.luminance * 100).toFixed(1)}% | ${[item.shared ? '与世界共享' : '', item.reserved ? '当前未引用/保留槽' : ''].filter(Boolean).join('；')} |`);
  }
  lines.push('');
  lines.push('## 每文件字面量统计（手算核对口径）');
  lines.push('');
  lines.push('| 文件 | 层 | 口径 | 出现次数 | 唯一色 |');
  lines.push('|---|---|---|---:|---:|');
  for (const row of data.files) {
    lines.push(`| ${row.file} | ${row.layer} | ${row.kind} | ${row.occurrences} | ${row.unique} |`);
  }
  lines.push('');
  lines.push('## 提案关键对比度（WCAG 比值）');
  lines.push('');
  lines.push('| 对比对 | 比值 |');
  lines.push('|---|---:|');
  for (const [name, value] of Object.entries(data.contrast)) {
    lines.push(`| ${name} | ${value.toFixed(2)}:1 |`);
  }
  lines.push('');
  lines.push('## 复跑');
  lines.push('');
  lines.push('```bash');
  lines.push('cd pixel-world');
  lines.push('node scripts/audit-palette.mjs          # 人读');
  lines.push('node scripts/audit-palette.mjs --json   # 机器可读');
  lines.push('node scripts/audit-palette.mjs --md     # 本 Markdown');
  lines.push('node scripts/audit-palette.mjs --strict # 超预算时退出码 1（已启用；默认不阻断）');
  lines.push('```');
  return `${lines.join('\n')}\n`;
}

if (useJson) {
  console.log(JSON.stringify(report, null, 2));
} else if (useMarkdown) {
  console.log(renderMarkdown(report));
} else {
  console.log('调色板审计（只报告，不阻断）');
  console.log(`根目录：${report.root}`);
  console.log(`口径：只统计源码源色值；不模拟运行时 alpha/光照叠加；世界层与 UI 层分开。`);
  console.log('');
  console.log('## 计数');
  console.log(`世界层唯一色：${report.counts.world}（引用 ${report.counts.worldRefs} 处）  预算 ${BUDGET.world}，超出 ${report.counts.overBudgetWorld}`);
  console.log(`UI 层唯一色：${report.counts.ui}（引用 ${report.counts.uiRefs} 处）  预算 ${BUDGET.ui}，超出 ${report.counts.overBudgetUi}`);
  console.log(`共享色：${report.counts.shared}（${report.shared.join(', ') || '无'}）`);
  console.log(`两层并集唯一色：${report.counts.union}`);
  console.log('');
  console.log('## 世界层 Top（按使用次数）');
  for (const item of report.topWorld) {
    console.log(`${item.hex}  refs=${item.refs}  ${item.tokens.join('/')}`);
    for (const site of item.sites) console.log(`    ${fmtSite(site)}`);
  }
  console.log('');
  console.log('## UI 层 Top');
  for (const item of report.topUi) {
    console.log(`${item.hex}  refs=${item.refs}  ${item.vars.join('/')}`);
    for (const site of item.sites) console.log(`    ${fmtSite(site)}`);
  }
  console.log('');
  console.log('## 世界层越界色（不在提案 24 色内）');
  for (const item of report.outWorld) {
    console.log(`${item.hex}  refs=${item.refs}  → 最近 ${item.nearest.hex} ΔE=${item.nearest.distance.toFixed(1)}  ${item.suggestion}`);
    for (const site of item.sites) console.log(`    ${fmtSite(site)}`);
  }
  console.log('');
  console.log('## UI 层越界色（不在提案 8 色内）');
  for (const item of report.outUi) {
    console.log(`${item.hex}  refs=${item.refs}  → 最近 ${item.nearest.hex} ΔE=${item.nearest.distance.toFixed(1)}  ${item.suggestion}`);
    for (const site of item.sites) console.log(`    ${fmtSite(site)}`);
  }
  console.log('');
  console.log('## 提案 24 色世界');
  for (const item of report.proposedWorld) {
    console.log(`${item.hex}  refs=${item.refs}  L=${(item.luminance * 100).toFixed(1)}%  ${item.role}${item.shared ? ' [与 UI 共享]' : ''}${item.reserved ? ' [当前未引用，保留槽]' : ''}`);
  }
  console.log('');
  console.log('## 提案 8 色 UI');
  for (const item of report.proposedUi) {
    console.log(`${item.hex}  refs=${item.refs}  L=${(item.luminance * 100).toFixed(1)}%  ${item.role}${item.shared ? ' [与世界共享]' : ''}${item.reserved ? ' [当前未引用，保留槽]' : ''}`);
  }
  console.log('');
  console.log('## palette.ts 未引用 token');
  console.log(report.unusedTokens.join(', ') || '无');
  console.log('');
  console.log('## 每文件字面量统计（手算核对口径）');
  console.log('file\tlayer\toccurrences\tunique');
  for (const row of report.files) console.log(`${row.file}\t${row.layer}\t${row.occurrences}\t${row.unique}`);
  console.log('');
  console.log('## 提案关键对比度（WCAG 比值，仅作可读性初判依据）');
  for (const [name, value] of Object.entries(report.contrast)) console.log(`${name}: ${value.toFixed(2)}:1`);
  console.log('');
  console.log('提示：--json 输出机器可读结果；--strict 在超预算时退出码 1（已启用；默认仍返回 0）。');
}

if (strict && (report.counts.overBudgetWorld > 0 || report.counts.overBudgetUi > 0)) {
  process.exitCode = 1;
}

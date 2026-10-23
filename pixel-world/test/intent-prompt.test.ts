import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildIntentMessages, buildIntentPrompt } from '../src/world/intent-prompt.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { readFileSync } from 'node:fs';

const templates = parseBlueprintTable(
  JSON.parse(readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8')),
);
const prompt = buildIntentPrompt(templates);

test('提示词里的模板表与注册表一致，不多不少', () => {
  // 这条是防漂移的核心：手抄的模板表迟早会和注册表对不上，
  // 对不上的表现是"模型说了个不存在的模板"——模型没错，是提示词在骗它。
  for (const [id, template] of templates) {
    assert.ok(prompt.includes(id), `提示词缺模板 ${id}`);
    assert.ok(prompt.includes(template.displayName), `提示词缺 ${id} 的显示名`);
  }
  const listed = prompt.match(/^  - (\w+)（/gm) ?? [];
  assert.equal(listed.length, templates.size, '提示词列出的模板数与注册表不符');
});

test('提示词覆盖全部八种意图', () => {
  for (const kind of [
    'dispatch',
    'complete',
    'cancelPending',
    'setWeather',
    'build',
    'demolish',
    'miracle',
    'unknown',
  ]) {
    assert.ok(prompt.includes(`"${kind}"`), `提示词没讲 ${kind}`);
  }
});

test('提示词把 unknown 说成一等公民，而不是失败', () => {
  assert.match(prompt, /听不出就答 unknown/);
  assert.match(prompt, /宁可不动/);
});

test('提示词明确禁止解释与代码块', () => {
  assert.match(prompt, /只输出 JSON/);
  assert.match(prompt, /不要解释/);
});

test('问句与抱怨的正例都在，防止模型把闲聊当命令', () => {
  assert.match(prompt, /外面在下雨吗.*unknown/s);
  assert.match(prompt, /怎么写都不对.*unknown/s);
});

test('不接活的模板也被如实说明', () => {
  // workshop_plot 的 boundTask 是 null。说它「接某某活」会误导模型。
  assert.match(prompt, /workshop_plot（新工坊，不接活）/);
});

test('活类型四类都讲到', () => {
  for (const kind of ['code', 'research', 'writing', 'quick']) {
    assert.ok(prompt.includes(`- ${kind}：`), `提示词没讲 ${kind}`);
  }
});

test('消息只有 system + user 两条，user 就是原话', () => {
  const messages = buildIntentMessages(templates, '帮我写个爬虫');
  assert.equal(messages.length, 2);
  assert.equal(messages[0]?.role, 'system');
  assert.equal(messages[1]?.role, 'user');
  assert.equal(messages[1]?.content, '帮我写个爬虫', '不许改写用户原话');
});

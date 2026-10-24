import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseOracle } from '../src/world/oracle.ts';

const kind = (text: string): string => parseOracle(text).kind;

// ------------------------------------------------------------------ 祈使句：触发

test('句首是祈使动词时派活', () => {
  const cases: [string, string][] = [
    ['帮我写个爬虫', 'dispatch'],
    ['查一下天气', 'dispatch'],
    ['整理一下便笺', 'dispatch'],
    ['写一个脚本', 'dispatch'],
    ['搜一下这个报错', 'dispatch'],
    ['研究一下向量库', 'dispatch'],
  ];
  for (const [text, expected] of cases) {
    assert.equal(kind(text), expected, `「${text}」应当触发`);
  }
});

test('祈使动词可以带礼貌前缀', () => {
  for (const text of ['帮我写个脚本', '麻烦查一下', '请整理一下', '帮忙搜一下']) {
    assert.equal(kind(text), 'dispatch', `「${text}」应当触发`);
  }
});

test('世界法则的祈使句也触发', () => {
  assert.equal(kind('下雨吧'), 'weather');
  assert.equal(kind('放晴吧'), 'weather');
  assert.equal(kind('撤销'), 'cancelPending');
  assert.equal(kind('交付'), 'complete');
});

// ------------------------------------------------------------------ 叙述句：不触发

test('叙述性的句子不触发：这是这一条的核心', () => {
  const cases = [
    '今天天气不错，我想写点东西',
    '这个 bug 怎么写都不对',
    '我昨天查了半天也没查到',
    '写代码真累',
    '今天真好看',
    '外面在下雨吗',
    '刚才那个报错挺奇怪的',
  ];
  for (const text of cases) {
    assert.equal(kind(text), 'unknown', `「${text}」是叙述，不该触发世界动作`);
  }
});

test('疑问句不触发：问句不是命令', () => {
  for (const text of ['要不要写个脚本？', '能查一下吗', '写不写？']) {
    assert.equal(kind(text), 'unknown', `「${text}」是提问，不该触发`);
  }
});

test('否定句不触发', () => {
  for (const text of ['别写了', '不用查了', '不要整理']) {
    assert.equal(kind(text), 'unknown', `「${text}」是否定，不该触发`);
  }
});

// ------------------------------------------------------------------ 边界

test('空句子与纯空白不触发', () => {
  assert.equal(kind(''), 'unknown');
  assert.equal(kind('   '), 'unknown');
});

test('句中含关键词但句首不是祈使，不触发', () => {
  // 关键词在句中：旧实现会误判，新实现不该。
  assert.equal(kind('我觉得这个代码有点问题'), 'unknown');
  assert.equal(kind('那个资料我看过了'), 'unknown');
});

test('英文也按同一规则：祈使触发，叙述不触发', () => {
  assert.equal(kind('write a script'), 'dispatch');
  assert.equal(kind('I wrote a script yesterday'), 'unknown');
});

test('承接副词也算祈使：「再整理一份」是在下命令，不是叙述', () => {
  const cases: [string, string][] = [
    ['再整理一份便笺', 'dispatch'],
    ['继续查一下', 'dispatch'],
    ['接着写', 'dispatch'],
    ['顺便搜一下', 'dispatch'],
  ];
  for (const [text, expected] of cases) {
    assert.equal(kind(text), expected, `「${text}」应当触发`);
  }
});

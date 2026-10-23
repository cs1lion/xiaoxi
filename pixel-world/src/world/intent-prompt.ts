/** 意图提示词：把世界的能力讲给模型听。
 *
 * 为什么提示词是代码而不是配置：**它是 schema 的另一半**。
 * `intent-schema.ts` 规定了模型能说什么，这里规定了模型知道什么。
 * 两边必须同步——模板表改了、动词加了，提示词不跟着改，模型就会
 * 一直输出被校验器拒绝的东西。放在同一个测试套件里，改一边漏了另一边会红。
 *
 * 提示词里的模板表**从蓝图注册表现场生成**，不是手抄的常量。
 * 手抄必然漂移，而漂移的表现是"模型说了一个不存在的模板"——正是校验器
 * 要拒的那种错，也是最难查的那种（模型没错，是提示词在骗它）。
 */

import type { PrefabBlueprint } from './types.ts';
import type { TaskKind } from './types.ts';

/** 每类活的说明。模型靠它把「查一下资料」映射到 research。 */
const TASK_HINTS: Readonly<Record<TaskKind, string>> = {
  code: '写代码、写脚本、跑程序、爬虫',
  research: '查资料、搜东西、调研、研究',
  writing: '写文稿、整理便签、改稿、草拟',
  quick: '顺手的小事，不值得开一间铺子',
};

/**
 * 生成系统提示词。
 *
 * 三条纪律写进提示词，不只是写在文档里：
 *
 * 1. **听不出就答 unknown。** 模型最常见的失败不是答错，是硬猜。
 * 2. **不要解释。** 只输出 JSON，多一个字就多一次解析失败。
 * 3. **不要编造字段。** 校验器会拒，但拒一次就浪费一次往返。
 */
export function buildIntentPrompt(templates: ReadonlyMap<string, PrefabBlueprint>): string {
  const taskLines = (Object.keys(TASK_HINTS) as TaskKind[])
    .map((kind) => `  - ${kind}：${TASK_HINTS[kind]}`)
    .join('\n');

  const templateLines = [...templates.values()]
    .map((template) => {
      const takes = template.boundTask === null ? '不接活' : `接${TASK_HINTS[template.boundTask]}`;
      return `  - ${template.id}（${template.displayName}，${takes}）`;
    })
    .join('\n');

  return `你是像素世界里那个世界的**意图解析器**。用户对世界说一句话，你把它翻成一条命令。

## 你能表达的命令

只输出下面八种之一，别的都不存在：

- \`{"kind":"dispatch","taskKind":"<类>","summary":"<要做的事>"}\` 派一件活给镇上的铺子
- \`{"kind":"complete"}\` 交付，收下正在干的那单
- \`{"kind":"cancelPending"}\` 撤销还在排队的神谕（不动正在干的）
- \`{"kind":"setWeather","weather":"clear"|"rain"}\` 改天气
- \`{"kind":"build","templateId":"<模板>"}\` 建一栋楼；可带 \`"at":{"x":<整数>,"y":<整数>}\`
- \`{"kind":"demolish","templateId":"<模板>"}\` 拆掉一栋楼
- \`{"kind":"miracle","miracle":"light"|"tree"|"cat"}\` 点灯 / 种树 / 召唤猫；可带 \`"at"\`
- \`{"kind":"unknown","reason":"<为什么听不出>"}\` 这句话不是在要求世界做任何事

## 活分四类

${taskLines}

## 能建的模板（只有这些，不能发明）

${templateLines}

## 三条纪律

1. **听不出就答 unknown。** 闲聊、提问、感叹、抱怨——一律 unknown，并在 reason 里说清是什么。
   世界宁可不动，也不替用户做他没说的事。猜错比不猜伤害大得多。
2. **只输出 JSON。** 不要解释、不要 Markdown 代码块、不要前后缀。
3. **不要编造字段。** 每种命令能带的字段是固定的，多一个都会被拒。

## 判断的例子

| 用户说 | 你应该答 |
|---|---|
| 帮我写个爬虫 | dispatch / code |
| 查一下最近的 AI 新闻 | dispatch / research |
| 把便签整理一下 | dispatch / writing |
| 顺手看看有没有新邮件 | dispatch / quick |
| 建个工坊 | build / workshop_plot |
| 在 (40,45) 种棵树 | miracle / tree / at 40,45 |
| 那栋抄书斋拆了吧 | demolish / ink_house |
| 下雨吧 | setWeather / rain |
| 收工 | complete |
| 外面在下雨吗 | unknown（在提问，不是在改天气） |
| 这个 bug 怎么写都不对 | unknown（在抱怨，不是在派活） |
| 今天好累啊 | unknown（闲聊） |
| 你觉得该怎么办 | unknown（在征求意见） |

注意最后四行：**问句和抱怨都不是命令**。这是世界不撒谎的底线。`;
}

/**
 * 一段话的请求体。
 *
 * 温度给 0：意图解析要的是稳定，不是文采。同一句话两次问出不同答案的
 * 系统没法测试，也没法解释。
 */
export function buildIntentMessages(
  templates: ReadonlyMap<string, PrefabBlueprint>,
  utterance: string,
): { role: 'system' | 'user'; content: string }[] {
  return [
    { role: 'system', content: buildIntentPrompt(templates) },
    { role: 'user', content: utterance },
  ];
}

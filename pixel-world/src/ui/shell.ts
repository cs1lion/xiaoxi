/** 界面壳的开关：`?debug=1` 那一层，和神谕输入的快捷键。
 *
 * 产品页默认只有世界、神谕栏、在手清单和报纸。造物、神迹、存档、时辰、
 * 派活按钮、调试面板都活在 `?debug=1` 里——世界自己的浏览器回归脚本靠它们
 * 驱动，所以它们必须还在，只是不在产品界面上抢位置。
 *
 * 快捷键用 DOM `keydown`：Phaser 的键盘插件只在自己持有焦点时派发，
 * 焦点在输入框或按钮上时收不到。
 */

export function bindShell(): void {
  const params = new URLSearchParams(window.location.search);
  const maintenance = document.querySelector<HTMLElement>('#maintenance');
  const debug = params.get('debug') === '1' || params.get('debug') === '';
  if (maintenance !== null) maintenance.hidden = !debug;

  const input = document.querySelector<HTMLInputElement>('#utterance');
  if (input === null) return;

  document.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() !== 'k' || !(event.ctrlKey || event.metaKey)) return;
    // 浏览器自己的 Ctrl+K 是地址栏搜索，抢过来要挡默认行为。
    event.preventDefault();
    input.focus();
    input.select();
  });

  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    input.blur();
  });
}

/** 神谕面板：把 DOM 上的输入接到场景暴露的同一条通道。
 *
 * 「打字」和「点按钮」不是两条路径：按钮也拼一句话交给 Oracle.say，
 * 免得上层要维护两套意图解析。
 */

export interface OracleApi {
  /** 只表示这句话认没认出。世界是否照办在下一个逻辑拍结算，结果走 onResult。 */
  readonly say: (text: string) => { readonly ok: boolean; readonly message: string };
  readonly dispatch: (kind: 'code' | 'research' | 'writing', summary: string) => void;
  readonly finish: () => void;
  /** 命令结算结果。返回取消订阅函数。 */
  readonly onResult: (listener: (result: { ok: boolean; message: string }) => void) => () => void;
  /** 世界状态一行字。像素画面不画中文，这行归纸面。 */
  readonly onStatus: (listener: (line: string) => void) => () => void;
}

const BUTTON_TEXT: Record<string, string> = {
  code: '帮我写一段代码',
  research: '帮我查一下资料',
  writing: '帮我整理一下便笺',
};

export function bindOraclePanel(getApi: () => OracleApi | undefined): void {
  const form = document.querySelector<HTMLFormElement>('#oracle');
  const input = document.querySelector<HTMLInputElement>('#utterance');
  const reply = document.querySelector<HTMLDivElement>('#reply');
  const status = document.querySelector<HTMLDivElement>('#status');
  const finish = document.querySelector<HTMLButtonElement>('#finish');
  if (form === null || input === null || reply === null || status === null || finish === null) {
    return;
  }

  const show = (ok: boolean, message: string): void => {
    reply.textContent = message;
    reply.classList.toggle('bad', !ok);
  };

  // 场景在 create() 里才挂上 window.Oracle。等它出现再订阅一次结果流。
  const subscribe = (): void => {
    const api = getApi();
    if (api === undefined) {
      requestAnimationFrame(subscribe);
      return;
    }
    api.onResult((result) => show(result.ok, result.message));
    api.onStatus((line) => {
      status.textContent = line;
    });
  };
  subscribe();

  const submit = (text: string): boolean => {
    const api = getApi();
    if (api === undefined) return false;
    const trimmed = text.trim();
    if (trimmed.length === 0) return false;
    const parsed = api.say(trimmed);
    if (!parsed.ok) show(false, parsed.message);
    return parsed.ok;
  };

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (submit(input.value)) input.value = '';
  });

  for (const button of form.querySelectorAll<HTMLButtonElement>('button[data-kind]')) {
    button.addEventListener('click', () => {
      const kind = button.dataset.kind;
      if (kind !== 'code' && kind !== 'research' && kind !== 'writing') return;
      submit(BUTTON_TEXT[kind]);
    });
  }

  finish.addEventListener('click', () => submit('交付'));
}

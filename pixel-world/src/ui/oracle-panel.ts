/** 神谕面板：把 DOM 上的输入接到场景暴露的同一条通道。
 *
 * 「打字」和「点按钮」不是两条路径：按钮也拼一句话交给 Oracle.say，
 * 免得上层要维护两套意图解析。
 */

export interface OracleApi {
  /** 只表示这句话认没认出。世界是否照办在下一个逻辑拍结算，结果走 onResult。 */
  readonly say: (text: string) => { readonly ok: boolean; readonly message: string };
  readonly dispatch: (kind: 'code' | 'research' | 'writing' | 'quick', summary: string) => void;
  /** 人工交付；target 可选（角色 id 或名字），省略时收第一个在干活的人的单。 */
  readonly finish: (target?: string) => void;
  /** 存档：立即写单槽。 */
  readonly saveNow: () => { readonly ok: boolean; readonly message: string };
  /** 读档：刷新页面，由启动流程从单槽恢复。 */
  readonly loadSave: () => { readonly ok: boolean; readonly message: string };
  /** 清档：删除单槽。 */
  readonly clearSave: () => { readonly ok: boolean; readonly message: string };
  readonly build: (prefabId: string) => void;
  readonly feed: (message: {
    readonly type: 'task.assigned';
    readonly taskId: string;
    readonly taskKind: 'code' | 'research' | 'writing' | 'quick';
    readonly summary: string;
  }) => { readonly ok: boolean; readonly message: string };
  /** 命令结算结果。返回取消订阅函数。 */
  readonly onResult: (listener: (result: { ok: boolean; message: string }) => void) => () => void;
  /** 世界状态一行字。像素画面不画中文，这行归纸面。 */
  readonly onStatus: (listener: (line: string) => void) => () => void;
  /** 夜景纯渲染开关；不写入世界快照或台账。 */
  readonly setNight: (enabled: boolean) => void;
  readonly toggleNight: () => boolean;
  readonly nightEnabled: () => boolean;
  readonly report: () => ReportApi;
}

export interface ReportApi {
  readonly headline: string;
  readonly lines: readonly string[];
  readonly date: string;
}

const BUTTON_TEXT: Record<string, string> = {
  code: '帮我写一段代码',
  research: '帮我查一下资料',
  writing: '帮我整理一下便笺',
  quick: '顺手看一眼',
};

export function bindOraclePanel(getApi: () => OracleApi | undefined): void {
  const form = document.querySelector<HTMLFormElement>('#oracle');
  const input = document.querySelector<HTMLInputElement>('#utterance');
  const reply = document.querySelector<HTMLDivElement>('#reply');
  const status = document.querySelector<HTMLDivElement>('#status');
  const finish = document.querySelector<HTMLButtonElement>('#finish');
  const paperHeadline = document.querySelector<HTMLHeadingElement>('#paper-headline');
  const paperLines = document.querySelector<HTMLUListElement>('#paper-lines');
  const paperDate = document.querySelector<HTMLSpanElement>('#paper-date');
  if (
    form === null ||
    input === null ||
    reply === null ||
    status === null ||
    finish === null ||
    paperHeadline === null ||
    paperLines === null ||
    paperDate === null
  ) {
    return;
  }

  const paintPaper = (api: OracleApi): void => {
    const report = api.report();
    paperHeadline.textContent = report.headline;
    paperDate.textContent = report.date;
    paperLines.replaceChildren(
      ...report.lines.map((line) => {
        const item = document.createElement('li');
        item.textContent = line;
        return item;
      }),
    );
  };

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
    api.onResult((result) => {
      show(result.ok, result.message);
      paintPaper(api);
    });
    api.onStatus((line) => {
      status.textContent = line;
      paintPaper(api);
    });
    paintPaper(api);
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
      const text = kind === undefined ? undefined : BUTTON_TEXT[kind];
      if (text === undefined) return;
      submit(text);
    });
  }

  finish.addEventListener('click', () => submit('交付'));

  document.querySelector<HTMLButtonElement>('#save-now')?.addEventListener('click', () => {
    const api = getApi();
    if (api === undefined) return;
    const result = api.saveNow();
    show(result.ok, result.message);
  });
  document.querySelector<HTMLButtonElement>('#load-save')?.addEventListener('click', () => {
    getApi()?.loadSave();
  });
  document.querySelector<HTMLButtonElement>('#clear-save')?.addEventListener('click', () => {
    const api = getApi();
    if (api === undefined) return;
    const result = api.clearSave();
    show(result.ok, result.message);
  });

  const rain = document.querySelector<HTMLButtonElement>('#rain');
  const clear = document.querySelector<HTMLButtonElement>('#clear');
  rain?.addEventListener('click', () => submit('下雨了'));
  clear?.addEventListener('click', () => submit('放晴吧'));

  for (const button of form.querySelectorAll<HTMLButtonElement>('button[data-build]')) {
    button.addEventListener('click', () => {
      const api = getApi();
      const prefabId = button.dataset.build;
      if (api === undefined || prefabId === undefined) return;
      api.build(prefabId);
    });
  }
}

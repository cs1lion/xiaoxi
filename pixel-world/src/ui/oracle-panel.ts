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
  /** 神迹：点灯 / 种树 / 召唤猫。不是神谕，不刻石板。 */
  readonly miracle: (kind: 'light' | 'tree' | 'cat') => void;
  /** 造物模式：进入后世界暂停，选位置、回车确认、Esc 取消。 */
  readonly beginCreation: (prefabId: string) => { readonly ok: boolean; readonly message: string };
  readonly confirmCreation: () => { readonly ok: boolean; readonly message: string };
  readonly cancelCreation: () => { readonly ok: boolean; readonly message: string };
  readonly creation: () => { readonly prefabId: string; readonly ghostAt: unknown } | null;
  /** 渲染统计：绘制对象数与指令总量。 */
  readonly renderStats: () => {
    readonly graphicsObjects: number;
    readonly totalCommands: number;
    readonly largest: readonly number[];
  };
  /** 立刻把镜头中心对准世界坐标。 */
  readonly centerCameraOn: (x: number, y: number) => void;
  /** 只查询某格能不能建，不改变状态。 */
  readonly canPlaceAt: (cell: { readonly x: number; readonly y: number }) => boolean;
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
  /** 时相：显式指定某一刻（截图/调试），会停止自动推进。 */
  readonly setPhase: (phase: 'dawn' | 'noon' | 'dusk' | 'night') => void;
  readonly currentPhase: () => 'dawn' | 'noon' | 'dusk' | 'night';
  /** 真实时间此刻对应的刻。 */
  readonly realPhase: () => 'dawn' | 'noon' | 'dusk' | 'night';
  /** 是否按真实时间自动推进。 */
  readonly isAutoPhase: () => boolean;
  /** 恢复按真实本地时间推进。 */
  readonly setAutoPhase: () => void;
  /** 旧入口：setNight(true) 等于跳到夜，false 等于回到真实时间。 */
  readonly setNight: (enabled: boolean) => void;
  readonly toggleNight: () => boolean;
  readonly nightEnabled: () => boolean;
  readonly report: () => ReportApi;
  /** 神谕石板：只含真正改变过世界的话，旧在前。 */
  readonly slab: () => readonly { readonly raw: string; readonly receipt: string; readonly at: number }[];
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
  const slabCount = document.querySelector<HTMLSpanElement>('#slab-count');
  const slabLines = document.querySelector<HTMLOListElement>('#slab-lines');
  if (
    form === null ||
    input === null ||
    reply === null ||
    status === null ||
    finish === null ||
    paperHeadline === null ||
    paperLines === null ||
    paperDate === null ||
    slabCount === null ||
    slabLines === null
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

  /** 石板：最新的一条最显眼（放最上面），旧的往下压。 */
  const paintSlab = (api: OracleApi): void => {
    const records = [...api.slab()].reverse();
    slabCount.textContent = records.length === 0 ? '' : `${records.length} 条`;
    if (records.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '还没有刻过。说一句会让世界动的话。';
      slabLines.replaceChildren(empty);
      return;
    }
    slabLines.replaceChildren(
      ...records.map((record) => {
        const item = document.createElement('li');
        item.textContent = record.raw;
        const receipt = document.createElement('span');
        receipt.className = 'receipt';
        receipt.textContent = `　→ ${record.receipt}`;
        item.appendChild(receipt);
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
      paintSlab(api);
    });
    paintPaper(api);
    paintSlab(api);
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

  for (const button of form.querySelectorAll<HTMLButtonElement>('button[data-create]')) {
    button.addEventListener('click', () => {
      const api = getApi();
      const prefabId = button.dataset.create;
      if (api === undefined || prefabId === undefined) return;
      show(true, api.beginCreation(prefabId).message);
    });
  }

  for (const button of form.querySelectorAll<HTMLButtonElement>('button[data-miracle]')) {
    button.addEventListener('click', () => {
      const api = getApi();
      const kind = button.dataset.miracle;
      if (api === undefined || kind === undefined) return;
      api.miracle(kind as 'light' | 'tree' | 'cat');
    });
  }

  for (const button of form.querySelectorAll<HTMLButtonElement>('button[data-build]')) {
    button.addEventListener('click', () => {
      const api = getApi();
      const prefabId = button.dataset.build;
      if (api === undefined || prefabId === undefined) return;
      api.build(prefabId);
    });
  }
}

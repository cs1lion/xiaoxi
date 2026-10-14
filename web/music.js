// Explicit click-only original-song workflow. Never accepts provider credentials.
const MAX_BYTES = 32 * 1024 * 1024;
const MAX_BASE64 = 4 * Math.ceil(MAX_BYTES / 3);
const $ = id => document.getElementById(id);
let configured = false;
let controller = null;
let audioURL = null;

function updateControls() {
  $('create').disabled = !configured || !!controller || !$('permission').checked;
  $('stop').disabled = !controller;
}
function clearAudio() {
  $('audio').pause();
  $('audio').removeAttribute('src');
  $('audio').load();
  if (audioURL) URL.revokeObjectURL(audioURL);
  audioURL = null;
  $('result').hidden = true;
}
function decodeAudio(data) {
  if (!data || !['audio/mpeg', 'audio/wav', 'audio/flac', 'audio/ogg'].includes(data.mime)
      || typeof data.audio_base64 !== 'string' || !data.audio_base64.length
      || data.audio_base64.length > MAX_BASE64
      || data.audio_base64.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(data.audio_base64)) {
    throw new Error('invalid_audio');
  }
  const raw = atob(data.audio_base64);
  if (!raw.length || raw.length > MAX_BYTES) throw new Error('invalid_audio');
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return new Blob([bytes], { type: data.mime });
}
$('permission').addEventListener('change', updateControls);
$('delete').addEventListener('click', () => {
  clearAudio(); $('status').textContent = '已关闭并释放本地音频。';
});
$('stop').addEventListener('click', () => {
  controller?.abort();
  $('status').textContent = '已停止浏览器等待；不能取消服务商已提交任务，仍可能产生费用。请勿立即重复提交。';
});
$('musicForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (!configured || controller || !$('permission').checked) return;
  const caption = $('caption').value.trim(), lyrics = $('lyrics').value.trim();
  if (!caption || caption.length > 2000 || lyrics.length > 8000) {
    $('status').textContent = '请输入有效的描述，并遵守描述和歌词长度限制。'; return;
  }
  clearAudio();
  controller = new AbortController();
  updateControls();
  $('status').textContent = '正在异步生成原创音乐，可能需要数分钟…';
  try {
    const response = await fetch('/api/music/create', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caption, lyrics, user_confirmed: true }), signal: controller.signal,
    });
    if (!response.ok) {
      // Do not display arbitrary provider/error payloads, URLs, or secrets.
      const messages = { 409: '另一个音乐请求正在处理，请稍后再试。',
        503: '原创音乐服务未启用或配置无效。', 422: '请检查描述、歌词及使用权限确认。',
        504: '等待超时；服务商任务可能继续完成并产生费用。' };
      $('status').textContent = messages[response.status] || '音乐生成未完成；已提交的任务可能继续处理并产生费用。';
      return;
    }
    const data = await response.json();
    if (controller.signal.aborted) return;
    const blob = decodeAudio(data);
    audioURL = URL.createObjectURL(blob);
    $('audio').src = audioURL;
    $('result').hidden = false;
    $('status').textContent = '原创音乐已生成。请手动播放；音频只保留在本页。';
  } catch (error) {
    if (error.name !== 'AbortError') $('status').textContent = '无法获取有效音频；已提交的服务商任务可能仍在处理。';
  } finally {
    controller = null; updateControls();
  }
});
window.addEventListener('pagehide', () => { controller?.abort(); clearAudio(); });
try {
  const response = await fetch('/api/music/capabilities');
  if (!response.ok) throw new Error('capabilities_unavailable');
  const data = await response.json();
  configured = data.configured === true;
  $('capability').textContent = configured
    ? '原创音乐服务已配置。勾选权限确认后才可手动生成（可能收费）。'
    : '原创音乐服务未启用或未配置专用服务凭据。生成已禁用，不会调用服务商。';
} catch {
  $('capability').textContent = '无法确认音乐服务配置；为安全起见，生成已禁用。';
}
updateControls();

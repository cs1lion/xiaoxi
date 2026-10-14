const text=document.getElementById('text'),status=document.getElementById('status'),samples=document.getElementById('samples');
const urls=new Set();let busy=false;
const clear=()=>{for(const audio of samples.querySelectorAll('audio')){audio.pause();audio.removeAttribute('src');audio.remove();}for(const url of urls)URL.revokeObjectURL(url);urls.clear();samples.querySelectorAll('pre').forEach(p=>p.remove());};
text.oninput=()=>{clear();status.textContent='文字已改变，旧样本已清除。';};
window.addEventListener('pagehide',clear);
async function request(method,body){const r=await fetch('/api/voice-taste',{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});if(!r.ok)throw Error(r.status===404?'试音接口尚未加载，需要更新运行服务。':`试音请求失败（${r.status}），请稍后重试。`);return r.json();}
try{
 const info=await request('GET');document.getElementById('engine').textContent=`实际引擎：${info.engine} · 固定音色：${info.voice} · 可用控制：${info.controls}`;
 for(const style of info.styles){const card=document.createElement('article');const h=document.createElement('h2');h.textContent=style.label;const button=document.createElement('button');button.textContent='生成试听';button.onclick=async()=>{
  if(busy)return;const input=text.value.trim();if(!input){status.textContent='先写一句试音文字。';return;}
  busy=true;text.disabled=true;samples.querySelectorAll('button').forEach(b=>b.disabled=true);status.textContent=`正在生成${style.label}，没有自动播放…`;
  try{const data=await request('POST',{text:input,style:style.id});card.querySelector('audio')?.remove();card.querySelector('pre')?.remove();if(!data.audio){status.textContent='当前引擎返回空音频，无法试听。';return;}const bytes=Uint8Array.from(atob(data.audio),c=>c.charCodeAt(0));const mime={mp3:'audio/mpeg',wav:'audio/wav',opus:'audio/ogg',flac:'audio/flac',aac:'audio/aac'}[data.format]||'application/octet-stream';const url=URL.createObjectURL(new Blob([bytes],{type:mime}));urls.add(url);const audio=document.createElement('audio');audio.controls=true;audio.src=url;audio.onplay=()=>{samples.querySelectorAll('audio').forEach(other=>{if(other!==audio)other.pause();});};const pre=document.createElement('pre');pre.textContent=JSON.stringify({text:data.text,voice:data.voice,controls:data.controls,profile:data.profile},null,2);card.append(audio,pre);status.textContent=`${style.label}已生成。点击播放器试听；默认设置未改变。`;
  }catch(error){status.textContent=error.message;}finally{busy=false;text.disabled=false;samples.querySelectorAll('button').forEach(b=>b.disabled=false);}
 };card.append(h,button);samples.append(card);}
}catch(error){status.textContent=error.message;}

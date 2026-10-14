// Shared live workspace. Hide panels, never recreate editor documents on tab changes.
const mount=document.querySelector('#assistantMount');
if(mount){
 const shell=document.createElement('section');shell.className='shared-workspace panel';shell.setAttribute('aria-label','工作便笺');
 shell.innerHTML='<div role="tablist" aria-label="工作便笺内容"><button id="work-now-tab" role="tab" aria-controls="work-now" aria-selected="true">此刻</button><button id="work-notes-tab" role="tab" aria-controls="work-notes" aria-selected="false" tabindex="-1">笔记</button><button id="work-tasks-tab" role="tab" aria-controls="work-tasks" aria-selected="false" tabindex="-1">待办<span id="work-badge"></span></button></div><section id="work-now" role="tabpanel" aria-labelledby="work-now-tab"><p class="hint">正在读取可继续的事情…</p></section><section id="work-notes" role="tabpanel" aria-labelledby="work-notes-tab" hidden><iframe title="个人笔记编辑器" src="/static/notes.html?embedded=1"></iframe></section><section id="work-tasks" role="tabpanel" aria-labelledby="work-tasks-tab" hidden></section><p id="work-feedback" class="hint" role="status"></p>';
 mount.before(shell);shell.querySelector('#work-tasks').append(mount);
 const tabs=[...shell.querySelectorAll('[role=tab]')];
 function select(tab){tabs.forEach(t=>{const active=t===tab;t.setAttribute('aria-selected',String(active));t.tabIndex=active?0:-1;document.getElementById(t.getAttribute('aria-controls')).hidden=!active;});if(tab.id==='work-tasks-tab')shell.querySelector('#work-badge').textContent='';updateEditorVisibility();}
 tabs.forEach((tab,i)=>{tab.onclick=()=>select(tab);tab.onkeydown=e=>{const n=e.key==='ArrowRight'?(i+1)%3:e.key==='ArrowLeft'?(i+2)%3:e.key==='Home'?0:e.key==='End'?2:null;if(n!==null){e.preventDefault();select(tabs[n]);tabs[n].focus();}};});
 // Results stay discoverable without stealing focus from a note being edited.
 mount.addEventListener('assistant-result',()=>{shell.querySelector('#work-badge').textContent=' · 新';shell.querySelector('#work-feedback').textContent='待办中有新的行动确认或结果。';});
 const now=shell.querySelector('#work-now');
 const editor=shell.querySelector('iframe');
 const writingToggle=document.querySelector('#writingToggle');
 const side=document.querySelector('#sidePanel');
 let writing=false;
 const writingHeader=document.createElement('header');writingHeader.className='writing-header';writingHeader.hidden=true;
 const writingTitle=document.createElement('div');
 const writingHeading=document.createElement('h2');writingHeading.textContent='一起写';
 const writingHint=document.createElement('p');writingHint.textContent='你来写，阿栖偶尔提一个值得想的点。';
 writingTitle.append(writingHeading,writingHint);
 const returnToChat=document.createElement('button');returnToChat.type='button';returnToChat.textContent='回到聊天';returnToChat.className='ghost';
 writingHeader.append(writingTitle,returnToChat);shell.prepend(writingHeader);
 function tellEditor(type,detail){editor.contentWindow?.postMessage({type,...detail},location.origin);}
 function updateEditorVisibility(){
  tellEditor('aqi-editor-visibility',{active:!document.hidden&&!shell.querySelector('#work-notes').hidden&&editor.getClientRects().length>0});
 }
 function syncEditor(){tellEditor('aqi-editor-mode',{writing});updateEditorVisibility();}
 function setWriting(value){
  // CSS changes the existing editor's layout; reparenting its iframe can reload
  // its browsing context and discard the draft even if the node is retained.
  writing=value;document.body.classList.toggle('is-writing',value);writingHeader.hidden=!value;
  shell.setAttribute('aria-label',value?'一起写':'工作便笺');side?.setAttribute('aria-label',value?'一起写':'记忆与偏好');
  writingToggle?.setAttribute('aria-expanded',String(value));
  if(value){select(tabs[1]);returnToChat.focus({preventScroll:true});}
  else writingToggle?.focus({preventScroll:true});
  syncEditor();
 }
 if(writingToggle)writingToggle.onclick=()=>setWriting(!writing);
 window.addEventListener('aqi-editor-discussion-start',()=>setWriting(false));
 window.addEventListener('aqi-editor-discussion-return',()=>setWriting(true));
 returnToChat.onclick=()=>setWriting(false);
 editor.addEventListener('load',syncEditor);
 document.addEventListener('visibilitychange',updateEditorVisibility);
 if(side)new MutationObserver(updateEditorVisibility).observe(side,{attributes:true,attributeFilter:['class']});
 document.addEventListener('keydown',event=>{if(writing&&event.key==='Escape'&&!event.isComposing){event.preventDefault();setWriting(false);}});
 const draftNotice=document.createElement('p');draftNotice.id='work-note-draft';draftNotice.className='hint';draftNotice.setAttribute('role','status');draftNotice.hidden=true;now.after(draftNotice);
 let refreshRevision=0;
 function button(text,fn){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=fn;return b;}
 function paragraph(text,className=''){const p=document.createElement('p');p.textContent=text;if(className)p.className=className;return p;}
 function noteLink(text,id,version){const link=document.createElement('a');const query=new URLSearchParams({note_id:id});if(version!=null)query.set('version',String(version));link.href=`/static/notes.html?${query}`;link.textContent=text;return link;}
 function render(activity){
  now.replaceChildren();
  const selection=activity.selection||{},note=activity.note,thread=activity.thread;
  const inThread=activity.material_source?.scope==='thread';
  const materialsLink=()=>{const link=document.createElement('a');link.href=`/static/threads.html?${new URLSearchParams({thread_id:thread.id})}`;link.textContent='管理本事项资料';return link;};
  const title=document.createElement('h2');title.textContent=selection.note_id||thread?'接着上次的事情':'此刻，不必急着做什么';now.append(title);
  if(selection.note_id){
   if(selection.missing)now.append(paragraph(inThread?'本事项当前稿已被删除或不可用。请在事项资料中核对或解除关联。':'已关联的笔记已被删除或不可用。没有自动换成其他笔记；请到笔记页核对或取消关联。','hint'),inThread?materialsLink():button('查看笔记',()=>select(tabs[1])));
   else{
    now.append(paragraph(`${inThread?'本事项当前稿':'个人关联笔记'}：${note?.title||selection.note_id} · 保存版本 ${selection.selected_version}`));
    now.append(noteLink('打开已关联的保存版本',selection.note_id,selection.selected_version));
    if(selection.stale){
     now.append(paragraph(`当前已有保存版本 ${selection.current_version}；之后的对话仍关联版本 ${selection.selected_version}。${inThread?'请在本事项资料中明确更新版本。':'请载入新版后明确关联。'}`,'hint'));
     now.append(noteLink('查看当前保存版本并决定是否关联',selection.note_id,selection.current_version));
    }
   }
  }else now.append(paragraph(inThread?'本事项尚无当前稿。请在本事项资料中选定保存版本和用途。':'之后的对话尚未关联笔记。可以在笔记页打开一份保存内容，再明确关联。','hint'),inThread?materialsLink():button('查看笔记',()=>select(tabs[1])));
  if(inThread&&selection.note_id)now.append(materialsLink());
  if(thread){
   const statuses={discussing:'讨论中',waiting:'等待中',active:'推进中',done:'已完成',paused:'已暂停'};
   now.append(paragraph(`继续事项：${thread.title} · ${statuses[thread.status]||thread.status}`));
   if(thread.next_step)now.append(paragraph(`下一步：${thread.next_step}`,'hint'));
   now.append(button('查看事项',()=>document.querySelector('#companionMount')?.scrollIntoView({behavior:'smooth',block:'nearest'})));
  }
  if(activity.resume?.text)now.append(paragraph(activity.resume.text,'hint'));
  now.append(button('刷新此刻',refresh));
 }
 async function refresh(){
  const revision=++refreshRevision;
  try{const response=await fetch('/api/activity');if(!response.ok)throw Error('活动读取失败');const activity=await response.json();if(revision===refreshRevision)render(activity);}
  catch{if(revision!==refreshRevision)return;now.replaceChildren();now.append(paragraph('暂时读不到已关联的事项。聊天和笔记入口仍可使用。','hint'),button('重试',refresh));}
 }
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==editor.contentWindow)return;
  const message=event.data;
  if(message?.type==='aqi-editor-ready'){syncEditor();return;}
  if(message?.type==='aqi-editor-exit'&&writing){setWriting(false);return;}
  if(!message||!['aqi-notes-state','aqi-notes-updated'].includes(message.type))return;
  if(typeof message.dirty==='boolean'){draftNotice.hidden=!message.dirty;draftNotice.textContent=message.dirty?'笔记编辑器有未保存修改；此刻和之后的对话只使用已明确关联的保存版本。':'';}
  if(message.type==='aqi-notes-updated')refresh();
 });
 window.addEventListener('companion-updated',refresh);
 refresh();
}

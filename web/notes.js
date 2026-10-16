import { mountEditorInsights } from './editor-insights.js';

if(new URLSearchParams(location.search).get('embedded')==='1'){
 const style=document.createElement('style');style.textContent='main{padding:8px}header{display:none}.layout{display:block;margin-top:10px}.layout>.panel{margin-bottom:16px}body{background:transparent}main>p:not(#status){font-size:11px}textarea{min-height:130px}.panel{padding:10px}';document.head.append(style);
}
const el=id=>document.getElementById(id);
const status=text=>el('status').textContent=text;
let selected=null,dirty=false,pending=false,draft=null,currentVersion=null;
let selection=null,selectionReady=false;
const association=document.createElement('p');association.id='noteSelectionStatus';association.className='hint';association.setAttribute('role','status');
el('source').after(association);
const scopeNotice=document.createElement('p');scopeNotice.className='hint';scopeNotice.textContent='选定共同事项时，对话按该事项资料中的当前稿继续；请在事项页关联或更新版本。';association.after(scopeNotice);
const selectionLink=document.createElement('a');selectionLink.id='selectedNoteLink';selectionLink.hidden=true;association.after(selectionLink);
const actions=el('editor').querySelector('.actions');
function control(id,label){const button=document.createElement('button');button.id=id;button.type='button';button.textContent=label;actions.append(button);return button;}
const associate=control('associateNote','关联之后的对话');
const clearAssociation=control('clearNoteSelection','取消笔记关联');
const openCurrent=control('openCurrentNote','载入当前保存版本');
const refreshAssociation=control('refreshNoteSelection','刷新关联');
const historical=()=>!!selected&&Number.isInteger(currentVersion)&&selected.version!==currentVersion;
const editorInsights=mountEditorInsights({editor:el('editor'),getDraft:()=>({title:el('title').value,body:el('body').value,eligible:!historical()})});
const noteLibrary=document.querySelector('.layout>.panel');
const libraryHolder=document.createElement('div');libraryHolder.id='noteLibrary';noteLibrary.before(libraryHolder);libraryHolder.append(noteLibrary);
const libraryDetails=document.createElement('details');libraryDetails.className='note-library-collapsed';
const librarySummary=document.createElement('summary');librarySummary.textContent='找回你的资料';libraryDetails.append(librarySummary);
window.addEventListener('message',event=>{
 if(window.parent===window||event.origin!==location.origin||event.source!==window.parent||event.data?.type!=='aqi-editor-mode'||typeof event.data.writing!=='boolean')return;
 const writing=event.data.writing;document.body.classList.toggle('notes-writing-mode',writing);
 if(writing&&!libraryDetails.parentNode){libraryHolder.append(libraryDetails);libraryDetails.append(noteLibrary);}
 else if(!writing&&libraryDetails.parentNode){libraryHolder.append(noteLibrary);libraryDetails.remove();}
});
function noteUrl(id,version){const query=new URLSearchParams({note_id:id});if(version!=null)query.set('version',String(version));return `/static/notes.html?${query}`;}
function notifyParent(type){
 const detail={type,dirty,note_id:selected?.id||null,version:selected?.version||null};
 if(window.parent!==window)window.parent.postMessage(detail,location.origin);
}
function updateControls(){
 const old=historical();
 el('title').readOnly=old;el('body').readOnly=old;
 el('save').disabled=pending||old;el('delete').disabled=pending||!selected||old;
 el('prepare').disabled=pending||!selected||dirty||old;
 const already=selectionReady&&selection?.note_id===selected?.id&&selection?.selected_version===selected?.version;
 associate.disabled=pending||!selectionReady||!selected||dirty||old||already;
 associate.textContent=already?(old?'此历史保存版本已关联':'当前保存版本已关联'):selection?.note_id===selected?.id?'关联当前保存版本':'关联之后的对话';
 clearAssociation.disabled=pending||!selectionReady||!selection?.note_id;
 openCurrent.hidden=!old;openCurrent.disabled=pending;
 refreshAssociation.disabled=pending;
 selectionLink.hidden=!selectionReady||!selection?.note_id||!!selection.missing;
 if(!selectionLink.hidden){selectionLink.href=noteUrl(selection.note_id,selection.selected_version);selectionLink.textContent=`查看已关联的保存版本 ${selection.selected_version}`;}
 if(!selectionReady)association.textContent='暂时无法确认对话的笔记关联；可以继续编辑，刷新后再关联。';
 else if(!selection.note_id)association.textContent='之后的对话尚未关联笔记；打开、编辑或保存都不会自动关联。';
 else if(selection.missing)association.textContent='已关联的笔记已被删除或不可用；未自动换成其他笔记。可以明确取消关联。';
 else association.textContent=`之后的对话关联保存版本 ${selection.selected_version}${selection.stale?`；当前已有保存版本 ${selection.current_version}，需明确关联新版。`:'。'}`;
 if(old)association.textContent+=` 正在只读查看历史版本 ${selected.version}；当前保存版本是 ${currentVersion}。`;
 if(dirty)association.textContent+=' 编辑器有未保存修改，之后的对话仍只使用明确关联的保存版本。';
}
async function api(path,method='GET',body){
 const response=await fetch(`/api/${path}`,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const result=await response.json();
 if(!response.ok){const error=Error(response.status===404?'笔记接口尚不可用或笔记已被删除，请检查服务版本。':typeof result.detail==='string'?result.detail:'请求未完成');error.status=response.status;error.path=path;throw error;}
 return result;
}
async function run(fn){if(pending)return;pending=true;const controls=[...document.querySelectorAll('button')];const previous=controls.map(b=>b.disabled);controls.forEach(b=>b.disabled=true);updateControls();try{await fn();}catch(error){if(error.status===409&&error.path==='notes/selection')selectionReady=false;status(error.status===409&&error.path==='notes/selection'?'笔记关联已在其他窗口变更。此页输入已保留；请刷新关联后再决定。':error.message.includes('version conflict')?'内容已在其他窗口修改或删除。输入已保留，请重新载入后核对。':error.message);}finally{pending=false;controls.forEach((b,i)=>b.disabled=previous[i]);updateControls();}}
const allowDiscard=()=>!dirty||confirm('有未保存的修改，确定放弃吗？');
function source(){el('source').textContent=selected?`来源：${selected.source} · 保存版本 ${selected.version}`:'来源：用户手动保存';}
function load(note,version=note?.version){selected=note;currentVersion=version;el('title').value=note?.title||'';el('body').value=note?.body||'';source();el('editorHeading').textContent=historical()?'历史笔记（只读）':note?'编辑笔记':'新建笔记';el('taskDraft').hidden=true;draft=null;dirty=false;editorInsights.changed();updateControls();notifyParent('aqi-notes-state');}
async function openNote(id,version=null){
 const path=`notes/${encodeURIComponent(id)}`;
 const [note,current]=version==null?[await api(path),null]:await Promise.all([api(`${path}?version=${version}`),api(path)]);
 load(note,current?.version||note.version);status(historical()?'已打开指定的历史保存版本，仅供查看；没有改变对话关联。':'已载入保存版本；没有改变对话关联。');
}
async function readSelection(){
 try{const next=await api('notes/selection');if(!Number.isInteger(next.revision))throw Error('无法识别笔记关联状态，请刷新后重试。');selection=next;selectionReady=true;}
 catch(error){selectionReady=false;throw error;}
 finally{updateControls();}
}
async function search(){const data=await api(`notes?query=${encodeURIComponent(el('query').value)}`);el('list').replaceChildren();for(const note of data.items){const button=document.createElement('button');button.className='note';button.type='button';button.dataset.noteId=note.id;button.textContent=note.title;const small=document.createElement('small');small.textContent=note.excerpt;button.append(small);button.onclick=()=>{if(allowDiscard())run(async()=>{await openNote(note.id);await readSelection();});};el('list').append(button);}if(!data.items.length)el('list').textContent='没有匹配笔记。';}
el('searchForm').onsubmit=e=>{e.preventDefault();run(search);};
el('editor').addEventListener('input',()=>{dirty=true;updateControls();notifyParent('aqi-notes-state');notifyParent('aqi-notes-typing');});
el('editor').onsubmit=e=>{e.preventDefault();if(historical())return;run(async()=>{const payload={title:el('title').value,body:el('body').value};const note=selected?await api(`notes/${selected.id}`,'PATCH',{...payload,version:selected.version}):await api('notes','POST',payload);
 if(el('title').value===payload.title&&el('body').value===payload.body){load(note);status('笔记已保存。');}
 else{selected=note;currentVersion=note.version;source();dirty=true;updateControls();notifyParent('aqi-notes-state');status('提交时的内容已保存；之后输入的修改尚未保存。');}
 notifyParent('aqi-notes-updated');try{await Promise.all([search(),readSelection()]);}catch{status('已保存，但列表或关联刷新失败；输入仍保留。');}});};
el('new').onclick=()=>{if(allowDiscard()){load(null);status('开始新笔记。');el('title').focus();}};
el('delete').onclick=()=>{if(!selected||historical()||!confirm('删除此笔记无法撤销；已创建的任务不会被删除。确定？'))return;run(async()=>{await api(`notes/${selected.id}/delete`,'POST',{version:selected.version});load(null);status('笔记已删除。');notifyParent('aqi-notes-updated');try{await Promise.all([search(),readSelection()]);}catch{status('已删除，但列表或关联刷新失败。');}});};
el('prepare').onclick=()=>{if(dirty){status('请先保存笔记修改，再准备有明确版本来源的任务草稿。');return;}draft={noteId:selected.id,version:selected.version,requestId:crypto.randomUUID(),signature:null};el('taskTitle').value=selected.title;el('taskSource').textContent=`来源笔记：${selected.title} · 版本 ${selected.version}`;el('taskDraft').hidden=false;el('taskTitle').focus();status('仅准备草稿，尚未创建任务。');};
el('cancelTask').onclick=()=>{draft=null;el('taskDraft').hidden=true;status('已取消草稿，没有创建任务。');};
el('confirmTask').onclick=()=>run(async()=>{if(!draft)return;const title=el('taskTitle').value.trim();if(!title)throw Error('请填写具体任务标题。');const current=await api(`notes/${draft.noteId}`);if(current.version!==draft.version)throw Error('笔记来源 version conflict');if(draft.signature!==null&&draft.signature!==title)draft.requestId=crypto.randomUUID();draft.signature=title;const task=await api('tasks','POST',{title,description:`来源笔记 ${draft.noteId} · 版本 ${draft.version}（用户确认）`,request_id:draft.requestId});el('taskDraft').hidden=true;draft=null;status(`任务已创建：${task.title}。可返回阿栖的任务面板继续推进。`);});
associate.onclick=()=>{if(associate.disabled)return;run(async()=>{selection=await api('notes/selection','POST',{note_id:selected.id,version:selected.version,expected_revision:selection.revision});selectionReady=true;updateControls();notifyParent('aqi-notes-updated');status(`已明确关联保存版本 ${selection.selected_version}。未保存修改不会加入之后的对话。`);});};
clearAssociation.onclick=()=>{if(clearAssociation.disabled)return;run(async()=>{selection=await api('notes/selection','POST',{note_id:null,version:null,expected_revision:selection.revision});selectionReady=true;updateControls();notifyParent('aqi-notes-updated');status('已取消笔记关联；笔记和编辑草稿没有被删除。');});};
openCurrent.onclick=()=>{if(selected&&allowDiscard())run(()=>openNote(selected.id));};
refreshAssociation.onclick=()=>run(async()=>{await readSelection();status('已读取最新关联；编辑器和未保存修改保持原样。');});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
run(async()=>{
 await search();let selectionError=null;try{await readSelection();}catch(error){selectionError=error;}
 const query=new URLSearchParams(location.search),id=query.get('note_id'),rawVersion=query.get('version');
 if(id){const version=rawVersion==null?null:Number(rawVersion);if(version!==null&&(!Number.isInteger(version)||version<1))throw Error('保存版本无效，没有载入或关联笔记。');await openNote(id,version);}
 else if(selectionReady&&selection.note_id&&!selection.missing)await openNote(selection.note_id,selection.selected_version);
 else load(null);
 if(selectionError)status(selectionError.message);else if(selectionReady&&selection.missing)status('已关联的笔记不可用，请核对后取消关联。');
});

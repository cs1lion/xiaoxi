/* Explicit preference management, separate from reminder settings and authority. */
const dimensions = {
  humor: ['幽默方式', {off:'不调侃', light:'允许轻微调侃'}],
  detail: ['回复详略', {short:'简短', detailed:'详细'}],
  support_style: ['支持方式', {listen:'先听我说', help:'优先帮我解决'}],
  question_style: ['追问方式', {minimal:'尽量少追问', normal:'正常追问'}],
};
const statuses = {active:'生效', candidate:'待确认候选', conflicted:'存在冲突，暂不使用', expired:'已过期', superseded:'已被修订替代'};
const activities = {'':'所有场景', chat:'日常闲聊', work:'工作', writing:'写作', technical:'技术讨论'};
const create = (tag, text) => { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; return el; };
async function request(path, method='GET', body) {
  const res=await fetch(`/api/interaction/${path}`, {method, cache:'no-store', headers:{'Content-Type':'application/json'}, ...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await res.json(); if(!res.ok) throw new Error(typeof data.detail==='string'?data.detail:'请求失败'); return data;
}
export async function bootPreferences(mount) {
  if(mount.querySelector('[data-interaction-preferences]')) return;
  const panel=create('details'); panel.className='panel assistant-panel'; panel.dataset.interactionPreferences='true';
  panel.append(create('summary','相处偏好'));
  const hint=create('p','只影响相处方式，不授予工具权限。当前明确要求优先；关闭记忆时不会应用历史偏好。'); hint.className='assistant-hint'; panel.append(hint);
  const error=create('p'); error.className='assistant-error'; error.setAttribute('role','alert'); panel.append(error);
  const editor=create('form'); editor.className='assistant-section';
  const list=create('div'); list.className='assistant-list';
  let editing=null, pendingEvidence=null;
  function field(title, control) { const label=create('label'); label.className='assistant-field'; label.append(create('span',title),control); control.setAttribute('aria-label',title); editor.append(label); return control; }
  function select(options) { const el=create('select'); for(const [key,label] of Object.entries(options)){const opt=create('option',label); opt.value=key; el.append(opt);}return el; }
  const dimension=field('偏好类型', select(Object.fromEntries(Object.entries(dimensions).map(([k,v])=>[k,v[0]]))));
  const value=field('偏好选择',select({}));
  function choices(){value.replaceChildren();for(const [k,v] of Object.entries(dimensions[dimension.value][1])){const opt=create('option',v);opt.value=k;value.append(opt);}}
  dimension.onchange=choices; choices();
  const activity=field('适用场景',select(activities));
  const evidence=field('我的偏好说明',create('textarea')); evidence.required=true; evidence.maxLength=2000;
  const expires=field('失效时间（可选，本地时间）',create('input')); expires.type='datetime-local';
  const submit=create('button','保存偏好'); submit.type='submit';
  const cancel=create('button','取消修订'); cancel.type='button'; cancel.hidden=true;
  editor.append(submit,cancel); panel.append(editor);
  function reset(){editing=null;pendingEvidence=null;dimension.disabled=false;activity.disabled=false;expires.disabled=false;submit.textContent='保存偏好';cancel.hidden=true;evidence.value='';expires.value='';}
  cancel.onclick=reset;
  function action(label, callback){const button=create('button',label);button.type='button';button.onclick=async()=>{button.disabled=true;try{await callback();error.textContent='';}catch(e){error.textContent=e.message;}finally{button.disabled=false;}};return button;}
  async function refresh(){
    const data=await request('preferences'); list.replaceChildren();
    if(!data.items.length) list.append(create('p','暂无相处偏好。你可以明确设置，不会从沉默或短回复猜测。'));
    for(const claim of data.items){
      const card=create('section');card.className='assistant-card';card.dataset.claimId=claim.id;
      card.append(create('h4',`${dimensions[claim.dimension]?.[0]||claim.dimension}：${dimensions[claim.dimension]?.[1][claim.value]||claim.value}`));
      card.append(create('p',statuses[claim.status]||claim.status));
      const scope=claim.scope||{};
      card.append(create('p',`范围：${activities[scope.activity||'']||scope.activity}${scope.session_id?' · 指定会话 '+scope.session_id:''}${scope.topic?' · 主题 '+scope.topic:''}`));
      card.append(create('small',`来源：${{explicit_user:'用户明确设置',manual:'手动设置',inferred:'推测候选'}[claim.source]||claim.source}${claim.valid_until?'；到期 '+new Date(claim.valid_until*1000).toLocaleString():''}`));
      if(claim.status==='superseded') card.append(create('small','已被明确变化退役'));
      else if(!claim.valid_until) card.append(create('small','未确认延续至今，未设定过期日'));
      const details=create('div');card.append(details);
      card.append(action('查看来源证据',async()=>{
        details.replaceChildren();
        for(const id of claim.evidence_ids){
          const item=await request(`evidence/${id}`);
          const block=create('div');block.append(create('p',item.text),create('small',new Date(item.created_at*1000).toLocaleString()));
          block.append(action('删除此证据及关联偏好',async()=>{
            if(!confirm('删除此说明及所有依赖它的偏好。不会删除独立聊天日志或备份，确定？')) return;
            await request(`evidence/${id}/delete`,'POST',{});reset();await refresh();
          }));details.append(block);
        }
      }));
      if(['active','conflicted'].includes(claim.status)) card.append(action('修订',async()=>{
        reset();editing=claim;dimension.value=claim.dimension;choices();value.value=claim.value;activity.value=scope.activity||'';
        dimension.disabled=true;activity.disabled=true;expires.disabled=true;submit.textContent='确认修订';cancel.hidden=false;
        panel.open=true;evidence.focus();error.textContent='修订保留原有全部范围和失效时间，请填写新的明确说明。';
      }));
      card.append(action('删除偏好',async()=>{
        if(!confirm('只删除这条偏好，原始说明保留；同范围冲突中的其他偏好可能恢复生效。确定？')) return;
        await request(`preferences/${claim.id}/delete`,'POST',{});reset();await refresh();
      })); list.append(card);
    }
  }
  editor.onsubmit=async event=>{
    event.preventDefault(); if(!evidence.value.trim()) return;
    submit.disabled=true;cancel.disabled=true;
    try{
      const text=evidence.value.trim();
      const until=expires.value?new Date(expires.value).getTime()/1000:null;
      if(until!==null&&!Number.isFinite(until)) throw new Error('请选择有效时间');
      if(!pendingEvidence||pendingEvidence.text!==text) pendingEvidence=await request('evidence','POST',{text});
      if(editing) await request(`preferences/${editing.id}/revise`,'POST',{value:value.value,evidence_ids:[pendingEvidence.id]});
      else await request('preferences','POST',{dimension:dimension.value,value:value.value,evidence_ids:[pendingEvidence.id],scope:activity.value?{activity:activity.value}:{},valid_until:until});
      reset();await refresh();error.textContent='';
    }catch(e){error.textContent=e.message;}finally{submit.disabled=false;cancel.disabled=false;}
  };
  panel.append(action('刷新偏好',refresh),list);mount.append(panel);
  try{await refresh();}catch(e){error.textContent=e.message;}
}

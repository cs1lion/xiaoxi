// Runtime facts, not inferred user emotion. Playback decoding is not speaking.
export function interactionState({recording,manual,playing,queued,turn,continuous,ducked}) {
 if(ducked&&playing)return {id:'yielding',text:'听到你了 · 继续说可打断',tone:'busy'};
 if(recording)return {id:'listening',text:manual?'录音中 · 松开结束':'听你说 · 停顿后发送',tone:'busy'};
 if(playing)return {id:'speaking',text:'正在说 · 可停止回复',tone:'busy'};
 if(queued)return {id:'preparing',text:'正在准备语音…',tone:'busy'};
 if(turn)return {id:'thinking',text:'等待回复…',tone:'busy'};
 return continuous?{id:'monitoring',text:'连续对话 · 本地监听',tone:'on'}:{id:'idle',text:'在线',tone:'on'};
}
export function reflectInteractionState(id,text){
 const stage=document.getElementById('avatarStage');if(stage)stage.dataset.interaction=id;
 const label=document.getElementById('interactionState');if(label)label.textContent=text;
}

const tabs=[...document.querySelectorAll('[role=tab]')];
function select(tab){for(const item of tabs){const active=item===tab;item.setAttribute('aria-selected',String(active));item.tabIndex=active?0:-1;document.getElementById(item.getAttribute('aria-controls')).hidden=!active;}}
for(const [index,tab] of tabs.entries()){
 tab.addEventListener('click',()=>select(tab));
 tab.addEventListener('keydown',event=>{let target;if(event.key==='ArrowRight')target=tabs[(index+1)%tabs.length];if(event.key==='ArrowLeft')target=tabs[(index+tabs.length-1)%tabs.length];if(event.key==='Home')target=tabs[0];if(event.key==='End')target=tabs.at(-1);if(target){event.preventDefault();select(target);target.focus();}});
}
const draft=document.querySelector('#draft'),feedback=document.querySelector('#feedback');
for(const button of document.querySelectorAll('[data-draft]'))button.onclick=()=>{draft.value=button.dataset.draft;feedback.textContent='已准备草稿，可以改写。没有自动发送。';draft.focus();};
for(const checkbox of document.querySelectorAll('.task-line input'))checkbox.onchange=()=>{document.querySelector('#count').textContent=document.querySelectorAll('.task-line input:not(:checked)').length;feedback.textContent='仅更新演示待办，切换标签会保留。';};
document.querySelector('#send').onclick=()=>{feedback.textContent=draft.value.trim()?'已预览发送反馈，没有请求模型。':'先写一句话，或选择一个聊天开头。';};

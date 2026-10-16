// Follow new text only while the reader stays near the end of the conversation.
export function installChatScroll(chat, button) {
  let following = true;
  // 欢迎屏（还没有消息）不跟底：跟底会把首屏标题滚出可视区顶边。有消息后恢复跟随。
  const hasConversation = () => chat.querySelector(".msg") != null;
  const nearEnd = () => chat.scrollHeight - chat.clientHeight - chat.scrollTop < 64;
  const goLatest = () => {
    chat.scrollTo({ top: chat.scrollHeight, behavior: 'instant' });
    following = true;
    button.hidden = true;
  };
  chat.addEventListener('scroll', () => {
    following = nearEnd();
    // 没有对话时（欢迎屏）不显示「回到最新」——它只属于对话流
    button.hidden = !hasConversation() || following;
  }, { passive: true });
  button.addEventListener('click', () => {
    goLatest();
    document.querySelector('#textInput').focus({ preventScroll: true });
  });
  new MutationObserver(() => {
    if (following && hasConversation()) goLatest();
    else button.hidden = !hasConversation() || nearEnd();
  }).observe(chat, { childList: true, subtree: true, characterData: true });
  new ResizeObserver(() => { if (following && hasConversation()) goLatest(); }).observe(chat);
}

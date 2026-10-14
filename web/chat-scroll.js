// Follow new text only while the reader stays near the end of the conversation.
export function installChatScroll(chat, button) {
  let following = true;
  const nearEnd = () => chat.scrollHeight - chat.clientHeight - chat.scrollTop < 64;
  const goLatest = () => {
    chat.scrollTo({ top: chat.scrollHeight, behavior: 'instant' });
    following = true;
    button.hidden = true;
  };
  chat.addEventListener('scroll', () => {
    following = nearEnd();
    button.hidden = following;
  }, { passive: true });
  button.addEventListener('click', () => {
    goLatest();
    document.querySelector('#textInput').focus({ preventScroll: true });
  });
  new MutationObserver(() => {
    if (following) goLatest();
    else button.hidden = nearEnd();
  }).observe(chat, { childList: true, subtree: true, characterData: true });
  new ResizeObserver(() => { if (following) goLatest(); }).observe(chat);
}

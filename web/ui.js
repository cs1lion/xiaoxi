// Presentation only: suggestions prepare a draft; they never send on your behalf.
const input = document.querySelector('#textInput');
const side = document.querySelector('#sidePanel');
const toggle = document.querySelector('#sidebarToggle');
const setSidebar = open => {
  side.classList.toggle('is-open', open);
  toggle.setAttribute('aria-expanded', String(open));
};
toggle.addEventListener('click', () => setSidebar(!side.classList.contains('is-open')));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && side.classList.contains('is-open')) {
    setSidebar(false); toggle.focus();
  }
});
document.addEventListener('pointerdown', event => {
  if (side.classList.contains('is-open') && !side.contains(event.target) && !toggle.contains(event.target)) setSidebar(false);
});
document.querySelectorAll('[data-prompt]').forEach(button => {
  button.addEventListener('click', () => {
    input.value = button.dataset.prompt;
    document.querySelectorAll('[data-prompt]').forEach(item => item.classList.toggle('draft-picked', item === button));
    document.querySelector('#draftFeedback').textContent = '已放入输入框，可以改写。按发送后才会开始对话。';
    input.focus();
  });
});
input.addEventListener('input', () => {
  document.querySelectorAll('[data-prompt]').forEach(item => item.classList.remove('draft-picked'));
  document.querySelector('#draftFeedback').textContent = '从一句「嗨」开始，也很好。';
});
import { installChatScroll } from './chat-scroll.js';
const chat = document.querySelector('#chat');
installChatScroll(chat, document.querySelector('#latestMessage'));
const welcome = document.querySelector('#welcome');
new MutationObserver(() => {
  const hasMessages = !!chat.querySelector('.msg');
  welcome.hidden = hasMessages;
  document.querySelector('.stage').classList.toggle('has-messages', hasMessages);
}).observe(chat, { childList:true });
// Explicit result events work even when details is already open; polling must not reopen the sidebar.
document.querySelector('#assistantMount').addEventListener('assistant-result', event => {
  if (matchMedia('(max-width:760px)').matches) setSidebar(true);
  const card = event.detail.card;
  card.tabIndex = -1;
  requestAnimationFrame(() => {
    card.scrollIntoView({ block:'nearest', behavior:'instant' });
    card.focus({ preventScroll:true });
  });
});

/* Presentation intent is an explicit animation choice, not a diagnosis of the
 * speaker. Auto follows only known emotion labels; it never inspects text.
 * These coefficients are tuning defaults, not biological timing claims.
 */
export const DIALOGUE_INTENTS = Object.freeze([
  Object.freeze({ id: 'neutral', label: '自然' }),
  Object.freeze({ id: 'serious', label: '认真' }),
  Object.freeze({ id: 'hesitant', label: '犹豫' }),
  Object.freeze({ id: 'teasing', label: '调侃' }),
]);

export const DIALOGUE_INTENT_PROFILES = Object.freeze({
  neutral: Object.freeze({ gazeScale: 1, delayScale: 1, durationScale: 1,
    budgetMax: 4, driftScale: 1 }),
  serious: Object.freeze({ gazeScale: .65, delayScale: 1.15, durationScale: .8,
    budgetMax: 1, driftScale: .65 }),
  hesitant: Object.freeze({ gazeScale: .9, delayScale: 1.6, durationScale: 1.05,
    budgetMax: 2, driftScale: .8 }),
  teasing: Object.freeze({ gazeScale: .85, delayScale: 1.1, durationScale: .9,
    budgetMax: 2, driftScale: .85 }),
});

export function resolveDialogueIntent(options = {}) {
  const { intent = 'auto', emotion = 'base' } = options && typeof options === 'object' ? options : {};
  if (typeof intent === 'string' && Object.hasOwn(DIALOGUE_INTENT_PROFILES, intent)) return intent;
  if (intent !== 'auto') return 'neutral';
  if (emotion === '认真') return 'serious';
  if (emotion === '调侃') return 'teasing';
  return 'neutral';
}

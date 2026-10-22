/* Show / hide password: every password field gets an eye button.
   Press it to show the password, press again to hide it. Fields are enhanced automatically
   as the app renders them (login, sign-up, password change, API key). Before a form is sent
   every field goes back to hidden, so the browser and password managers still see a password. */
(function () {
  const L = (ar, en) => (window.StudyI18n?.lang() === 'en' ? en : ar);
  const EYE = '<svg class="eye-on" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>' +
    '<svg class="eye-off" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

  function label(shown) { return shown ? L('إخفاء الباسورد', 'Hide password') : L('إظهار الباسورد', 'Show password'); }

  function enhance(input) {
    if (input.dataset.pw !== undefined || input.dataset.noReveal !== undefined) return;
    input.dataset.pw = '';
    const wrap = document.createElement('span');
    wrap.className = 'password-wrap';
    input.before(wrap);
    wrap.append(input);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-toggle';
    btn.dataset.pwToggle = '';
    btn.dataset.noI18n = '';
    btn.setAttribute('aria-pressed', 'false');
    btn.setAttribute('aria-label', label(false));
    if (input.id) btn.setAttribute('aria-controls', input.id);
    btn.innerHTML = EYE;
    wrap.append(btn);
  }

  function toggle(btn, force) {
    const input = btn.parentElement?.querySelector('input[data-pw]');
    if (!input) return;
    const show = typeof force === 'boolean' ? force : input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(show));
    btn.setAttribute('aria-label', label(show));
  }

  function scan(root) {
    if (root.nodeType !== 1) return;
    if (root.matches('input[type=password]')) enhance(root);
    root.querySelectorAll('input[type=password]').forEach(enhance);
  }

  new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(scan)))
    .observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', () => scan(document.body));

  document.addEventListener('click', event => {
    const btn = event.target.closest?.('[data-pw-toggle]');
    if (!btn) return;
    event.preventDefault();
    const input = btn.parentElement?.querySelector('input[data-pw]');
    toggle(btn);
    input?.focus({ preventScroll: true });
  });
  // Hide again before the form is sent (capture phase runs before the app's submit handler).
  document.addEventListener('submit', event => {
    event.target.querySelectorAll?.('[data-pw-toggle]').forEach(btn => toggle(btn, false));
  }, true);

  window.StudyPassword = { enhance, toggle, scan };
})();

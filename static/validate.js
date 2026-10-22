/* Form validation with clear messages under each field.
   A field is checked when the user leaves it (not on the first keystroke); once it shows an error it is
   re-checked on every keystroke so the message disappears as soon as it is fixed. On submit every field
   is checked, invalid ones are marked together and the first one gets focus.
   Secrets (the faculty code, invite code, passwords against the server) are only checked by the server:
   their server errors are shown under the matching field. */
(function () {
  const L = (ar, en) => (window.StudyI18n?.lang() === 'en' ? en : ar);
  const FIELDS = 'input:not([type=hidden]):not([type=checkbox]):not([type=file]),select,textarea';
  const touched = new WeakSet();

  function message(el) {
    const v = el.validity, value = el.value || '';
    if (v.valueMissing) return el.tagName === 'SELECT' ? L('اختار من القايمة', 'Choose an option') : L('الخانة دي مطلوبة', 'This field is required');
    if (el.id === 'su-user' || (el.name === 'username' && el.pattern)) {
      if (/\s/.test(value)) return L('من غير مسافات', 'No spaces');
      if (/[^\x00-\x7F]/.test(value)) return L('بالحروف الإنجليزية بس', 'English letters only');
      if (value.length < 3) return L(`لسه ${value.length} من ٣ حروف على الأقل`, `${value.length} of at least 3 characters`);
      if (!/^[A-Za-z0-9_.\-]+$/.test(value)) return L('حروف إنجليزي وأرقام و _ . - بس', 'Only letters, numbers and _ . -');
    }
    if (v.tooShort) {
      const min = el.minLength;
      return (el.type === 'password' || el.dataset.pw !== undefined)
        ? L(`الباسورد لسه قصير: ${value.length} من ${min}`, `Password too short: ${value.length} of ${min}`)
        : L(`لسه ${value.length} من ${min} حروف على الأقل`, `${value.length} of at least ${min} characters`);
    }
    if (v.tooLong) return L(`أقصى طول ${el.maxLength} حرف`, `At most ${el.maxLength} characters`);
    if (v.typeMismatch) return el.type === 'url' ? L('اكتب لينك صحيح يبدأ بـ https://', 'Enter a full link starting with https://') : L('الصيغة مش صحيحة', 'This format is not valid');
    if (v.rangeUnderflow) return L(`أقل قيمة ${el.min}`, `Minimum is ${el.min}`);
    if (v.rangeOverflow) return L(`أكبر قيمة ${el.max}`, `Maximum is ${el.max}`);
    if (v.badInput) return L('القيمة دي مش صحيحة', 'This value is not valid');
    if (v.patternMismatch) return L('الصيغة مش صحيحة', 'This format is not valid');
    if (el.dataset.serverError) return el.dataset.serverError;
    return '';
  }

  function slot(el) {
    const id = 'err-' + (el.id || el.name || Math.random().toString(36).slice(2));
    let box = document.getElementById(id);
    if (!box) {
      box = document.createElement('small');
      box.className = 'field-error'; box.id = id; box.setAttribute('role', 'alert'); box.dataset.noI18n = '';
      const inline = el.closest('.form-inline');
      const field = el.closest('.field');
      if (inline) inline.after(box);
      else if (field) field.append(box);
      else (el.closest('.password-wrap') || el).after(box);
    }
    return box;
  }

  function show(el, text) {
    const box = slot(el);
    box.textContent = text;
    box.hidden = !text;
    el.toggleAttribute('aria-invalid', !!text);
    if (text) el.setAttribute('aria-describedby', box.id); else el.removeAttribute('aria-describedby');
    const hint = box.nextElementSibling?.matches?.('small.muted') ? box.nextElementSibling : null;
    if (hint) hint.hidden = !!text;
    el.classList.toggle('is-valid', !text && touched.has(el) && !!el.value && el.type !== 'password' && el.dataset.pw === undefined);
  }

  function check(el) {
    if (!el.willValidate) return true;
    const text = message(el);
    show(el, text);
    return !text;
  }

  // Own the messages instead of the browser's tooltip.
  const own = root => root.querySelectorAll?.('form').forEach(f => { f.noValidate = true; });
  new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && (n.tagName === 'FORM' ? (n.noValidate = true) : own(n)))))
    .observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', () => own(document));

  document.addEventListener('focusout', e => {
    const el = e.target;
    if (!el.matches?.(FIELDS) || !el.form) return;
    if (!el.value && !touched.has(el)) return;          // just tabbing through an empty field
    touched.add(el); check(el);
  }, true);

  document.addEventListener('input', e => {
    const el = e.target;
    if (!el.matches?.(FIELDS) || !el.form) return;
    delete el.dataset.serverError;
    if (el.hasAttribute('aria-invalid') || touched.has(el)) check(el);
  }, true);
  document.addEventListener('change', e => { const el = e.target; if (el.matches?.('select') && el.form && el.hasAttribute('aria-invalid')) check(el); }, true);

  // Runs before the app's submit handler (capture on document beats its bubbling listener).
  document.addEventListener('submit', e => {
    const form = e.target;
    let first = null;
    form.querySelectorAll(FIELDS).forEach(el => {
      if (el.closest('[hidden]')) return;
      touched.add(el);
      if (!check(el) && !first) first = el;
    });
    if (first) {
      e.preventDefault(); e.stopImmediatePropagation();
      first.scrollIntoView({ block: 'center', behavior: 'smooth' });
      first.focus({ preventScroll: true });
    }
  }, true);

  // Server answers that belong to one field (wrong faculty code, taken username…).
  const SERVER = [
    [/كود الكلية/, '#su-college,[name=college_code]', 'Wrong faculty code. Ask your class group for it, or leave it empty'],
    [/كود الدعوة/, '#su-code', 'Wrong invite code. Ask the site owner for it'],
    [/اسم المستخدم ده محجوز/, '#su-user', 'This username is taken, try another one'],
    [/اسم المستخدم من/, '#su-user', '3 to 32 English letters, numbers or . _ -'],
    [/باسورد أصعب/, '#su-pass,#pw-new', 'Choose a stronger password'],
    [/الباسورد الحالي|current password/i, '#pw-old', 'Your current password is wrong'],
    [/سنتك/, '#su-level,[name=level]', 'Choose your level (1 to 4)'],
    [/البرنامج/, '#su-program,[name=program]', 'Choose your program'],
  ];
  function serverError(form, text) {
    for (const [pattern, selector, en] of SERVER) {
      if (!pattern.test(text || '')) continue;
      const el = form.querySelector(selector);
      if (!el) continue;
      el.dataset.serverError = L(text, en);
      touched.add(el); show(el, el.dataset.serverError);
      el.focus();
      return true;
    }
    return false;
  }

  window.StudyValidate = { check, serverError, message };
})();

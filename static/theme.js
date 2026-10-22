/* StudyHub colour themes: هادي (sage, default) · وردي (rose) · لافندر (lavender).
   Loaded synchronously in <head> so the saved theme is on <html> before the first paint.
   The choice is stored per device, like the UI language. */
(function () {
  const THEMES = {
    sage: { ar: 'هادي', en: 'Calm', color: '#F7F6F3' },
    rose: { ar: 'وردي', en: 'Rose', color: '#FFF6F7' },
    lavender: { ar: 'لافندر', en: 'Lavender', color: '#F7F3FC' },
  };
  const KEY = 'studyhub-theme';
  const valid = name => Object.prototype.hasOwnProperty.call(THEMES, name);
  let current = 'sage';
  try { const saved = localStorage.getItem(KEY); if (valid(saved)) current = saved; } catch {}

  function apply(name) {
    const html = document.documentElement;
    if (name === 'sage') html.removeAttribute('data-theme'); else html.setAttribute('data-theme', name);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', THEMES[name].color);
    document.querySelectorAll('[data-theme-set]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themeSet === name)));
  }

  function switcher() {
    const en = window.StudyI18n?.lang() === 'en';
    const buttons = Object.entries(THEMES).map(([id, t]) =>
      `<button type="button" data-theme-set="${id}" aria-pressed="${id === current}" title="${en ? t.en : t.ar}"><span class="theme-dot ${id}" aria-hidden="true"></span><span class="theme-name">${en ? t.en : t.ar}</span></button>`).join('');
    return `<div class="theme-switch" role="group" aria-label="${en ? 'Theme' : 'الثيم'}" data-no-i18n>${buttons}</div>`;
  }

  apply(current);
  document.addEventListener('DOMContentLoaded', () => apply(current));
  document.addEventListener('click', event => {
    const b = event.target.closest?.('[data-theme-set]');
    if (b) window.StudyTheme.set(b.dataset.themeSet);
  });

  window.StudyTheme = {
    list: () => Object.keys(THEMES),
    get: () => current,
    switcher,
    set(name) {
      if (!valid(name)) return false;
      current = name;
      try { localStorage.setItem(KEY, name); } catch {}
      apply(name);
      return true;
    },
  };
})();

"""Browser tests (Playwright): every page and dialog, Arabic and English, desktop and phone.
Fails on any JavaScript error, failed request (5xx) or a page that renders "undefined"/"NaN".
Skipped automatically when Playwright isn't installed."""
import os
import re
import socket
import subprocess
import sys
import time

import pytest

from .conftest import ROOT

playwright = pytest.importorskip('playwright.sync_api')
ARABIC = re.compile(r'[؀-ۿ]{2,}')
PAGES = ['home', 'day', 'courses', 'tasks', 'schedule', 'weekly', 'library', 'settings']


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def server(tmp_path_factory, request):
    """A real uvicorn server in friends mode, with the fake board from conftest wired in."""
    data = tmp_path_factory.mktemp('e2e')
    board_page = (ROOT / 'studyhub' / 'tests' / 'fixtures' / 'board' / 'main.html')
    port = free_port()
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'STUDY_MULTIUSER': '1', 'STUDY_DATA_DIR': str(data), 'STUDY_SECURE_COOKIE': 'false',
           'STUDY_REMINDERS_ENABLED': 'false', 'STUDY_COLLEGE_CODE': 'cs', 'STUDY_MIN_FREE_MB': '1', 'STUDY_RATE_LIMIT': '100000',
           'STUDY_BOARD_SYNC': '0', 'STUDY_SIGNUPS_PER_IP_HOUR': '1000'}
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'studyhub.app:app', '--port', str(port)], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f'http://127.0.0.1:{port}'
    process.terminate()
    process.wait(10)


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get('STUDY_CHROME') or None)
        yield b
        b.close()


def watch(page):
    problems = []
    page.on('pageerror', lambda e: problems.append(f'JS error: {e}'))
    page.on('console', lambda m: problems.append(f'console {m.type}: {m.text}') if m.type == 'error' and 'favicon' not in m.text else None)
    page.on('response', lambda r: problems.append(f'HTTP {r.status} {r.url}') if r.status >= 500 else None)
    return problems


counter = {'n': 0}


def signup(page, base, college):
    counter['n'] += 1
    page.goto(base + '/')
    page.wait_for_load_state('networkidle')
    page.click('[data-auth-mode="signup"]')
    page.fill('#su-name', 'Test User')
    page.fill('#su-user', f'e2e{counter["n"]}')
    page.fill('#su-pass', 'goodpass99')
    if college:
        page.fill('#su-college', 'cs')
        page.select_option('#su-level', '3')
        page.select_option('#su-program', 'rse')
    page.click('#signup-form button[type=submit]')
    page.wait_for_selector('button.nav[data-page="home"]', timeout=10000)


def visible_text(page):
    return page.locator('#content').inner_text()


@pytest.mark.parametrize('lang', ['ar', 'en'])
@pytest.mark.parametrize('viewport', [(1366, 900), (390, 844)], ids=['desktop', 'phone'])
@pytest.mark.parametrize('college', [False, True], ids=['everyone', 'faculty'])
def test_every_page_and_dialog(server, browser, lang, viewport, college):
    context = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]})
    context.add_init_script(f"try{{localStorage.setItem('studyhub-lang','{lang}')}}catch(e){{}}")
    page = context.new_page()
    problems = watch(page)
    signup(page, server, college)
    pages = PAGES + (['archive'] if college else [])
    nav = [b.get_attribute('data-page') for b in page.locator('button.nav').all()]
    assert ('archive' in nav) == college, nav
    leftovers = {}
    for name in pages:
        page.locator(f'button.nav[data-page="{name}"]').first.click()
        page.wait_for_load_state('networkidle')
        text = visible_text(page)
        assert 'undefined' not in text and 'NaN' not in text and '[object Object]' not in text, (name, text[:300])
        if lang == 'en':
            leftovers[name] = sorted(set(ARABIC.findall(page.locator('.page-heading, .panel-head, .settings-block h2, nav').all_inner_texts().__str__())))
    # every "new" dialog opens and closes cleanly
    page.locator('button.nav[data-page="tasks"]').first.click()
    for action in ['task', 'personal-course', 'note', 'upload']:
        page.locator('button.nav[data-page="courses" ]' if action == 'personal-course' else 'button.nav[data-page="tasks"]').first.click()
        button = page.locator(f'[data-action="{action}"]').first
        if not button.count():
            continue
        button.click()
        page.wait_for_selector('#modal[open]')
        page.locator('#modal [data-action="close"]').first.click()
    page.locator('button.nav[data-page="schedule"]').first.click()
    page.locator('[data-action="slot"]').first.click()
    page.wait_for_selector('#modal[open]')
    page.locator('#modal [data-action="close"]').first.click()
    # assistant opens and points to the key settings
    page.locator('#assistant-fab').click()
    page.wait_for_selector('#assistant:not([hidden])')
    assert page.locator('[data-scroll="ai-key"]').count() == 1
    page.locator('[data-assistant-close]').click()
    context.close()
    assert not problems, problems
    if lang == 'en':
        untranslated = {k: v for k, v in leftovers.items() if v}
        assert not untranslated, untranslated


def test_full_flow_create_everything(server, browser):
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    page = context.new_page()
    problems = watch(page)
    signup(page, server, college=False)
    page.locator('button.nav[data-page="courses"]').click()
    page.click('[data-action="personal-course"]')
    page.fill('#entry-form input[name="title"]', 'Physics')
    page.click('#entry-form button[type=submit]')
    page.wait_for_selector('text=Physics')
    page.locator('button.nav[data-page="tasks"]').click()
    page.click('[data-action="task"]')
    page.fill('#entry-form input[name="title"]', 'Lab report')
    page.select_option('#entry-form select[name="course_id"]', label='Physics')
    page.fill('#entry-form input[name="due"]', '2030-01-01T10:00')
    page.click('#entry-form button[type=submit]')
    page.wait_for_selector('text=Lab report')
    page.locator('[data-toggle-task]').first.check()
    page.wait_for_timeout(800)
    page.locator('button.nav[data-page="schedule"]').click()
    page.click('[data-action="slot"]')
    page.fill('#entry-form input[name="title"]', 'Physics lecture')
    page.click('#entry-form button[type=submit]')
    page.wait_for_selector('text=Physics lecture')
    page.locator('button.nav[data-page="weekly"]').click()
    page.wait_for_selector('text=Lab report')
    page.locator('button.nav[data-page="settings"]').click()
    page.fill('#pw-old', 'goodpass99')
    page.fill('#pw-new', 'newpassword1')
    page.click('#password-form button[type=submit]')
    page.wait_for_selector('#login-form', timeout=10000)
    context.close()
    assert not problems, problems


def test_personal_site_login_and_pages(tmp_path, browser):
    from .conftest import OWNER_PASSWORD, _OWNER_HASH
    port = free_port()
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'STUDY_DATA_DIR': str(tmp_path), 'STUDY_SECURE_COOKIE': 'false',
           'STUDY_REMINDERS_ENABLED': 'false', 'STUDY_PASSWORD_HASH': _OWNER_HASH, 'STUDY_BOARD_SYNC': '0'}
    env.pop('STUDY_MULTIUSER', None)
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'studyhub.app:app', '--port', str(port)], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        page = browser.new_page()
        problems = watch(page)
        page.goto(f'http://127.0.0.1:{port}/')
        page.fill('#password', 'wrong-password')
        page.click('#login-form button[type=submit]')
        page.wait_for_timeout(800)
        assert page.locator('#login-form').count() == 1                     # still on login
        page.fill('#password', OWNER_PASSWORD)
        page.click('#login-form button[type=submit]')
        page.wait_for_selector('button.nav[data-page="home"]')
        for name in PAGES + ['archive']:
            page.locator(f'button.nav[data-page="{name}"]').first.click()
            page.wait_for_load_state('networkidle')
        page.close()
        assert not [p for p in problems if '401' not in p], problems
    finally:
        process.terminate()
        process.wait(10)


def _err(page, field):
    box = page.locator(f'#err-{field}')
    return box.inner_text() if box.count() and box.is_visible() else ''


@pytest.mark.parametrize('lang', ['en', 'ar'])
def test_signup_form_explains_each_mistake(server, browser, lang):
    """Field errors appear when you leave a field, clear as soon as you fix it, and server answers that
    belong to one field (wrong faculty code, taken username) show under that field."""
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    context.add_init_script(f"try{{localStorage.setItem('studyhub-lang','{lang}')}}catch(e){{}}")
    page = context.new_page()
    problems = watch(page)
    page.goto(server + '/')
    page.wait_for_load_state('networkidle')
    page.click('[data-auth-mode="signup"]')
    en = lang == 'en'
    # nothing shows while you are still typing the first time
    page.fill('#su-user', 'ab')
    assert _err(page, 'su-user') == ''
    page.locator('#su-pass').focus()                                    # leave the field
    assert ('2 of at least 3' if en else 'لسه 2 من') in _err(page, 'su-user')
    assert page.locator('#su-user').get_attribute('aria-invalid') is not None
    page.locator('#su-user').press_sequentially('c')                     # fixed → message gone while typing
    assert _err(page, 'su-user') == ''
    page.fill('#su-user', 'a b c')
    page.locator('#su-user').dispatch_event('input')
    assert ('No spaces' if en else 'من غير مسافات') in _err(page, 'su-user')
    page.fill('#su-user', 'أحمد')
    page.locator('#su-user').dispatch_event('input')
    assert ('English letters only' if en else 'بالحروف الإنجليزية') in _err(page, 'su-user')
    page.fill('#su-pass', '123')
    page.locator('#su-name').focus()
    assert ('Password too short: 3 of 8' if en else 'الباسورد لسه قصير: 3 من 8') in _err(page, 'su-pass')
    # submit with mistakes: every field marked, first one focused, nothing sent
    page.fill('#su-name', '')
    requests = []
    page.on('request', lambda r: requests.append(r.url) if '/api/signup' in r.url else None)
    page.click('#signup-form button[type=submit]')
    page.wait_for_timeout(400)
    assert not requests
    assert _err(page, 'su-name') and _err(page, 'su-user') and _err(page, 'su-pass')
    assert page.evaluate('document.activeElement.id') == 'su-name'
    # a wrong faculty code is checked by the server only, and shown under its own field
    page.fill('#su-name', 'Test User')
    page.fill('#su-user', f'validator{lang}')
    page.fill('#su-pass', 'goodpass99')
    page.fill('#su-college', 'nope')
    page.select_option('#su-level', '3')
    page.select_option('#su-program', 'rse')
    page.click('#signup-form button[type=submit]')
    page.wait_for_selector('#err-su-college:not([hidden])')
    assert ('Wrong faculty code' if en else 'كود الكلية غلط') in _err(page, 'su-college')
    assert page.evaluate('document.activeElement.id') == 'su-college'
    assert page.locator('#login-error').inner_text() == ''
    page.locator('#su-college').press_sequentially('x')                  # editing clears the server message
    assert _err(page, 'su-college') == ''
    # selects are required once a code is typed
    page.fill('#su-college', 'cs')
    page.select_option('#su-level', '')
    page.click('#signup-form button[type=submit]')
    page.wait_for_timeout(300)
    assert ('Choose an option' if en else 'اختار من القايمة') in _err(page, 'su-level')
    page.select_option('#su-level', '3')
    assert _err(page, 'su-level') == ''
    page.click('#signup-form button[type=submit]')
    page.wait_for_selector('button.nav[data-page="home"]', timeout=10000)
    context.close()
    # a taken username is shown under the username field
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    context.add_init_script(f"try{{localStorage.setItem('studyhub-lang','{lang}')}}catch(e){{}}")
    page = context.new_page()
    page.goto(server + '/')
    page.wait_for_load_state('networkidle')
    page.click('[data-auth-mode="signup"]')
    page.fill('#su-name', 'Someone')
    page.fill('#su-user', f'validator{lang}')
    page.fill('#su-pass', 'goodpass99')
    page.click('#signup-form button[type=submit]')
    page.wait_for_selector('#err-su-user:not([hidden])')
    assert ('username is taken' if en else 'محجوز') in _err(page, 'su-user')
    context.close()
    assert not [p for p in problems if 'HTTP 4' not in p and 'status of 4' not in p], problems


def test_task_and_password_forms_show_field_errors(server, browser):
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    context.add_init_script("try{localStorage.setItem('studyhub-lang','en')}catch(e){}")
    page = context.new_page()
    problems = watch(page)
    signup(page, server, college=False)
    page.locator('button.nav[data-page="tasks"]').click()
    page.click('[data-action="task"]')
    page.wait_for_selector('#modal[open]')
    page.click('#entry-form button[type=submit]')
    page.wait_for_timeout(300)
    assert page.locator('#modal[open]').count() == 1                     # stays open
    assert 'This field is required' in page.locator('#modal .field-error:not([hidden])').first.inner_text()
    page.fill('#entry-form input[name="title"]', 'Lab report')
    assert page.locator('#modal .field-error:not([hidden])').count() == 0
    page.click('#entry-form button[type=submit]')
    page.wait_for_selector('text=Lab report')
    page.locator('button.nav[data-page="settings"]').click()
    page.fill('#pw-old', 'wrong-password')
    page.fill('#pw-new', 'short')
    page.click('#password-form button[type=submit]')
    page.wait_for_timeout(300)
    assert 'Password too short: 5 of 8' in _err(page, 'pw-new')
    page.fill('#pw-new', 'newpassword1')
    page.click('#password-form button[type=submit]')
    page.wait_for_selector('#err-pw-old:not([hidden])')
    assert 'current password is wrong' in _err(page, 'pw-old')
    context.close()
    assert not [p for p in problems if 'HTTP 4' not in p and 'status of 4' not in p], problems


# ---------------------------------------------------------------- themes (theme.js)

THEME_ACCENTS = {'sage': '#3F6E5A', 'rose': '#B23F6A', 'lavender': '#6A3E9E', 'dark': '#7DB597'}


def css_var(page, name):
    return page.evaluate(f"getComputedStyle(document.documentElement).getPropertyValue('{name}').trim()").upper()


def test_theme_switch_on_login_page_persists(server, browser):
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    page = context.new_page()
    problems = watch(page)
    page.goto(server + '/')
    page.wait_for_selector('.login-controls .theme-switch')
    assert page.evaluate('StudyTheme.list()') == ['sage', 'rose', 'lavender', 'dark']
    assert page.evaluate('StudyTheme.get()') == 'sage' and page.get_attribute('html', 'data-theme') is None
    assert css_var(page, '--accent') == THEME_ACCENTS['sage']
    for name in ('rose', 'lavender', 'dark', 'sage'):
        page.click(f'.login-controls [data-theme-set="{name}"]')
        assert page.evaluate('StudyTheme.get()') == name
        assert css_var(page, '--accent') == THEME_ACCENTS[name]
        assert page.get_attribute(f'[data-theme-set="{name}"]', 'aria-pressed') == 'true'
        assert page.locator('.theme-switch [aria-pressed="true"]').count() == 1
    page.click('[data-theme-set="lavender"]')
    page.reload()
    page.wait_for_selector('.login-controls .theme-switch')
    assert page.get_attribute('html', 'data-theme') == 'lavender'          # saved per device
    assert page.get_attribute('meta[name="theme-color"]', 'content') == '#F7F3FC'
    assert 'Readex' in page.evaluate('getComputedStyle(document.body).fontFamily')
    assert page.evaluate("StudyTheme.set('neon')") is False                 # unknown themes are ignored
    assert page.evaluate('StudyTheme.get()') == 'lavender'
    page.evaluate("localStorage.setItem('studyhub-theme','<script>')")
    page.reload()
    assert page.evaluate('StudyTheme.get()') == 'sage'                      # a tampered value falls back
    context.close()
    assert not problems, problems


def test_dark_theme_follows_device_until_chosen(server, browser):
    context = browser.new_context(viewport={'width': 1366, 'height': 900}, color_scheme='dark')
    page = context.new_page()
    page.goto(server + '/')
    page.wait_for_selector('.login-controls .theme-switch')
    assert page.get_attribute('html', 'data-theme') == 'dark'             # no saved choice: follow the device
    assert page.get_attribute('meta[name="theme-color"]', 'content') == '#141614'
    page.click('.login-controls [data-theme-set="sage"]')
    page.reload()
    page.wait_for_selector('.login-controls .theme-switch')
    assert page.get_attribute('html', 'data-theme') is None                # an explicit choice wins
    context.close()


@pytest.mark.parametrize('theme', ['rose', 'lavender', 'dark'])
@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_every_page_in_each_new_theme(server, browser, theme, lang):
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    context.add_init_script(f"try{{localStorage.setItem('studyhub-theme','{theme}');localStorage.setItem('studyhub-lang','{lang}')}}catch(e){{}}")
    page = context.new_page()
    problems = watch(page)
    signup(page, server, college=True)
    assert page.locator('.topbar .theme-switch').count() == 1
    assert page.locator('.topbar .theme-switch + .lang-toggle').count() == 1  # right next to عربي/English
    assert css_var(page, '--accent') == THEME_ACCENTS[theme]
    names = page.locator('.topbar .theme-name').all_inner_texts()
    assert names == (['Calm', 'Rose', 'Lavender', 'Dark'] if lang == 'en' else ['هادي', 'وردي', 'لافندر', 'ليلي'])
    for name in PAGES + ['archive']:
        page.locator(f'button.nav[data-page="{name}"]').first.click()
        page.wait_for_load_state('networkidle')
        text = visible_text(page)
        assert 'undefined' not in text and 'NaN' not in text, (name, text[:200])
        assert page.get_attribute('html', 'data-theme') == theme           # survives re-renders
    page.click('.topbar [data-theme-set="sage"]')
    assert page.get_attribute('html', 'data-theme') is None and css_var(page, '--accent') == THEME_ACCENTS['sage']
    context.close()
    assert not problems, problems


def test_theme_switch_fits_on_a_phone(server, browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    context.add_init_script("try{localStorage.setItem('studyhub-theme','rose')}catch(e){}")
    page = context.new_page()
    signup(page, server, college=False)
    assert page.locator('.topbar .theme-name').first.is_hidden()           # dots only on small screens
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    box = page.locator('.topbar [data-theme-set="lavender"]').bounding_box()
    assert box['width'] >= 30 and box['height'] >= 30
    context.close()


# ---------------------------------------------------------------- show / hide password (password.js)

def test_password_eye_on_login_and_signup(server, browser):
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    page = context.new_page()
    problems = watch(page)
    page.goto(server + '/')
    page.wait_for_selector('#password')
    eye = page.locator('#password + .pw-toggle')
    assert eye.count() == 1 and eye.get_attribute('aria-pressed') == 'false'
    assert eye.get_attribute('aria-label') == 'إظهار الباسورد' and eye.get_attribute('type') == 'button'
    page.fill('#password', 'secret-pass')
    pad = page.evaluate("parseFloat(getComputedStyle(document.querySelector('#password')).paddingLeft)")
    eye_box, field_box = eye.bounding_box(), page.locator('#password').bounding_box()
    assert pad >= eye_box['x'] + eye_box['width'] - field_box['x']        # RTL page: the text never runs under the eye
    eye.click()
    assert page.get_attribute('#password', 'type') == 'text' and eye.get_attribute('aria-pressed') == 'true'
    assert eye.get_attribute('aria-label') == 'إخفاء الباسورد'
    assert page.evaluate('document.activeElement.id') == 'password'        # focus goes back to the field
    eye.click()
    assert page.get_attribute('#password', 'type') == 'password' and eye.get_attribute('aria-pressed') == 'false'
    assert page.input_value('#password') == 'secret-pass'                  # the value is never touched
    assert page.locator('#modal[open]').count() == 0 and page.locator('#login-form').count() == 1   # no submit
    page.fill('#username', 'nobody-here')
    page.fill('#password', 'wrong-password-1')
    eye.click()
    page.click('#login-form button[type=submit]')                         # really sent to the server
    page.wait_for_selector('#login-error:not(:empty), .field-error:not([hidden])')
    assert page.get_attribute('#password', 'type') == 'password'           # hidden again when the form is sent
    assert eye.get_attribute('aria-pressed') == 'false'
    # sign-up form: the eye works and the short-password message still sits under the field
    page.click('[data-auth-mode="signup"]')
    page.wait_for_selector('#su-pass + .pw-toggle')
    page.fill('#su-name', 'Eye Test'); page.fill('#su-user', 'eyetest'); page.fill('#su-pass', 'short')
    page.click('#su-pass + .pw-toggle')
    assert page.get_attribute('#su-pass', 'type') == 'text'
    page.click('#signup-form button[type=submit]')
    page.wait_for_selector('#err-su-pass:not([hidden])')
    assert 'الباسورد لسه قصير' in page.inner_text('#err-su-pass')            # still a password message while shown
    assert page.get_attribute('#su-pass', 'type') == 'text'                # still fixing it: stays as chosen
    assert page.evaluate("document.querySelector('#err-su-pass').previousElementSibling.classList.contains('password-wrap')")
    page.fill('#su-pass', 'goodpass99')
    page.click('#signup-form button[type=submit]')
    page.wait_for_selector('button.nav[data-page="home"]', timeout=10000)
    context.close()
    assert not [p for p in problems if 'HTTP 4' not in p and 'status of 4' not in p], problems


def test_password_eye_in_settings_and_english(server, browser):
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    context.add_init_script("try{localStorage.setItem('studyhub-lang','en')}catch(e){}")
    page = context.new_page()
    problems = watch(page)
    signup(page, server, college=False)
    page.locator('button.nav[data-page="settings"]').click()
    page.wait_for_selector('#pw-old + .pw-toggle')
    for field in ('#pw-old', '#pw-new', '#ai-key-input'):
        eye = page.locator(f'{field} + .pw-toggle')
        assert eye.count() == 1, field
        assert eye.get_attribute('aria-label') == 'Show password'
        eye.click()
        assert page.get_attribute(field, 'type') == 'text' and eye.get_attribute('aria-label') == 'Hide password'
        eye.click()
        assert page.get_attribute(field, 'type') == 'password'
    assert page.locator('.password-wrap .password-wrap').count() == 0       # re-renders never double-wrap
    page.locator('button.nav[data-page="home"]').click()
    page.locator('button.nav[data-page="settings"]').click()
    page.wait_for_selector('#pw-old + .pw-toggle')
    assert page.locator('#pw-old ~ .pw-toggle').count() == 1
    page.fill('#pw-old', 'goodpass99'); page.fill('#pw-new', 'brandnewpass1')
    page.click('#pw-new + .pw-toggle')
    page.click('#password-form button[type=submit]')
    page.wait_for_selector('#login-form', timeout=10000)                   # password changed -> back to login
    context.close()
    assert not [p for p in problems if 'HTTP 4' not in p and 'status of 4' not in p], problems

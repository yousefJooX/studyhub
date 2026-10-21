'use strict';
const $ = s => document.querySelector(s);
const E = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  home:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  book:'<path d="M3 4h6l3 2 3-2h6v15h-6l-3 2-3-2H3zM12 6v15"/>',
  task:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 12 3 3 5-6"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-14 4h2m6 0h2"/>',
  file:'<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8m-8 4h6"/>',
  archive:'<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v13h14V8m-10 4h6"/>',
  settings:'<path d="m9 3 6 0 1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z"/><circle cx="12" cy="12" r="3"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  edit:'<path d="m15 3 6 6-12 12H3v-6zM12 6l6 6"/>',
  refresh:'<path d="M20 7a9 9 0 1 0 1 9M20 3v5h-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
  chart:'<path d="M4 20V11m6 9V5m6 15v-7m5 7H3"/>',
  logout:'<path d="M10 4H4v16h6m5-13 5 5-5 5m-7-5h12"/>'
};
const I = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
let S = {}, page = 'home', activeCourse = '', query = '', kind = 'all', status = 'open', selectedCourse = '', filePage = 0;
let weekly = null, weekOffset = 0;
let folderFilter = '', openFolders = new Set((()=>{try{return JSON.parse(localStorage.getItem('library-open')||'[]');}catch{return [];}})()), libraryLayout = (()=>{try{return localStorage.getItem('library-layout')||'list';}catch{return 'list';}})(), activeVaultNote = null;
let files = [], archive = null, archiveYear = '4', archiveDepartment = '', archiveTerm = '1', activeNote = null, installPrompt = null;
let planDay = cairoLocal(new Date()).slice(0,10), clockOffset = 0, pushInfo = null, devicePush = null, polling = false;
const finishedTimers = new Set();
const dayNames = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'];
const typeNames = {study:'مذاكرة',lecture:'محاضرة',section:'سكشن',lab:'لاب'};
const statusNames = {todo:'لسه هبدأ',doing:'شغّال عليها',done:'خلصت'};
const dateFormat = (value, opts = {}) => new Intl.DateTimeFormat(window.StudyI18n?.locale()||'ar-EG', {timeZone:'Africa/Cairo',day:'numeric',month:'short',...opts}).format(new Date(value));
const today = () => new Intl.DateTimeFormat('en-US',{timeZone:'Africa/Cairo',weekday:'long'}).format(new Date());
const dayIndex = () => ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].indexOf(today());
const courseName = id => S.courses?.find(c => c.id === id)?.title || 'عام';
const size = n => n >= 1073741824 ? `${(n/1073741824).toFixed(1)} GB` : n >= 1048576 ? `${(n/1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.ceil(n/1024)} KB` : `${n || 0} B`;
const act = (action, label, cls='button', extra='') => `<button type="button" class="${cls}" data-action="${action}" ${extra}>${label}</button>`;
const empty = (title, text, action='', label='') => `<div class="empty">${I('book')}<b>${title}</b>${text}${action ? `<div>${act(action,label)}</div>` : ''}</div>`;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').classList.remove('show'), 4000); }
async function api(url, method='GET', data) {
  let options = {method, credentials:'same-origin'};
  if (data !== undefined) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(data); }
  try{
    return await StudyHttp.json(url,options,{onRetry:(attempt,total)=>{
      const message=$('#app .boot span');if(message)message.textContent=`بنحاول نوصل للموقع تاني… ${attempt+1} / ${total}`;
    }});
  }catch(error){
    if(error.status===401&&url!=='/api/login')loginView();
    throw error;
  }
}
async function refresh(render=true) { S = await api('/api/state'); clockOffset=S.server_time*1000-Date.now(); if (render) await renderPage(); }
const LEVELS = [[1,'الفرقة الأولى'],[2,'الفرقة الثانية'],[3,'الفرقة الثالثة'],[4,'الفرقة الرابعة']];
function collegeFields(level='', program='') {
  const programs = Object.entries(AUTH.programs || {});
  return `<div class="field"><label for="su-level">سنتك</label><select id="su-level" name="level" data-college-select><option value="">اختار…</option>${LEVELS.map(([v,t])=>`<option value="${v}" ${String(level)===String(v)?'selected':''}>${t}</option>`).join('')}</select></div>
    <div class="field"><label for="su-program">البرنامج</label><select id="su-program" name="program"><option value="">عام / لسه مختارتش</option>${programs.map(([v,t])=>`<option value="${v}" ${program===v?'selected':''}>${E(t)}</option>`).join('')}</select></div>`;
}
function toggleCollegeFields(on) {
  const box=document.querySelector('.college-fields'); if(!box) return;
  box.hidden=!on; box.querySelectorAll('[data-college-select]').forEach(s=>s.required=on);
}
function loginView(mode='login') {
  S = {}; if (typeof Assistant !== 'undefined') Assistant.setVisible(false);
  const multi = AUTH.multiuser, signup = multi && AUTH.signup && mode === 'signup';
  const switcher = multi && AUTH.signup ? `<p class="auth-switch">${signup ? 'عندك حساب؟' : 'أول مرة هنا؟'} <button type="button" class="link-button" data-auth-mode="${signup ? 'login' : 'signup'}">${signup ? 'سجّل دخول' : 'اعمل حساب'}</button></p>` : '';
  const fields = signup
    ? `<label for="su-name">اسمك</label><input id="su-name" name="name" autocomplete="name" maxlength="60" required autofocus>
       <label for="su-user">اسم المستخدم</label><input id="su-user" name="username" dir="ltr" autocomplete="username" pattern="[A-Za-z0-9_.\-]{3,32}" minlength="3" maxlength="32" required>
       <small class="muted">إنجليزي وأرقام بس، من ٣ حروف</small>
       <label for="su-pass">الباسورد</label><input id="su-pass" name="password" type="password" autocomplete="new-password" minlength="8" required>${AUTH.invite ? `
       <label for="su-code">كود الدعوة</label><input id="su-code" name="code" dir="ltr" autocomplete="off" required>` : ''}
       <div class="college-box">${AUTH.college_code
         ? `<label for="su-college">كود الكلية · لو انت من الكلية</label><input id="su-college" name="college_code" dir="ltr" autocomplete="off" data-college-code-input>`
         : `<label class="checkbox-option"><input type="checkbox" name="college" value="1" data-college-check> أنا من ${E(AUTH.college_name || 'الكلية')}</label>`}
         <div class="college-fields" hidden>${collegeFields()}</div>
         <small class="muted">الكود بيفتحلك أرشيف الكلية وتاسكات الكلية حسب سنتك. من غيره الموقع شغال عادي.</small></div>`
    : `${multi ? `<label for="username">اسم المستخدم</label><input id="username" name="username" dir="ltr" autocomplete="username" required autofocus>` : ''}
       <label for="password">كلمة المرور</label><input id="password" name="password" type="password" autocomplete="current-password" required ${multi ? '' : 'autofocus'}>`;
  $('#app').innerHTML = `<div class="login-page"><section class="login-art"><img src="/static/icon.svg" alt=""><div class="label">YOUR STUDY SPACE</div><h1>كل اللي بتتعلّمه.<br>في مساحتك.</h1><p>مواد الكلية، نوتس Obsidian، والكورسات اللي بتختارها بنفسك. خطة واحدة، من أي جهاز.</p></section><section class="login-form"><form id="${signup ? 'signup-form' : 'login-form'}"><h2>${signup ? 'اعمل حسابك' : 'أهلًا بيك'}</h2><p>${signup ? 'مساحة دراسية خاصة بيك، محدش يشوفها غيرك.' : 'ادخل لمساحتك الدراسية.'}</p>${fields}<button class="button" type="submit">${signup ? 'اعمل الحساب' : 'دخول لمساحتي'} ${I('arrow')}</button><div id="login-error" class="error" role="alert"></div>${switcher}<p class="small muted">بياناتك محفوظة على السيرفر.</p></form></section></div>`;
}
const pageNames = {home:'نظرة عامة',day:'قائمة يومي',courses:'المواد والكورسات',course:'المواد والكورسات',tasks:'المهام',schedule:'الجدول',library:'النوتس والملفات',weekly:'ملخص الأسبوع',vnote:'النوتس والملفات',archive:'أرشيف الكلية',settings:'الإعدادات',note:'النوتس والملفات'};
function shell() {
  $('#app').innerHTML = `${!navigator.onLine ? '<div class="offline">إنت أوفلاين. الاتصال بالسيرفر مطلوب لفتح بياناتك وحفظ التغييرات.</div>' : ''}<div class="layout"><aside class="sidebar"><div class="brand"><img src="/static/icon.svg" alt=""><div>مساحتي<small>STUDY WORKSPACE</small></div></div><nav>${[['home','home'],['day','calendar'],['courses','book'],['tasks','task'],['schedule','calendar'],['weekly','chart'],['library','file'],...(S.archive?[['archive','archive']]:[])].map(([p,i]) => `<button class="nav ${page === p || (p==='courses' && page==='course') || (p==='library' && (page==='note'||page==='vnote')) ? 'active':''}" data-page="${p}">${I(i)}${pageNames[p]}${p==='tasks' && S.tasks.filter(t=>t.status!=='done').length ? `<span class="count">${S.tasks.filter(t=>t.status!=='done').length}</span>`:''}</button>`).join('')}<button class="nav ${page==='settings'?'active':''}" data-page="settings">${I('settings')}الإعدادات</button></nav><div class="side-bottom"><div class="profile"><div class="avatar">${E(firstName().slice(0,1) || 'S')}</div><div><span dir="auto">${E(userName())}</span><small><span class="server-dot"></span>مساحة شخصية</small></div>${act('logout',I('logout'),'icon-button','aria-label="تسجيل الخروج"')}</div></div></aside><main class="main"><header class="topbar"><div class="breadcrumb">مساحتي <span> / </span><b>${pageNames[page]}</b></div><div class="top-actions"><span class="pill">${dateFormat(new Date(),{weekday:'short'})}</span><button type="button" class="lang-toggle" data-lang-toggle data-no-i18n aria-label="${window.StudyI18n?.lang()==='en'?'التحويل للعربي':'Switch to English'}">${window.StudyI18n?.lang()==='en'?'عربي':'English'}</button>${act('install',I('download')+' تثبيت التطبيق','button ghost')}${act('refresh',I('refresh'),'icon-button','aria-label="تحديث البيانات"')}</div></header><div id="content"></div></main></div>`;
}
function heading(title, subtitle, button='') { return `<div class="page-heading"><div><h1>${E(title)}</h1><p>${E(subtitle)}</p></div>${button}</div>`; }
function courseCards(courses) {
  return `<div class="course-grid">${courses.map(c => `<article class="course-card"><button class="course-open" data-course="${E(c.id)}"><div class="course-icon">${I('book')}</div><span class="tag ${c.kind==='personal'?'green':''}">${c.kind==='college'?'مادة كلية':'كورس خارج الكلية'}</span><h3 dir="auto">${E(c.title)}</h3><div class="meta"><span>${I('task')}${S.tasks.filter(t=>t.course_id===c.id && t.status!=='done').length} مهام مفتوحة</span><span>${c.vault_folder?'Obsidian':c.archive_account?'أرشيف الكلية':'مادة خاصة'}</span></div></button><div class="course-card-actions">${act('edit-course',I('edit')+' تعديل','button ghost',`data-id="${E(c.id)}" aria-label="${E('تعديل '+c.title)}"`)}${act('delete-course','إزالة','button ghost remove',`data-id="${E(c.id)}" aria-label="${E('إزالة '+c.title)}"`)}</div></article>`).join('')}</div>`;
}
function coursesView() {
  const courses = S.courses.filter(c=>kind==='all'||c.kind===kind);
  return heading('المواد والكورسات','أضف أي كورس بتاخده برّه الكلية، ونظّم مهامه ونوتسه ومواعيده.',act('personal-course',I('plus')+' كورس خارج الكلية'))+
    `<div class="toolbar">${act('college-course',I('plus')+' مادة كلية','button secondary')}${S.archive?'<button class="button ghost" data-page="archive">إضافة من أرشيف الكلية ←</button>':''}<div class="filter">${[['all','الكل'],['college','مواد الكلية'],['personal','كورسات خارج الكلية']].map(([v,t])=>`<button data-kind="${v}" class="${kind===v?'active':''}">${t}</button>`).join('')}</div></div>`+
    (courses.length?courseCards(courses):empty('ابدأ بمادة أو كورس','الكورس الشخصي مش محتاج أرشيف كلية أو فولدر Obsidian.','course','إضافة مادة أو كورس'));
}
function taskRows(tasks) {
  return tasks.map(t => `<div class="task-row"><input type="checkbox" class="check" data-toggle-task="${E(t.id)}" ${t.status==='done'?'checked':''} aria-label="${E('إكمال '+t.title)}"><div class="task-main"><button class="task-title ${t.status==='done'?'done':''}" dir="auto" data-edit-task="${E(t.id)}">${E(t.title)}</button><div class="task-meta"><span>${E(courseName(t.course_id))}</span>${t.planned_date?`<span>${E(t.planned_date)}${t.planned_time?' · '+E(t.planned_time):''}</span>`:''}<span>${t.duration_minutes||25} د</span>${t.status==='doing'?'<span class="tag">شغّال عليها</span>':''}${t.priority==='high'?'<span class="tag red">مهمة</span>':''}${t.remind_at?`<span title="${E(dateFormat(t.remind_at,{hour:'2-digit',minute:'2-digit'}))}">تذكير مضبوط</span>`:''}${t.resource?`<a target="_blank" rel="noopener" href="${E(t.resource)}">افتح المرفق</a>`:''}</div><div class="timer-inline" data-timer-box="${E(t.id)}">${timerControls(t,true)}</div></div>${t.due?`<time class="deadline ${t.status!=='done' && new Date(t.due)<new Date()?'overdue':''}" datetime="${E(t.due)}">التسليم<br>${dateFormat(t.due)}<br>${dateFormat(t.due,{hour:'2-digit',minute:'2-digit',day:undefined,month:undefined})}</time>`:'<span class="deadline">بدون deadline</span>'}</div>`).join('');
}
const todayDate = () => cairoLocal(new Date(Date.now()+clockOffset)).slice(0,10);
const shiftedDay = (day, offset) => new Date(new Date(day+'T12:00:00Z').getTime()+offset*86400000).toISOString().slice(0,10);
const taskDay = t => t.planned_date || (t.due?cairoLocal(t.due).slice(0,10):'');
const dayTasks = day => S.tasks.filter(t=>taskDay(t)===day).sort((a,b)=>(a.status==='done')-(b.status==='done') || (a.planned_time||'99:99').localeCompare(b.planned_time||'99:99'));
function timerSeconds(t) {
  const timer=t.timer||{};
  return timer.state==='running'?Math.max(0,Math.ceil((timer.ends_at*1000-Date.now()-clockOffset)/1000)):timer.remaining_seconds??(t.duration_minutes||25)*60;
}
const clockText = seconds => `${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`;
function timerControls(t,compact=false) {
  if(t.status==='done')return compact?'':'<span class="muted">المهمة مكتملة.</span>';
  const timer=t.timer||{}, finished=timer.state==='finished'||(timer.state==='running'&&timerSeconds(t)===0), running=timer.state==='running'&&!finished, paused=timer.state==='paused';
  return `<span class="timer-count ${running?'running':''}" data-countdown="${E(t.id)}" dir="ltr">${clockText(timerSeconds(t))}</span>${act(running?'timer-pause':'timer-start',running?'إيقاف مؤقت':paused?'كمّل':finished?'جلسة جديدة':`ابدأ ${t.duration_minutes||25} د`,compact?'button ghost':'button',`data-id="${E(t.id)}"`)}${act('timer-duration','غيّر المدة','button ghost',`data-id="${E(t.id)}"`)}${timer.state&&timer.state!=='idle'?act('timer-reset','تصفير','button ghost',`data-id="${E(t.id)}"`):''}${finished?'<span class="tag green">الجلسة خلصت</span>':''}`;
}
function durationEditor(task) {
  if($('#modal').open&&$('#entry-form')){
    const form=$('#entry-form');form.elements.duration_minutes.focus();
    return toast('اكتب مدة الجلسة اللي تناسبك فوق. زر ابدأ هيستخدمها مباشرة.');
  }
  const active=['running','paused'].includes(task.timer?.state);
  modal('خصّص مدة التايمر',`<form id="duration-form" data-id="${E(task.id)}"><p dir="auto">${E(task.title)}</p><div class="duration-presets">${[5,15,30,45,60,90].map(minutes=>act('duration-preset',minutes+' د','button secondary',`data-minutes="${minutes}"`)).join('')}</div>${field('المدة اللي تناسبك بالدقائق',input('duration_minutes',task.duration_minutes||25,'number','required min="1" max="1440"'))}<p class="small muted">اكتب أي مدة من دقيقة ليوم كامل. ساعة = 60 دقيقة، ساعة ونص = 90.</p><label class="checkbox-option"><input type="checkbox" name="use_default_duration">خلي دي المدة الافتراضية للمهام الجديدة</label>${active?'<div class="notice warn">تغيير المدة يعيد ضبط وقت الجلسة الحالية. لو المدة نفسها، التايمر هيكمل زي ما هو.</div>':''}<div class="error" role="alert"></div><div class="modal-actions"><button type="submit" class="button">${active?'حفظ المدة وإعادة الضبط':'حفظ المدة'}</button>${act('close','إلغاء','button secondary')}</div></form>`);
}
function timerPreview(task,box) {
  const form=box.closest('#entry-form');
  const minutes=Number(form?.elements.duration_minutes?.value);
  if(form?.dataset.id===task.id&&Number.isInteger(minutes)&&minutes>=1&&minutes<=1440&&minutes!==(task.duration_minutes||25)&&task.timer?.state!=='running')
    return {...task,duration_minutes:minutes,timer:{state:'idle',remaining_seconds:minutes*60}};
  return task;
}
function updateDraftTimer() {
  for(const box of document.querySelectorAll('#entry-form [data-timer-box]')){
    const task=S.tasks.find(t=>t.id===box.dataset.timerBox);if(task)box.innerHTML=timerControls(timerPreview(task,box));
  }
}
function dayView() {
  const tasks=dayTasks(planDay), done=tasks.filter(t=>t.status==='done').length;
  return heading(planDay===todayDate()?'قائمة النهارده':'خطة يوم '+dateFormat(planDay+'T12:00:00Z',{weekday:'long'}),'حدّد يومك، علّم على اللي خلص، وخد كل مهمة بوقتها.',act('task',I('plus')+' مهمة لليوم ده'))+
    `<div class="day-toolbar">${act('previous-day','اليوم السابق','button secondary')}<input type="date" aria-label="يوم القائمة" data-change="plan-day" value="${E(planDay)}">${act('next-day','اليوم التالي','button secondary')}${act('today','النهارده','button ghost')}<span class="day-progress">${done} / ${tasks.length} مكتمل · ${tasks.reduce((n,t)=>n+(t.duration_minutes||25),0)} د مخطّطة</span></div>`+
    `<div class="reminder-banner"><div><b>${devicePush?'تذكيرات الجهاز مفعّلة':'خلي مهامك تفكّرك'}</b><p>${devicePush?'اضبط وقت التذكير داخل المهمة. انتهاء التايمر له إشعار كمان.':'فعّل إشعارات الجهاز، وبعدها اختار وقت تذكير لكل مهمة.'}</p></div>${act(devicePush?'notification-settings':'enable-notifications',devicePush?'إعدادات التذكيرات':'تفعيل تذكيرات الجهاز','button secondary')}</div>`+
    `<section class="panel"><form id="quick-task-form" class="quick-task"><input name="title" required maxlength="300" placeholder="أضف خطوة لليوم ده…" aria-label="مهمة سريعة لليوم"><button type="submit" class="button">${I('plus')} إضافة</button><div class="error" role="alert"></div></form>${tasks.length?taskRows(tasks):empty('يوم جديد، قائمة جديدة','أضف مهمة من فوق، أو افتح تفاصيل مهمة وغيّر يوم تنفيذها.','task','أضف أول مهمة')}</section>`;
}
function homeView() {
  const open = S.tasks.filter(t=>t.status!=='done');
  const overdue = open.filter(t=>t.due && new Date(t.due)<new Date());
  const daily = dayTasks(todayDate()).slice(0,5);
  const slots = S.slots.filter(s=>s.day===dayIndex()).sort((a,b)=>a.start.localeCompare(b.start));
  return heading(firstName() ? `أهلًا يا ${firstName()}` : 'أهلًا بيك','يوم جديد للتعلّم. خد نظرة على يومك، وابدأ بخطوة واحدة.',act('task',I('plus')+' مهمة جديدة')) +
    `<div class="stats">${[['موادي',S.courses.length,'كلية وتعلّم شخصي','book'],['مهام مفتوحة',open.length,'خطواتك الجاية','task'],['متأخر عن موعده',overdue.length,overdue.length?'محتاجين منك مراجعة':'مافيش مهام متأخرة','clock'],['ملفات الـVault',S.vault_count,`${S.vault_notes} نوتس ضمن الملفات`,'file']].map(([a,b,c,i],n)=>`<div class="stat ${n===2&&b?'urgent':''}"><div class="stat-top">${a}${I(i)}</div><b>${b}</b><small>${c}</small></div>`).join('')}</div>` +
    `<div class="dashboard-grid"><section class="panel"><div class="panel-head"><h2>قائمة النهارده</h2><button class="button ghost" data-page="day">اختار يوم تاني ←</button></div>${daily.length?taskRows(daily):empty('ابدأ بأول مهمة لليوم','محاضرة تذاكرها، واجب تسلّمه، أو كورس تكمّله.','task','أضف مهمة')}</section><div class="stack"><section class="panel"><div class="panel-head"><h2>جدول النهارده</h2><span class="today-date">${dayNames[dayIndex()]}</span></div><div class="panel-body">${slots.length?slots.map(s=>`<div class="schedule-item"><div class="schedule-time">${E(s.start)}<small>${E(s.end)}</small></div><div class="schedule-copy"><b>${E(s.title)}</b><small>${typeNames[s.kind]} · ${E(s.location || courseName(s.course_id))}</small></div></div>`).join(''):empty('يومك لسه مفتوح','حدّد مواعيد الكلية والمذاكرة.','slot','أضف ميعاد')}</div></section><section class="library-summary"><div class="label">CONNECTED TO OBSIDIAN</div><h2>نوتسك قريبة منك.</h2><p>${S.vault_available?`${S.mounts.map(E).join('، ')} · ${S.vault_count} ملف متاح للتصفح`:'اربط نسخة فولدراتك على السيرفر لفتحها من أي جهاز.'}</p><button class="button" data-page="library">افتح مكتبتي ${I('arrow')}</button></section></div></div>`+
    `<div class="section-title"><h2>مسارات التعلّم</h2><div class="section-actions">${act('personal-course',I('plus')+' كورس خارج الكلية','button secondary')}<button class="button ghost" data-page="courses">كل المواد ←</button></div></div>${S.courses.length?courseCards(S.courses.slice(0,6)):empty('جهّز موادك','أضف مادة بنفسك أو اختار من أرشيف الكلية.','course','إضافة مادة')}`;
}
function courseView() {
  const c = S.courses.find(c=>c.id===activeCourse);
  if (!c) return empty('المادة غير موجودة','ارجع لقائمة المواد.');
  const tasks = S.tasks.filter(t=>t.course_id===c.id);
  return `<button class="back" data-page="courses">← كل المواد والكورسات</button>` + heading(c.title,c.kind==='college'?'مادة كلية':'كورس خارج الكلية',act('edit-course',I('edit')+' تعديل / إزالة'))+
    `<div class="toolbar">${act('task',I('plus')+' مهمة')}${act('note',I('plus')+' نوت','button secondary')}${act('upload',I('plus')+' ملف','button secondary')}${act('course-files',I('file')+' مكتبة المادة','button secondary')}${S.archive&&c.archive_account?act('archive-files',I('archive')+' ملفات الكلية','button secondary',`data-account="${E(c.archive_account)}" data-source-course="${E(c.archive_course)}"`):''}</div>`+
    `<div class="connection"><span>${c.vault_folder?`Obsidian: <b dir="auto">${E(c.vault_folder)}</b>`:'فولدر Obsidian غير مربوط'} ${act('edit-course','تعديل','button ghost')}</span>${S.archive?`<span>${c.archive_account?'المادة مربوطة بأرشيف الكلية':'تقدر تربطها بمادة من أرشيف الكلية'}</span>`:''}</div>`+
    `<div class="section-title"><h2>مهام المادة</h2><span class="muted small">${tasks.filter(t=>t.status==='done').length} / ${tasks.length} مكتمل</span></div><section class="panel">${tasks.length?taskRows(tasks):empty('خطط للمادة','كل مهمة هنا هتظهر في الداشبورد كمان.','task','أضف مهمة')}</section>`+
    `<div class="section-title"><h2>نوتس وملفات أضفتها</h2></div><section class="panel">${personalFiles(c.id)}</section>`;
}
function personalFiles(course='') {
  const notes = S.notes.filter(n=>!course || n.course_id===course);
  const uploads = S.uploads.filter(n=>!course || n.course_id===course);
  const entries = [...notes.map(n=>({id:n.id,name:n.title,kind:'local-note',folder:courseName(n.course_id),modified:n.updated})),...uploads.map(n=>({...n,kind:'upload',folder:courseName(n.course_id),modified:n.created}))];
  return entries.length?entries.map(fileRow).join(''):empty('مساحة لنوتسك وملفاتك','تقدر تكتب نوت أو ترفع ملف من جهازك.');
}
function tasksView() {
  let tasks = S.tasks.filter(t=>(!selectedCourse || t.course_id===selectedCourse) && (status==='all' || (status==='open'?t.status!=='done':t.status===status)) && `${t.title} ${courseName(t.course_id)}`.toLowerCase().includes(query.toLowerCase()));
  tasks.sort((a,b)=>(a.due?new Date(a.due).getTime():Infinity)-(b.due?new Date(b.due).getTime():Infinity));
  return heading('خطوة بخطوة.','مهام الكلية والكورسات، بالمواعيد اللي إنت بتحددها.',act('task',I('plus')+' مهمة جديدة'))+
    `<div class="toolbar"><div class="search"><input data-search type="search" placeholder="دوّر على مهمة…" aria-label="بحث المهام" value="${E(query)}"></div><select data-change="course-filter" aria-label="تصفية حسب المادة">${courseOptions(selectedCourse,'كل المواد')}</select><div class="filter">${[['open','مفتوحة'],['done','مكتملة'],['all','الكل']].map(([v,t])=>`<button data-status="${v}" class="${status===v?'active':''}">${t}</button>`).join('')}</div></div><section class="panel">${tasks.length?taskRows(tasks):empty('مافيش مهام في العرض ده','غيّر الفلتر، أو أضف مهمة جديدة.','task','إضافة مهمة')}</section>`;
}
function scheduleView() {
  return heading('أسبوعك قدامك.','مواعيد الكلية وجلسات المذاكرة بتوقيت القاهرة.',act('slot',I('plus')+' ميعاد جديد'))+
    `<div class="toolbar"><div class="filter">${[['all','الكل'],['college','الكلية'],['study','المذاكرة']].map(([v,t])=>`<button data-kind="${v}" class="${kind===v?'active':''}">${t}</button>`).join('')}</div><span class="small muted">المواعيد أسبوعية ومتكررة</span></div><div class="week">${[6,0,1,2,3,4,5].map(day=>`<section class="day ${day===dayIndex()?'today':''}"><h3>${dayNames[day]} ${day===dayIndex()?'<span class="tag">النهارده</span>':''}</h3>${S.slots.filter(s=>s.day===day && (kind==='all' || (kind==='college'?s.kind!=='study':s.kind==='study'))).sort((a,b)=>a.start.localeCompare(b.start)).map(s=>`<button class="day-slot ${s.kind}" data-edit-slot="${s.id}"><time>${E(s.start)} — ${E(s.end)}</time><b>${E(s.title)}</b><small>${typeNames[s.kind]}${s.location?' · '+E(s.location):''}</small></button>`).join('')}<button class="button ghost" data-new-day="${day}">${I('plus')} إضافة</button></section>`).join('')}</div>`;
}
const FTYPES = {
  note:{label:'نوتس',ext:['md'],tag:'MD'}, pdf:{label:'PDF',ext:['pdf'],tag:'PDF'},
  slides:{label:'عروض',ext:['ppt','pptx'],tag:'PPT'}, doc:{label:'مستندات',ext:['doc','docx','txt'],tag:'DOC'},
  sheet:{label:'جداول',ext:['xls','xlsx','csv'],tag:'XLS'}, code:{label:'كود',ext:['py','ipynb','html'],tag:'CODE'},
  image:{label:'صور',ext:['png','jpg','jpeg','webp','gif','svg'],tag:'IMG'}, media:{label:'صوت وفيديو',ext:['mp3','mp4','wav'],tag:'MEDIA'}
};
const ftype = f => (f.kind==='note'||f.kind==='local-note') ? 'note' : (Object.entries(FTYPES).find(([,t])=>t.ext.includes((f.name.split('.').pop()||'').toLowerCase()))||['other'])[0];
const natural = (a,b) => a.localeCompare(b,'ar',{numeric:true,sensitivity:'base'});
const mountPrefix = folder => { const m=(S.vault_folders||[]).concat(['1 Univ']).find(m=>folder===m||folder.startsWith(m+'/')); return m?folder.slice(m.length).replace(/^\//,''):folder; };
const shortFolder = folder => mountPrefix(folder||'') || 'الفولدر الرئيسي';
function libraryEntries() {
  const own = x => !selectedCourse || x.course_id===selectedCourse;
  return [...files.map(f=>({...f,group:f.folder})),
    ...S.notes.filter(own).map(n=>({id:n.id,name:n.title,kind:'local-note',group:'مساحتي/'+courseName(n.course_id),folder:courseName(n.course_id),modified:n.updated})),
    ...S.uploads.filter(own).map(n=>({...n,kind:'upload',group:'مساحتي/'+courseName(n.course_id),folder:courseName(n.course_id),modified:n.created}))];
}
async function keepTreeScroll(fn){const top=document.querySelector('.library-tree .tree')?.scrollTop||0;await fn();const tree=document.querySelector('.library-tree .tree');if(tree)tree.scrollTop=top;}
function saveOpenFolders(){try{localStorage.setItem('library-open',JSON.stringify([...openFolders]));}catch{}}
function folderTree(entries) {
  const root={name:'',path:'',children:{},count:0};
  entries.forEach(f=>{
    const parts=(f.kind==='upload'||f.kind==='local-note'?f.group:shortFolder(f.group)).split('/').filter(Boolean);
    let node=root; root.count++;
    parts.forEach((part,i)=>{ const path=parts.slice(0,i+1).join('/'); node=node.children[part]||(node.children[part]={name:part,path,children:{},count:0}); node.count++; });
  });
  const draw = (node,depth) => Object.values(node.children).sort((a,b)=>natural(a.name,b.name)).map(c=>{
    const kids=Object.keys(c.children).length, open=openFolders.has(c.path);
    const toggle=kids?`<button type="button" class="tree-toggle" data-tree-toggle="${E(c.path)}" aria-expanded="${open}" aria-label="${open?'اقفل':'افتح'} ${E(c.name)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>`:'<span class="tree-toggle-space"></span>';
    const row=`<div class="tree-row">${toggle}<button type="button" class="tree-item ${folderFilter===c.path?'active':''}" data-folder="${E(c.path)}">${I('archive')}<span dir="auto">${E(c.name)}</span><small>${c.count}</small></button></div>`;
    return kids?`<div class="tree-group">${row}${open?`<div class="tree-children">${draw(c,depth+1)}</div>`:''}</div>`:row;
  }).join('');
  return `<button type="button" class="tree-item ${folderFilter?'':'active'}" data-folder="">${I('home')}<span>كل الملفات</span><small>${root.count}</small></button>${draw(root,0)}`;
}
const entryFolder = f => f.kind==='upload'||f.kind==='local-note' ? f.group : shortFolder(f.group);
function fileRow(f, compact=false) {
  const type=ftype(f), isNote=type==='note';
  const url = f.kind==='local-note'?'':f.kind==='upload'?`/api/uploads/${f.id}`:`/api/vault/${isNote?'note':'file'}?path=${encodeURIComponent(f.path)}`;
  const name = isNote?f.name.replace(/\.md$/i,''):f.name;
  const label = `<b dir="auto">${E(name)}</b><small dir="auto">${compact?'':E(entryFolder(f))+' · '}${f.size!==undefined?size(f.size)+' · ':''}${dateFormat(f.modified*1000)}${f.kind==='upload'?' · رفع يدوي':f.kind==='local-note'?' · نوت شخصية':''}</small>`;
  const open = f.kind==='local-note'?`<button type="button" class="file-open" data-note="${E(f.id)}">${label}</button>`
    : f.kind==='note'?`<button type="button" class="file-open" data-vault-note="${E(f.path)}">${label}</button>`
    : `<a class="file-open" href="${E(url)}" target="_blank" rel="noopener">${label}</a>`;
  return `<div class="file-row ft-${type}"><span class="file-icon" aria-hidden="true">${(FTYPES[type]||{tag:E((f.name.split('.').pop()||'').slice(0,4).toUpperCase())}).tag}</span><div class="file-info">${open}</div>${url?act('resource-task',I('plus'),'icon-button',`data-title="${E('مراجعة '+name)}" data-resource="${E(url)}" aria-label="إضافة مهمة للملف" title="إضافة مهمة مراجعة"`):''}${f.kind==='upload'?act('delete-upload',I('close'),'icon-button',`data-id="${f.id}" aria-label="حذف الملف المرفوع"`):''}</div>`;
}
function libraryView() {
  const entries = libraryEntries();
  const inFolder = entries.filter(f=>!folderFilter || entryFolder(f)===folderFilter || entryFolder(f).startsWith(folderFilter+'/'));
  const q = query.trim().toLowerCase();
  const searched = inFolder.filter(f=>!q || `${f.name} ${entryFolder(f)}`.toLowerCase().includes(q));
  const counts = {}; searched.forEach(f=>{const t=ftype(f);counts[t]=(counts[t]||0)+1;});
  const shown = searched.filter(f=>kind==='all'||ftype(f)===kind);
  const chips = [['all','الكل',searched.length],...Object.entries(FTYPES).filter(([t])=>counts[t]).map(([t,v])=>[t,v.label,counts[t]]),...(counts.other?[['other','تانية',counts.other]]:[])]
    .map(([v,t,n])=>`<button type="button" data-kind="${v}" class="chip ${kind===v?'active':''}" aria-pressed="${kind===v}">${t}<small>${n}</small></button>`).join('');
  const recent = !q && !folderFilter && kind==='all' ? [...entries].sort((a,b)=>b.modified-a.modified).slice(0,6) : [];
  const groups = {}; shown.forEach(f=>{ (groups[entryFolder(f)]=groups[entryFolder(f)]||[]).push(f); });
  const groupNames = Object.keys(groups).sort(natural);
  const pageSize = 80, flat = groupNames.flatMap(g=>groups[g].sort((a,b)=>natural(a.name,b.name)));
  const pages = Math.max(1,Math.ceil(flat.length/pageSize)); filePage=Math.min(filePage,pages-1);
  const slice = flat.slice(filePage*pageSize,(filePage+1)*pageSize);
  let lastGroup = null, list = '';
  slice.forEach(f=>{ const g=entryFolder(f); if(g!==lastGroup){ if(lastGroup!==null) list+='</div></section>'; const parts=g.split('/'); list+=`<section class="file-group"><header class="file-group-head"><button type="button" data-folder="${E(g)}" dir="auto">${I('archive')}<span class="crumbs">${parts.slice(0,-1).map(p=>`<span>${E(p)}</span>`).join('')}<b>${E(parts.at(-1))}</b></span></button><small>${groups[g].length}</small></header><div class="${libraryLayout==='grid'?'file-grid':'file-list'}">`; lastGroup=g; } list+=fileRow(f,true); });
  if(lastGroup!==null) list+='</div></section>';
  const crumbs = folderFilter ? `<nav class="breadcrumb" aria-label="المسار"><button type="button" data-folder="">المكتبة</button>${folderFilter.split('/').map((p,i,a)=>`<span aria-hidden="true">‹</span><button type="button" data-folder="${E(a.slice(0,i+1).join('/'))}" dir="auto">${E(p)}</button>`).join('')}</nav>` : '';
  return heading('كل حاجة في مكانها.','نوتس Obsidian وملفاتك، متقسمة بالفولدر زي ما هي عندك.',act('note',I('plus')+' نوت جديدة'))+
    `<div class="library">
      <aside class="library-tree panel" aria-label="الفولدرات"><div class="panel-head"><h2>الفولدرات</h2></div><div class="tree">${folderTree(entries)}</div></aside>
      <div class="library-main">
        <div class="toolbar"><div class="search"><input data-search type="search" placeholder="دوّر في النوتس والملفات…" value="${E(query)}" aria-label="بحث الملفات"></div><select data-change="library-course" aria-label="تصفية ملفات المادة">${courseOptions(selectedCourse,'كل المواد والفولدرات')}</select>${act('upload',I('plus')+' رفع ملف','button secondary')}<div class="filter view-toggle">${[['list','قائمة'],['grid','كروت']].map(([v,t])=>`<button type="button" data-layout="${v}" class="${libraryLayout===v?'active':''}" aria-pressed="${libraryLayout===v}">${t}</button>`).join('')}</div></div>
        <div class="chips" role="group" aria-label="نوع الملف">${chips}</div>
        ${crumbs}
        ${recent.length?`<section class="recent"><h2>آخر حاجات اتعدلت</h2><div class="recent-grid">${recent.map(f=>fileRow(f)).join('')}</div></section>`:''}
        <div class="library-results"><div class="results-meta muted small">${shown.length} عنصر${q?` لـ «${E(query)}»`:''}</div>
        ${slice.length?list:`<section class="panel">${empty('مافيش ملفات في العرض ده','غيّر الفلتر أو الفولدر، أو ارفع ملف جديد.','upload','رفع ملف')}</section>`}
        ${pages>1?`<div class="pagination"><button type="button" data-file-page="${filePage-1}" ${filePage===0?'disabled':''}>السابق</button><span>${filePage+1} / ${pages}</span><button type="button" data-file-page="${filePage+1}" ${filePage+1>=pages?'disabled':''}>التالي</button></div>`:''}</div>
      </div>
    </div>`;
}
function readerView(note, local) {
  const folder = local ? courseName(note.course_id) : shortFolder(note.folder);
  const raw = local ? '' : `/api/vault/note?path=${encodeURIComponent(note.path)}`;
  const actions = local ? act('edit-note',I('edit')+' تعديل النوت','button secondary')
    : `<a class="button secondary" href="${E(raw)}" target="_blank" rel="noopener">فتح في تاب جديدة</a>${act('resource-task',I('plus')+' مهمة مراجعة','button',`data-title="${E('مراجعة '+note.title)}" data-resource="${E(raw)}"`)}`;
  return `<div class="reading-progress" aria-hidden="true"><span></span></div>
    <button type="button" class="back" data-page="library">→ رجوع للمكتبة</button>
    <div class="reader">
      <div class="reader-main">
        <header class="reader-head"><div class="reader-crumbs" dir="auto">${folder.split('/').map(p=>`<span>${E(p)}</span>`).join('<i aria-hidden="true">‹</i>')}</div>
          <h1 dir="auto">${E(note.title)}</h1>
          <div class="reader-meta"><span>${(note.modified||note.updated)?'آخر تعديل '+dateFormat(((note.modified||note.updated)>1e11?(note.modified||note.updated):(note.modified||note.updated)*1000),{year:'numeric'}):''}</span><div class="reader-actions">${actions}</div></div>
        </header>
        <article class="prose reader-article" data-reader>${note.html}</article>
      </div>
      <aside class="reader-toc" aria-label="المحتوى"><h2>في النوت دي</h2><nav id="toc"></nav></aside>
    </div>`;
}
async function enhanceReader() {
  const root=document.querySelector('#content [data-reader]'); if(!root||!window.StudyReader) return;
  const items=await StudyReader.enhance(root); const nav=$('#toc'); if(!nav) return;
  if(items.length<2){ nav.closest('.reader-toc').hidden=true; return; }
  nav.innerHTML=items.map(h=>`<a href="#${encodeURIComponent(h.id)}" class="toc-l${h.level}" dir="auto">${E(h.text)}</a>`).join('');
}
const fmtMin = m => { m=Math.round(m||0); const h=Math.floor(m/60), r=m%60; return h ? `${h} س${r?` ${r} د`:''}` : `${r} د`; };
function deltaLine(now, prev, unit) {
  if (!prev && !now) return '<small class="wk-delta">مفيش بيانات الأسبوع اللي فات</small>';
  const diff = now - prev, cls = diff > 0 ? 'up' : diff < 0 ? 'down' : '';
  const text = unit === 'min' ? fmtMin(Math.abs(diff)) : Math.abs(diff);
  return `<small class="wk-delta ${cls}">${diff===0?'زي الأسبوع اللي فات':`${diff>0?'+':'-'}${text} عن الأسبوع اللي فات`}</small>`;
}
function weeklyView() {
  if (!weekly) return '<div class="loading">بنحسب أسبوعك…</div>';
  const w = weekly, goal = w.daily_goal_minutes || 90;
  const max = Math.max(goal * 1.4, ...w.days.map(d=>d.minutes), 1);
  const range = `${dateFormat(w.week_start+'T12:00:00Z')} – ${dateFormat(w.week_end+'T12:00:00Z')}`;
  const nav = `<div class="wk-nav"><button type="button" class="button secondary" data-week="${w.weeks_ago+1}">الأسبوع اللي قبله</button><button type="button" class="button secondary" data-week="${Math.max(0,w.weeks_ago-1)}" ${w.weeks_ago===0?'disabled':''}>الأسبوع اللي بعده</button></div>`;
  const kpi = (label, value, extra) => `<div class="stat wk-kpi"><div class="stat-top"><span>${label}</span></div><b>${value}</b>${extra}</div>`;
  const bars = w.days.map(d => {
    const h = Math.round(d.minutes / max * 170), isToday = d.date === w.today;
    return `<div class="wk-day ${isToday?'today':''}"><span class="wk-val">${d.minutes?fmtMin(d.minutes):'—'}</span><span class="wk-bar ${d.minutes>=goal?'hit':''}" style="height:${Math.max(3,h)}px"></span><span class="wk-label"><span class="wk-long">${dayNames[d.weekday]}</span><span class="wk-short">${['أحد','اتنين','تلات','أربع','خميس','جمعة','سبت'][d.weekday]}</span></span></div>`;
  }).join('');
  const goalLine = `<span class="wk-goal" style="bottom:${Math.round(goal/max*170)+28}px" aria-hidden="true"></span>`;
  const cmax = Math.max(1, ...w.courses.map(c=>c.minutes));
  const courseRows = w.courses.length ? w.courses.map(c => `<div class="wk-course"><div class="wk-course-top"><b dir="auto">${E(c.name)}</b><span>${fmtMin(c.minutes)} · ${c.done} ${c.done===1?'مهمة':'مهام'}</span></div><div class="wk-track"><span style="width:${Math.round(c.minutes/cmax*100)}%"></span></div></div>`).join('') : empty('لسه مفيش وقت متسجل','شغّل التايمر على أي مهمة، والوقت هيظهر هنا.');
  const levels = m => m===null ? 'future' : m===0 ? 'l0' : m < 30 ? 'l1' : m < 90 ? 'l2' : 'l3';
  const heat = w.heatmap.map(col => `<div class="wk-heat-col">${col.map(m=>`<span class="wk-cell ${levels(m)}" title="${m===null?'':fmtMin(m)}"></span>`).join('')}</div>`).join('');
  const doneList = w.done.length ? w.done.slice(0,12).map(t=>`<div class="wk-done"><span class="wk-check" aria-hidden="true"></span><div><b dir="auto">${E(t.title)}</b><small dir="auto">${E(t.course||'عام')}${t.updated?' · '+dateFormat(t.updated*1000,{weekday:'long'}):''}</small></div></div>`).join('') : empty('مفيش مهام خلصت الأسبوع ده','لما تعلّم على مهمة إنها خلصت هتظهر هنا.');
  const missed = w.missed.length ? `<div class="wk-missed">فاتها المعاد: ${w.missed.map(t=>E(t.title)).join('، ')}</div>` : '';
  return heading('ملخص الأسبوع', `${range} · من السبت للجمعة`, nav) +
    `<div class="stats wk-kpis">
      ${kpi('وقت التركيز', fmtMin(w.focus_minutes), deltaLine(w.focus_minutes, w.focus_minutes_prev, 'min'))}
      ${kpi('مهام خلصت', w.done_count, deltaLine(w.done_count, w.done_prev))}
      ${kpi('جلسات تركيز', w.sessions, `<small class="wk-delta">${w.sessions?`متوسط الجلسة ${fmtMin(w.avg_session_minutes)}`:'ابدأ أول جلسة من أي مهمة'}</small>`)}
      ${kpi('أيام ورا بعض', w.streak_days, `<small class="wk-delta">${w.planned_count?`${w.planned_done} من ${w.planned_count} من المخطط خلص`:'ذاكرت أو خلّصت مهمة'}</small>`)}
    </div>
    <div class="wk-grid">
      <section class="panel wk-chart-panel"><div class="panel-head"><h2>مذاكرة كل يوم</h2><span class="muted small">الخط المتقطع = هدفك ${fmtMin(goal)}</span></div>
        <div class="wk-chart" role="img" aria-label="دقايق التركيز في كل يوم: ${w.days.map(d=>dayNames[d.weekday]+' '+d.minutes).join('، ')}">${goalLine}${bars}</div></section>
      <section class="panel"><div class="panel-head"><h2>الوقت راح فين</h2></div><div class="panel-body wk-courses">${courseRows}</div></section>
      <section class="panel"><div class="panel-head"><h2>آخر ١٢ أسبوع</h2><span class="muted small">كل مربع = يوم</span></div>
        <div class="panel-body"><div class="wk-heat" role="img" aria-label="خريطة أيام المذاكرة لآخر ١٢ أسبوع">${heat}</div>
        <div class="wk-legend"><span>أقل</span><span class="wk-cell l0"></span><span class="wk-cell l1"></span><span class="wk-cell l2"></span><span class="wk-cell l3"></span><span>أكتر</span></div></div></section>
      <section class="panel"><div class="panel-head"><h2>خلصت الأسبوع ده</h2><span class="muted small">${w.done_count}</span></div>${doneList}${missed}</section>
    </div>`;
}
function archiveView() {
  if (!archive) return '<div class="loading">بنقرأ أرشيف الكلية…</div>';
  const years = [...new Set(archive.accounts.map(a=>String(a.year)))].sort();
  if (!years.includes(archiveYear)) archiveYear = years[0] || '';
  const departments = [...new Set(archive.accounts.filter(a=>String(a.year)===archiveYear).map(a=>a.department))];
  if (!departments.includes(archiveDepartment)) archiveDepartment = departments.find(d=>/robot|روبوت|إنسان/i.test(d)) || departments[0] || '';
  const terms = [...new Set(archive.accounts.filter(a=>String(a.year)===archiveYear && a.department===archiveDepartment).map(a=>String(a.term)))].sort();
  if (!terms.includes(archiveTerm)) archiveTerm = terms[0] || '';
  const accounts = archive.accounts.filter(a=>String(a.year)===archiveYear && a.department===archiveDepartment && String(a.term)===archiveTerm);
  const keys = new Set(accounts.map(a=>a.key));
  const courses = archive.courses.filter(c=>keys.has(c.account) && c.title.toLowerCase().includes(query.toLowerCase()));
  return heading('من أرشيف الكلية لمساحتك.','اختار السنة والقسم والترم، وبعدها أضف موادك أو اربطها بمادة موجودة.')+
    `<div class="toolbar"><select data-change="archive-year" aria-label="السنة">${years.map(y=>`<option value="${E(y)}" ${archiveYear===y?'selected':''}>سنة ${E(y)}</option>`).join('')}</select><select data-change="archive-department" aria-label="القسم">${departments.map(d=>`<option ${archiveDepartment===d?'selected':''}>${E(d)}</option>`).join('')}</select><select data-change="archive-term" aria-label="الترم">${terms.map(t=>`<option value="${E(t)}" ${archiveTerm===t?'selected':''}>الترم ${E(t)}</option>`).join('')}</select><div class="search"><input data-search type="search" value="${E(query)}" placeholder="دوّر على مادة…" aria-label="بحث أرشيف الكلية"></div></div>`+
    `<div class="notice">المواد المضافة بتقرأ أحدث محتوى محفوظ في الأرشيف. إضافة المادة تقدر تجمع ملفات الكلية ونوتس Obsidian تحت نفس المادة.</div>`+
    `<section class="panel">${courses.length?courses.map(c=>{ const linked=S.courses.find(x=>x.archive_account===c.account&&x.archive_course===c.id); return `<div class="file-row"><div class="course-icon">${I('book')}</div><div class="file-info"><b dir="auto">${E(c.title)}</b><small>${E(c.doctor||'')}${linked?' · مضافة لموادك':''}</small></div>${act('archive-files','الملفات','button ghost',`data-account="${E(c.account)}" data-source-course="${E(c.id)}"`)}${linked?`<button class="button secondary" data-course="${linked.id}">افتح المادة</button>`:act('import-course','أضف لموادي','button secondary',`data-account="${E(c.account)}" data-source-course="${E(c.id)}" data-title="${E(c.title)}"`)}</div>`;}).join(''):empty('الأرشيف غير متاح في العرض ده','تأكد إن نسخة الأرشيف مربوطة بالموقع، أو غيّر الاختيارات.')}</section>`;
}
function settingsView() {
  return heading('مساحتك، بطريقتك.','مصادر المحتوى والتثبيت على أجهزتك.')+
    `${AUTH.multiuser?'':`<section class="settings-block"><h2>الفولدرات المربوطة من Obsidian</h2><p>الموقع يعرض ملفات الفولدرات دي من نسخة الـVault المتاحة للسيرفر. ملفات Obsidian الأصلية للقراءة، والنوتس الجديدة هنا بتتحفظ في الموقع.</p>${S.mounts.map(m=>`<div class="mount"><b>${E(m)}</b><span class="tag">${S.vault_available?'مضاف':'في انتظار الملفات'}</span></div>`).join('')}<form id="mount-form" class="form-inline"><select name="folder" aria-label="فولدر Obsidian جديد" required><option value="">اختار فولدر من نسخة السيرفر</option>${S.available_folders.filter(f=>!S.mounts.includes(f)).map(f=>`<option>${E(f)}</option>`).join('')}</select><button class="button secondary" type="submit">إضافة فولدر</button></form><p class="small">إضافة فولدر هنا بتسمح بعرضه. وصول تعديلاته من جهاز Obsidian للسيرفر يحتاج مزامنة الملفات بين الجهازين.</p></section>`}`+(AUTH.multiuser?vaultUploadView()+accountView():'')+
    `<section class="settings-block"><h2>الموقع كتطبيق</h2><p>من Chrome أو Edge اختار تثبيت التطبيق. على iPhone افتح قائمة المشاركة واختار «إضافة إلى الشاشة الرئيسية». التثبيت على التليفون يحتاج رابط HTTPS.</p>${act('install',I('download')+' تثبيت مساحتي')}<p class="small">النسخة الحالية تحتاج الاتصال بالسيرفر لقراءة المحتوى وحفظ التغييرات. ملفاتك تقدر تنزّلها على جهازك.</p></section>`+
    aiKeyView()+collegeProfileView()+notificationsView()+voicesView()+`<section class="settings-block"><h2>حفظ البيانات والتحديثات</h2><p>المهام والجداول والنوتس والملفات المرفوعة محفوظة على السيرفر. فتح التطبيق من جهاز آخر بنفس كلمة المرور يعرض نفس البيانات. كل المواعيد بتوقيت القاهرة.</p>${act('refresh',I('refresh')+' تحديث البيانات','button secondary')} ${act('logout','تسجيل الخروج','button secondary')}</section>`;
}
function notificationsView() {
  const permission='Notification'in window?Notification.permission:'unsupported';
  return `<section class="settings-block"><h2>تذكيرات المهام والتايمر</h2><p>${devicePush?'إشعارات الجهاز ده مفعّلة. التذكير بيوصلك حتى لو صفحة الموقع مقفولة، مع اتصال الجهاز والسيرفر بالإنترنت.':permission==='denied'?'الإشعارات محظورة على الجهاز ده. اسمح بها من إعدادات الموقع في المتصفح، وبعدها فعّلها هنا.':'فعّل الإشعارات على كل جهاز عايز توصلك عليه تذكيرات. حدد وقت التذكير من تفاصيل المهمة.'}</p><div class="toolbar">${act('enable-notifications',devicePush?'إعادة ربط الجهاز':'تفعيل تذكيرات الجهاز')}${devicePush?act('test-notification','إرسال إشعار تجريبي','button secondary')+act('disable-notifications','إيقاف إشعارات الجهاز','button ghost'):''}</div><p class="small">على iPhone: أضف مساحتي للشاشة الرئيسية وافتحه منها لتفعيل الإشعارات. السماح بالإشعارات مطلوب، وقد تؤخر إعدادات التركيز أو توفير البطارية ظهورها.</p>${pushInfo?.worker?.ok===false?'<div class="notice warn">خدمة التذكيرات محتاجة مراجعة. مهامك والتايمر محفوظين.</div>':''}${pushInfo?.failed_recent?'<div class="notice warn">بعض الإشعارات تعذّر إرسالها. جرّب إشعارًا تجريبيًا أو أعد ربط الجهاز.</div>':''}</section>`;
}
async function syncPushState() {
  if(!('serviceWorker'in navigator)||!('PushManager'in window)||!('Notification'in window))return;
  const registration=await navigator.serviceWorker.getRegistration();
  devicePush=registration?await registration.pushManager.getSubscription():null;
  pushInfo=await api('/api/notifications/push/config');
}
async function enableNotifications() {
  if(!('Notification'in window)||!('PushManager'in window)||!('serviceWorker'in navigator)||!window.isSecureContext)
    return modal('تفعيل التذكيرات', '<p>افتح رابط مساحتي في Chrome أو Firefox أو Safari لتفعيل إشعارات الجهاز. على iPhone أضف التطبيق للشاشة الرئيسية وافتحه منها.</p><p>قائمة اليوم والتايمر شغالين هنا، وإشعارات الجهاز تحتاج متصفحًا يدعمها.</p>');
  const permission=await Notification.requestPermission();
  if(permission!=='granted')throw new Error('السماح بإشعارات الموقع مطلوب. تقدر تغيّره من إعدادات المتصفح.');
  const registration=await navigator.serviceWorker.ready;
  pushInfo=await api('/api/notifications/push/config');
  const key=Uint8Array.from(atob(pushInfo.public_key.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
  devicePush=await registration.pushManager.getSubscription()||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
  await api('/api/notifications/push/subscription','POST',devicePush.toJSON());
  toast('تذكيرات الجهاز اتفعّلت. اضبط وقت التذكير داخل المهمة.');
  if(!$('#modal').open)await renderPage();
}
async function disableNotifications(silent=false) {
  if('serviceWorker'in navigator){
    const registration=await navigator.serviceWorker.getRegistration();
    const subscription=registration?await registration.pushManager?.getSubscription():null;
    if(subscription){
      try{await api('/api/notifications/push/subscription','DELETE',{endpoint:subscription.endpoint});}
      finally{await subscription.unsubscribe();devicePush=null;}
    }
  }
  if(!silent){await syncPushState();await renderPage();toast('إشعارات الجهاز اتوقفت');}
}
function updateTimerDisplays() {
  if(!S.tasks)return;
  for(const node of document.querySelectorAll('[data-countdown]')){
    const task=S.tasks.find(t=>t.id===node.dataset.countdown);
    if(task)node.textContent=clockText(timerSeconds(timerPreview(task,node.closest('[data-timer-box]'))));
  }
  for(const task of S.tasks){
    const timer=task.timer||{};
    if(task.status!=='done' && timer.state==='running' && timerSeconds(task)===0 && !finishedTimers.has(timer.run_id)){
      finishedTimers.add(timer.run_id);toast('جلسة «'+task.title+'» خلصت. خُد استراحة أو كمّل المهمة.');
      for(const box of document.querySelectorAll('[data-timer-box]'))if(box.dataset.timerBox===task.id)box.innerHTML=timerControls(task,!box.closest('#entry-form'));
    }
  }
  const running=S.tasks.find(t=>t.timer?.state==='running'&&timerSeconds(t)>0&&t.status!=='done');
  document.title=running?clockText(timerSeconds(running))+' • '+running.title+' — StudyHub':'مساحتك الدراسية — StudyHub';
}
async function openTask(id) {
  await refresh(false);const task=S.tasks.find(t=>t.id===id);if(!task)return;
  planDay=taskDay(task)||todayDate();page='day';await renderPage();editor('task',task);
}
async function renderPage() {
  shell(); Assistant.refreshVisibility(); let content = '';
  if (page==='home') content=homeView();
  else if (page==='day') content=dayView();
  else if (page==='courses') content=coursesView();
  else if (page==='course') content=courseView();
  else if (page==='tasks') content=tasksView();
  else if (page==='schedule') content=scheduleView();
  else if (page==='library') content=libraryView();
  else if (page==='archive') content=archiveView();
  else if (page==='weekly') content=weeklyView();
  else if (page==='settings') content=settingsView();
  else if (page==='note' && activeNote) content=readerView(activeNote,true);
  else if (page==='vnote' && activeVaultNote) content=readerView(activeVaultNote,false);
  $('#content').innerHTML=content;
  if(page==='note'||page==='vnote') enhanceReader();
  updateTimerDisplays();
}
async function navigate(p) {
  page=p; query='';kind='all';status='open';selectedCourse='';filePage=0;activeCourse='';folderFilter='';
  if (page==='library') files=(await api('/api/vault')).files;
  if (page==='archive' && !S.archive) page='home';
  if (page==='archive') archive=await api('/api/archive');
  if (page==='weekly') {weekOffset=0;weekly=await api('/api/weekly');}
  if (page==='day') planDay=todayDate();
  if (page==='settings') {aiKey=await api('/api/assistant/key').catch(()=>null);boardInfo=S.college?.college?await api('/api/board').catch(()=>null):null;await syncPushState();voices=await api('/api/voices').catch(()=>null);newToken=null;if(AUTH.multiuser)account=await api('/api/account').catch(()=>null);}
  await renderPage(); window.scrollTo(0,0);
}
function courseOptions(value='', blank='بدون مادة') { return `<option value="">${blank}</option>`+S.courses.map(c=>`<option value="${E(c.id)}" ${value===c.id?'selected':''}>${E(c.title)}</option>`).join(''); }
function field(label, control, full=false) {
  const name = control.match(/name="([^"]+)"/)?.[1];
  const id = name ? 'field-'+name : '';
  if(id) control=control.replace(/^<(input|select|textarea)/,`<$1 id="${id}"`);
  return `<div class="field ${full?'full':''}"><label ${id?'for="'+id+'"':''}>${label}</label>${control}</div>`;
}
function input(name,value='',type='text',extra='') { return `<input name="${name}" type="${type}" value="${E(value)}" ${extra}>`; }
function select(name,entries,value) { return `<select name="${name}">${entries.map(([v,l])=>`<option value="${E(v)}" ${v===value?'selected':''}>${E(l)}</option>`).join('')}</select>`; }
function modal(title,body) { $('#modal-content').innerHTML=`<div class="modal-head"><h2>${E(title)}</h2>${act('close',I('close'),'icon-button','aria-label="إغلاق"')}</div><div class="modal-body">${body}</div>`; if(!$('#modal').open) $('#modal').showModal(); }
function editor(type, item={}, extras={}) {
  const course = item.course_id || (page==='course'?activeCourse:selectedCourse) || '';
  let title='', fields='';
  if (type==='course') {
    const folders=[...new Set([...S.vault_folders,...S.courses.map(c=>c.vault_folder).filter(Boolean)])].sort();
    title=item.id?'تعديل المادة أو الكورس':extras.kind==='college'?'مادة كلية جديدة':'كورس خارج الكلية';
    fields=field('اسم المادة أو الكورس',input('title',item.title,'text','required maxlength="200" placeholder="مثلاً: MLOps أو كورس ألماني"'),true)+field('النوع',select('kind',[['personal','كورس خارج الكلية'],['college','مادة كلية']],item.kind||extras.kind||'personal'))+field('فولدر Obsidian · اختياري',select('vault_folder',[['','بدون ربط'],...folders.map(f=>[f,f])],item.vault_folder||''));
    if(!item.id) fields+='<p class="small muted field full">اكتب اسم الكورس واحفظه، وبعدها أضف مهامه ومواعيده ونوتسه أو ارفع ملفاته.</p>';
  } else if (type==='task') {
    title=item.id?'تفاصيل المهمة':'مهمة جديدة';
    const due = item.due ? cairoLocal(item.due):'';
    const planned=item.id?(item.planned_date||''):(page==='day'?planDay:todayDate());
    const duration=item.id?(item.duration_minutes||25):(S.task_defaults?.duration_minutes||25);
    fields=field('المهمة',input('title',item.title||extras.title,'text','required maxlength="300"'),true)+field('تبع مادة',`<select name="course_id">${courseOptions(course)}</select>`)+field('يوم التنفيذ',input('planned_date',planned,'date'))+field('وقت البداية · اختياري',input('planned_time',item.planned_time||'','time'))+field('مدة الجلسة اللي تناسبك · بالدقائق',input('duration_minutes',duration,'number','required min="1" max="1440"'))+`<div class="field full duration-field"><div class="duration-presets">${[15,30,45,60,90].map(minutes=>act('duration-preset',minutes+' د','button secondary',`data-minutes="${minutes}"`)).join('')}</div><label class="checkbox-option"><input type="checkbox" name="use_default_duration">خلي دي المدة الافتراضية للمهام الجديدة</label></div>`+field('موعد التسليم · القاهرة',input('due',due,'datetime-local'))+field('تذكير · القاهرة',input('remind_at',item.remind_at?cairoLocal(item.remind_at):'','datetime-local'))+`<div class="field full reminder-quick">${act('remind-start','ذكّرني عند البداية','button secondary')}${act('remind-deadline','قبل التسليم بـ15 دقيقة','button secondary')}${act('clear-reminder','إلغاء التذكير','button ghost')}<span class="small muted">التذكير يحتاج تفعيل إشعارات الجهاز.</span>${act('enable-notifications','تفعيل تذكيرات الجهاز','button ghost')}</div>`+field('الحالة',select('status',Object.entries(statusNames),item.status||'todo'))+field('الأولوية',select('priority',[['normal','عادية'],['high','مهمة']],item.priority||'normal'))+field('تفاصيل',`<textarea name="details">${E(item.details||'')}</textarea>`,true)+input('resource',item.resource||extras.resource||'','hidden');
    if(item.id)fields+=`<div class="field full focus-session"><b>تايمر المهمة</b><div data-timer-box="${E(item.id)}">${timerControls(item)}</div><span class="small muted">زر ابدأ هيستخدم المدة اللي كتبتها فوق مباشرة. تغيير المدة يبدأ جلسة جديدة.</span>${act('move-task-tomorrow','انقل المهمة لبكرة','button ghost',`data-id="${E(item.id)}"`)}</div>`;
  } else if (type==='slot') {
    title=item.id?'تعديل الميعاد':'ميعاد في جدولك';
    fields=field('عنوان الميعاد',input('title',item.title,'text','required maxlength="200"'),true)+field('المادة',`<select name="course_id">${courseOptions(course)}</select>`)+field('النوع',select('kind',Object.entries(typeNames),item.kind||'study'))+field('اليوم',select('day',dayNames.map((n,i)=>[String(i),n]),String(item.day??extras.day??dayIndex())))+field('المكان',input('location',item.location))+field('من',input('start',item.start||'10:00','time','required'))+field('إلى',input('end',item.end||'11:00','time','required'));
  } else if (type==='note') {
    title=item.id?'تعديل النوت':'نوت جديدة';
    fields=field('عنوان النوت',input('title',item.title,'text','required maxlength="200"'),true)+field('المادة',`<select name="course_id">${courseOptions(course)}</select>`,true)+field('المحتوى · Markdown',`<textarea class="editor" name="content" placeholder="# عنوان النوت\nاكتب أفكارك هنا…">${E(item.content||'')}</textarea>`,true);
  } else if (type==='upload') {
    title='رفع ملف من جهازك';
    fields=field('المادة',`<select name="course_id">${courseOptions(course)}</select>`,true)+field('الملف · لحد 512 ميجابايت','<input type="file" name="file" required>',true);
  }
  modal(title,`<form id="entry-form" data-type="${type}" data-id="${E(item.id||'')}"><div class="form-grid">${fields}</div><div class="error" id="form-error" role="alert"></div><div class="modal-actions"><button type="submit" class="button">${type==='upload'?'رفع الملف':'حفظ'}</button>${act('close','إلغاء','button secondary')}${item.id?act(type==='course'?'delete-course':'delete-entry',type==='course'?'إزالة المادة':'حذف','button danger',`data-type="${type}" data-id="${E(item.id)}"`):''}</div></form>`);
}
function cairoLocal(value) {
  const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'Africa/Cairo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));
  const p = Object.fromEntries(parts.map(x=>[x.type,x.value])); return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}
function cairoISO(value) {
  if (!value) return '';
  const [date,tm] = value.split('T'); const [y,m,d]=date.split('-').map(Number); const [h,min]=tm.split(':').map(Number);
  const target = Date.UTC(y,m-1,d,h,min); let guess=target;
  for(let i=0;i<3;i++) { const local=cairoLocal(new Date(guess)); const [ld,lt]=local.split('T'); const [ly,lm,lday]=ld.split('-').map(Number); const [lh,lmin]=lt.split(':').map(Number); const delta=target-Date.UTC(ly,lm-1,lday,lh,lmin); if(!delta) return new Date(guess).toISOString(); guess+=delta; }
  if(cairoLocal(new Date(guess))!==value) throw new Error('الوقت ده غير موجود بسبب تغيير التوقيت الصيفي؛ اختار وقت تاني');
  return new Date(guess).toISOString();
}
async function install() {
  if(installPrompt) { await installPrompt.prompt(); installPrompt=null; }
  else modal('تثبيت مساحتي',`<p>على Android: افتح الموقع في Chrome واختار «تثبيت التطبيق» من القائمة.</p><p>على iPhone: افتح قائمة المشاركة، وبعدها «إضافة إلى الشاشة الرئيسية».</p><p>على الكمبيوتر: افتح الموقع في Chrome أو Edge واضغط أيقونة التثبيت جنب عنوان الموقع.</p><div class="notice">تثبيت التطبيق على الموبايل يحتاج رابط HTTPS. البيانات بتتحفظ على سيرفرك، والتحديث الجديد بيظهر لما تفتح التطبيق أو تحدّثه.</div>`);
}
document.addEventListener('click',async event=>{
  const b=event.target.closest('button,a'); if(!b) return;
  const inReader=b.tagName==='A'&&b.closest('[data-reader]'), href=b.getAttribute('href')||'';
  if(inReader&&href.startsWith('/api/vault/note?')) event.preventDefault();
  if(inReader&&href.startsWith('#')){event.preventDefault();document.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView({behavior:'smooth',block:'start'});return;}
  if(b.closest('#toc')&&href.startsWith('#')){event.preventDefault();document.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView({behavior:'smooth',block:'start'});return;}
  try {
    if(inReader&&href.startsWith('/api/vault/note?')) {const path=new URL(href,location.origin).searchParams.get('path');activeVaultNote=await api('/api/vault/note-data?path='+encodeURIComponent(path));page='vnote';await renderPage();return window.scrollTo(0,0);}
    if(b.dataset.action==='create-token') {b.disabled=true;newToken=await api('/api/account/token','POST',{});account=await api('/api/account');await renderPage();return $('#token-url')?.select();}
    if(b.dataset.action==='revoke-token') {b.disabled=true;await api('/api/account/token','DELETE');newToken=null;account=await api('/api/account');toast('الـ token وقف');return await renderPage();}
    if(b.dataset.langToggle!==undefined) return window.StudyI18n?.set(window.StudyI18n.lang()==='en'?'ar':'en');
    if(b.dataset.vaultNote!==undefined) {b.disabled=true;activeVaultNote=await api('/api/vault/note-data?path='+encodeURIComponent(b.dataset.vaultNote));page='vnote';await renderPage();return window.scrollTo(0,0);}
    if(b.dataset.treeToggle!==undefined) {const path=b.dataset.treeToggle;openFolders.has(path)?openFolders.delete(path):openFolders.add(path);saveOpenFolders();return await keepTreeScroll(renderPage);}
    if(b.dataset.folder!==undefined) {folderFilter=b.dataset.folder;folderFilter.split('/').forEach((_,i,a)=>openFolders.add(a.slice(0,i+1).join('/')));saveOpenFolders();filePage=0;kind='all';await keepTreeScroll(renderPage);if(innerWidth<=720)document.querySelector('.library-main')?.scrollIntoView({block:'start'});return;}
    if(b.dataset.week!==undefined) {weekOffset=Number(b.dataset.week);b.disabled=true;weekly=await api('/api/weekly?weeks_ago='+weekOffset);return await renderPage();}
    if(b.dataset.layout) {libraryLayout=b.dataset.layout;try{localStorage.setItem('library-layout',libraryLayout);}catch{}return await renderPage();}
    if(b.dataset.page) {const target=b.dataset.scroll; if(target)Assistant.close(); await navigate(b.dataset.page); if(target)document.getElementById(target)?.scrollIntoView({behavior:'smooth',block:'start'}); return;}
    if(b.dataset.action==='assistant-open-now') return Assistant.open();
    if(b.dataset.action==='board-sync') {b.disabled=true;const r=await api('/api/board/sync','POST',{});boardInfo=await api('/api/board');await refresh();toast(r.added?`اتضاف ${r.added} تاسك من الكلية`:'مفيش تاسكات جديدة');return;}
    if(b.dataset.action==='delete-ai-key') {b.disabled=true;await api('/api/assistant/key','DELETE');aiKey=await api('/api/assistant/key');Assistant.reset();toast('اتمسح الـ key');return await renderPage();}
    if(b.dataset.course) {activeCourse=b.dataset.course;page='course';return await renderPage();}
    if(b.dataset.kind) {kind=b.dataset.kind;filePage=0;return await renderPage();}
    if(b.dataset.status) {status=b.dataset.status;return await renderPage();}
    if(b.dataset.filePage) {filePage=Number(b.dataset.filePage);return await renderPage();}
    if(b.dataset.newDay) return editor('slot',{}, {day:Number(b.dataset.newDay)});
    if(b.dataset.editTask) return editor('task',S.tasks.find(t=>t.id===b.dataset.editTask));
    if(b.dataset.editSlot) return editor('slot',S.slots.find(t=>t.id===b.dataset.editSlot));
    if(b.dataset.toggleTask) {const t=S.tasks.find(t=>t.id===b.dataset.toggleTask);b.disabled=true;await api(`/api/tasks/${t.id}`,'PUT',{...t,status:t.status==='done'?'todo':'done'});await refresh();return;}
    if(b.dataset.note) {activeNote=await api(`/api/notes/${b.dataset.note}`);page='note';return await renderPage();}
    const action=b.dataset.action;
    if(action==='close') return $('#modal').close();
    if(action==='timer-duration')return durationEditor(S.tasks.find(t=>t.id===b.dataset.id));
    if(action==='duration-preset'){
      const form=b.closest('form');form.elements.duration_minutes.value=b.dataset.minutes;updateDraftTimer();return;
    }
    if(['previous-day','next-day','today'].includes(action)){planDay=action==='today'?todayDate():shiftedDay(planDay,action==='next-day'?1:-1);return renderPage();}
    if(action==='enable-notifications')return await enableNotifications();
    if(action==='notification-settings')return await navigate('settings');
    if(action==='disable-notifications')return await disableNotifications();
    if(action==='test-notification'){
      if(!devicePush)throw new Error('فعّل إشعارات الجهاز الأول');
      b.disabled=true;await api('/api/notifications/push/test','POST',{endpoint:devicePush.endpoint});b.disabled=false;return toast('اتبعث إشعار تجريبي للجهاز ده');
    }
    if(['remind-start','remind-deadline','clear-reminder'].includes(action)){
      const form=$('#entry-form'), reminder=form.elements.remind_at;
      if(action==='clear-reminder'){reminder.value='';return;}
      let value;
      if(action==='remind-start'){
        if(!form.elements.planned_date.value||!form.elements.planned_time.value)throw new Error('حدد يوم التنفيذ ووقت البداية الأول');
        value=cairoISO(form.elements.planned_date.value+'T'+form.elements.planned_time.value);
      }else{
        if(!form.elements.due.value)throw new Error('حدد موعد التسليم الأول');
        value=new Date(new Date(cairoISO(form.elements.due.value)).getTime()-15*60000).toISOString();
      }
      reminder.value=cairoLocal(value);return;
    }
    if(action==='move-task-tomorrow'){
      const form=$('#entry-form');form.elements.planned_date.value=shiftedDay(todayDate(),1);return toast('يوم التنفيذ اتغيّر لبكرة. اضغط حفظ لتأكيده.');
    }
    if(['timer-start','timer-pause','timer-reset'].includes(action)){
      const data={action:action.split('-')[1]}, form=$('#entry-form');
      if(action==='timer-start'&&form?.dataset.id===b.dataset.id){
        if(!form.elements.duration_minutes.reportValidity())return;
        data.duration_minutes=Number(form.elements.duration_minutes.value);
        data.set_default=form.elements.use_default_duration.checked;
      }
      b.disabled=true;const task=await api(`/api/tasks/${b.dataset.id}/timer`,'POST',data);
      S.tasks=S.tasks.map(t=>t.id===task.id?task:t);
      if(data.set_default)S.task_defaults={duration_minutes:task.duration_minutes};
      if($('#modal').open){
        for(const box of document.querySelectorAll('[data-timer-box]'))if(box.dataset.timerBox===task.id)box.innerHTML=timerControls(timerPreview(task,box),!box.closest('#entry-form'));
        if(form?.dataset.id===task.id)form.elements.status.value=task.status;
        updateTimerDisplays();
      }else await renderPage();
      return;
    }
    if(action==='personal-course'||action==='college-course') return editor('course',{}, {kind:action==='college-course'?'college':'personal'});
    if(['course','task','slot','note','upload'].includes(action)) return editor(action);
    if(action==='edit-course') return editor('course',S.courses.find(c=>c.id===(b.dataset.id||activeCourse)));
    if(action==='edit-note') return editor('note',activeNote);
    if(action==='install') return await install();
    if(action==='logout') {await disableNotifications(true);await api('/api/logout','POST');return loginView();}
    if(action==='refresh') {b.disabled=true;if(page==='library')files=(await api('/api/vault'+(selectedCourse?'?course_id='+selectedCourse:''))).files;if(page==='archive')archive=await api('/api/archive');await refresh();return toast('بياناتك اتحدّثت');}
    if(action==='resource-task') return editor('task',{}, {title:b.dataset.title,resource:b.dataset.resource});
    if(action==='course-files') {selectedCourse=activeCourse;page='library';kind='all';query='';files=(await api('/api/vault?course_id='+selectedCourse)).files;return await renderPage();}
    if(action==='delete-course') {
      const c=S.courses.find(c=>c.id===b.dataset.id);
      const counts=[['tasks','مهام'],['slots','مواعيد'],['notes','نوتس'],['uploads','ملفات مرفوعة']].map(([table,label])=>[S[table].filter(entry=>entry.course_id===c.id).length,label]).filter(([count])=>count);
      modal('إزالة المادة من موادي؟',`<p dir="auto"><b>${E(c.title)}</b></p><p>${counts.length?`المحتوى المرتبط (${counts.map(([count,label])=>`${count} ${label}`).join('، ')}) هيفضل محفوظ تحت «عام».`:'هيتم إزالة المادة من قائمتك.'}</p><p class="small muted">ملفات Obsidian وأرشيف الكلية هتفضل موجودة.</p><div class="modal-actions">${act('confirm-remove-course','إزالة المادة','button danger',`data-id="${E(c.id)}"`)}${act('close','إلغاء','button secondary')}</div>`);return;
    }
    if(action==='confirm-remove-course') {
      b.disabled=true;await api(`/api/courses/${b.dataset.id}`,'DELETE');$('#modal').close();
      if(activeCourse===b.dataset.id){activeCourse='';page='courses';}
      if(selectedCourse===b.dataset.id)selectedCourse='';
      await refresh();return toast('المادة اتشالت، ومحتواها المرتبط محفوظ تحت «عام»');
    }
    if(action==='delete-entry'||action==='delete-upload') {
      const table=action==='delete-upload'?'uploads':{task:'tasks',slot:'slots',note:'notes'}[b.dataset.type];
      modal('حذف العنصر؟',`<p>العنصر ده هيتحذف من مساحتك.</p><div class="modal-actions">${act('confirm-delete','حذف','button danger',`data-table="${table}" data-id="${E(b.dataset.id)}"`)}${act('close','إلغاء','button secondary')}</div>`);return;
    }
    if(action==='confirm-delete') {b.disabled=true;await api(`/api/${b.dataset.table}/${b.dataset.id}`,'DELETE');$('#modal').close();if(page==='note'||page==='vnote')page='library';await refresh();return toast('تم الحذف');}
    if(action==='import-course') return modal('إضافة مادة من الأرشيف',`<form id="import-form"><p dir="auto">${E(b.dataset.title)}</p><div class="field"><label>أضفها جديدة، أو اربطها بمادة عندك</label><select name="existing_id">${courseOptions('','مادة جديدة')}</select></div>${input('account',b.dataset.account,'hidden')}${input('course',b.dataset.sourceCourse,'hidden')}<div class="error" id="form-error"></div><div class="modal-actions"><button class="button" type="submit">إضافة لموادي</button></div></form>`);
    if(action==='archive-files') {
      const result=await api(`/api/archive/${encodeURIComponent(b.dataset.account)}/${encodeURIComponent(b.dataset.sourceCourse)}/items`);
      return modal('ملفات المادة',result.items.length?result.items.map(i=>`<h3 dir="auto">${E(i.title)}</h3><p class="muted small">${E(i.category)}</p>${i.files.length?i.files.map(f=>`<div class="file-row"><div class="file-info"><a dir="auto" href="${E(f.url)}" target="_blank" rel="noopener">${E(f.name)}</a><small>${size(f.size)}</small></div>${act('resource-task',I('plus'),'icon-button',`data-title="${E('مذاكرة '+i.title)}" data-resource="${E(f.url)}" aria-label="إضافة مهمة"`)}</div>`).join(''):'<p class="muted small">مفيش ملف محفوظ للعنصر ده.</p>'}`).join(''):empty('مفيش محتوى محفوظ حاليًا','المحتوى الجديد هيظهر بعد مزامنة الأرشيف.'));
    }
  } catch(error) {toast(error.message); b.disabled=false;}
});
document.addEventListener('change',async event=>{
  const el=event.target;
  try {
    if(el.dataset.collegeCheck!==undefined){toggleCollegeFields(el.checked);return;}
    if(el.dataset.boardToggle!==undefined){await api('/api/board','PUT',{enabled:el.checked});boardInfo=await api('/api/board');toast(el.checked?'تاسكات الكلية هتتضاف أوتوماتيك':'وقفت إضافة تاسكات الكلية');return;}
    if(el.dataset.toggleTask){
      const task=S.tasks.find(t=>t.id===el.dataset.toggleTask);el.disabled=true;
      try{await api(`/api/tasks/${task.id}`,'PUT',{...task,status:el.checked?'done':'todo'});await refresh();}
      catch(error){el.checked=task.status==='done';el.disabled=false;throw error;}return;
    }
    if(el.dataset.change==='plan-day'){if(!el.value)return;planDay=el.value;}
    else if(el.dataset.change==='course-filter')selectedCourse=el.value;
    else if(el.dataset.change==='library-course'){selectedCourse=el.value;folderFilter='';files=(await api('/api/vault'+(selectedCourse?'?course_id='+selectedCourse:''))).files;filePage=0;}
    else if(el.dataset.change==='archive-year')archiveYear=el.value;
    else if(el.dataset.change==='archive-department')archiveDepartment=el.value;
    else if(el.dataset.change==='archive-term')archiveTerm=el.value;
    else return;
    await renderPage();
  }catch(error){toast(error.message);}
});
document.addEventListener('input',event=>{
  if(event.target.name==='duration_minutes'){updateDraftTimer();return;}
  if(!event.target.matches('[data-search]'))return;
  query=event.target.value;filePage=0;const cursor=event.target.selectionStart;
  renderPage().then(()=>{const el=$('[data-search]');el.focus();try{el.setSelectionRange(cursor,cursor);}catch{}});
});
document.addEventListener('submit',async event=>{
  const form=event.target;event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;
  try {
    if(form.id==='login-form') {const f=new FormData(form);await api('/api/login','POST',{username:f.get('username')||'',password:f.get('password')});await refresh(false);page='home';return await renderPage();}
    if(form.id==='signup-form') {await api('/api/signup','POST',Object.fromEntries(new FormData(form)));await refresh(false);page='home';toast('أهلًا بيك! ابدأ بإضافة موادك');return await renderPage();}
    if(form.id==='password-form') {await api('/api/account/password','POST',Object.fromEntries(new FormData(form)));toast('اتغيّر الباسورد، سجّل دخول تاني');return loginView();}
    const data=Object.fromEntries(new FormData(form));
    const useDefault=data.use_default_duration==='on';delete data.use_default_duration;
    if(form.id==='entry-form') {
      const type=form.dataset.type,id=form.dataset.id;
      if(type==='task'){data.due=cairoISO(data.due);data.remind_at=cairoISO(data.remind_at);data.duration_minutes=Number(data.duration_minutes);}
      if(type==='slot')data.day=Number(data.day);
      if(type==='upload'){
        if(data.file.size>512*1024*1024)throw new Error('الحد الأقصى للملف 512 ميجابايت');
        button.textContent='جارٍ رفع الملف…';
        const response=await fetch(`/api/uploads?course_id=${encodeURIComponent(data.course_id)}&name=${encodeURIComponent(data.file.name)}`,{method:'POST',body:data.file,credentials:'same-origin'});
        if(!response.ok){const error=await response.json();throw new Error(error.detail||'فشل رفع الملف');}
      }else{
        const table={course:'courses',task:'tasks',slot:'slots',note:'notes'}[type];
        const result=await api(`/api/${table}${id?'/'+id:''}`,id?'PUT':'POST',data);
        if(type==='task'&&useDefault)await api('/api/preferences/tasks','PUT',{duration_minutes:data.duration_minutes});
        if(type==='note'&&page==='note')activeNote=await api('/api/notes/'+result.id);
        if(type==='course'&&!id){activeCourse=result.id;page='course';kind='all';selectedCourse='';}
      }
    }else if(form.id==='duration-form'){
      const result=await api(`/api/tasks/${form.dataset.id}/timer`,'POST',{action:'configure',duration_minutes:Number(data.duration_minutes),set_default:useDefault});
      S.tasks=S.tasks.map(t=>t.id===result.id?result:t);
    }else if(form.id==='quick-task-form'){
      await api('/api/tasks','POST',{title:data.title,planned_date:planDay});
    }else if(form.id==='mount-form')await api('/api/vault/mounts','POST',data);
    else if(form.id==='import-form')await api('/api/archive/import','POST',data);
    else if(form.id==='ai-key-form'){const r=await api('/api/assistant/key','POST',data);aiKey=await api('/api/assistant/key');Assistant.reset();await renderPage();toast('الـ key شغال ✓ المساعد جاهز ('+r.model+')');return;}
    else if(form.id==='profile-form'){data.level=Number(data.level);await api('/api/profile/college','PUT',data);await refresh();toast('اتحفظت سنتك');return;}
    else if(form.id==='college-form'){await api('/api/account/college','POST',data);account=await api('/api/account');await refresh();toast('أرشيف الكلية اتفعّل عندك');return;}
    else return;
    if($('#modal').open)$('#modal').close();await refresh();toast('اتحفظت على سيرفرك');
  }catch(error){const el=form.querySelector('.error');if(el)el.textContent=error.message;else toast(error.message);}
  finally{button.disabled=false;}
});
document.addEventListener('input',event=>{if(event.target.dataset?.collegeCodeInput!==undefined)toggleCollegeFields(!!event.target.value.trim());});
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;});
window.addEventListener('online',()=>{if($('#app .connection-error'))return boot();if(S.tasks){toast('الاتصال رجع');refresh().catch(()=>toast('لسه مش قادر أوصل للسيرفر'));}});
window.addEventListener('offline',()=>toast('الاتصال مقطوع؛ البيانات تحتاج الوصول للسيرفر'));
setInterval(updateTimerDisplays,1000);
async function pollTasks(){
  if(polling||!S.tasks||!navigator.onLine)return;
  polling=true;
  try{
    const previous=JSON.stringify(S.tasks);await refresh(false);
    if($('#modal').open){for(const box of document.querySelectorAll('[data-timer-box]')){const t=S.tasks.find(t=>t.id===box.dataset.timerBox);if(t)box.innerHTML=timerControls(timerPreview(t,box),!box.closest('#entry-form'));}updateTimerDisplays();}
    else if(previous!==JSON.stringify(S.tasks)&&!document.activeElement?.matches('input,select,textarea'))await renderPage();
  }catch{}finally{polling=false;}
}
setInterval(pollTasks,15000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){if($('#app .connection-error'))boot();else pollTasks();}});
if('serviceWorker'in navigator)navigator.serviceWorker.addEventListener('message',event=>{if(event.data?.type==='open-task')openTask(event.data.id).catch(error=>toast(error.message));});
if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').then(reg=>{
  const offer=()=>{if(reg.waiting && navigator.serviceWorker.controller)toast('تحديث جديد متاح. اقفل التطبيق وافتحه لتفعيل التحديث.');};
  offer();reg.addEventListener('updatefound',()=>reg.installing?.addEventListener('statechange',offer));
}).catch(()=>{});
let booting=false,bootRetry;
async function boot(){
  if(booting)return;
  booting=true;clearTimeout(bootRetry);
  $('#app').innerHTML='<div class="boot">مساحتي الدراسية<span>بنحاول نوصل للموقع…</span></div>';
  try{const auth=await api('/api/auth');AUTH=auth;if(!auth.authenticated)return loginView();await refresh(false);await syncPushState().catch(()=>{});await renderPage();const params=new URLSearchParams(location.search),task=params.get('task'),voice=params.get('voice');if(voice){VoicePlayer.play(voice,'تذكير الديدلاين');history.replaceState(null,'','/'+(task?'?task='+encodeURIComponent(task):''));}if(task)await openTask(task);}
  catch(error){
    if(error.status===401)return loginView();
    const retryable=!error.status||error.status>=500;
    $('#app').innerHTML=`<div class="boot connection-error">الاتصال بالموقع انقطع<span>${E(error.message)}</span>${retryable?'<span>هنعيد الاتصال تلقائيًا، وتقدر تحاول دلوقتي.</span>':''}${act('reload','إعادة الاتصال','button secondary')}</div>`;
    if(retryable)bootRetry=setTimeout(boot,15000);
  }finally{booting=false;}
}
boot();
document.addEventListener('click',event=>{if(event.target.closest('[data-action="reload"]'))boot();});

/* ---------- in-app assistant ---------- */
const Assistant = (() => {
  let messages = [], busy = false, status = null;
  try { messages = JSON.parse(sessionStorage.getItem('assistant-chat') || '[]'); } catch {}
  const save = () => { try { sessionStorage.setItem('assistant-chat', JSON.stringify(messages.slice(-30))); } catch {} };
  const panel = () => $('#assistant'), log = () => $('#assistant-log');
  const suggestions = ['إيه اللي ورايا النهارده؟', 'عندي حل sheet بتاع NLP قبل الخميس، ومراجعة محاضرة ٣ بتاع ROB 307 بكرة', 'لخّصلي آخر محاضرة NLP', 'اعملي خطة مذاكرة للأسبوع ده'];
  const format = text => E(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>');
  function render() {
    if (!log()) return;
    const items = messages.map(m => m.role === 'user'
      ? `<div class="as-msg user" dir="auto">${format(m.content)}</div>`
      : `<div class="as-msg bot"><div dir="auto">${format(m.content)}</div>${(m.actions||[]).length ? `<ul class="as-actions">${m.actions.map(a => `<li><span class="as-tick" aria-hidden="true"></span><span><b>${E(a.label)}</b> <span dir="auto">${E(a.title)}</span>${a.detail ? `<small dir="auto">${E(a.detail)}</small>` : ''}</span></li>`).join('')}</ul>` : ''}</div>`).join('');
    const empty = status && !status.configured
      ? `<div class="as-empty"><p><b>المساعد محتاج API key.</b> هات key مجاني من Groq في دقيقتين وحطه في الإعدادات.</p><button type="button" class="button" data-page="settings" data-scroll="ai-key">حط الـ key بتاعك</button></div>`
      : `<div class="as-empty"><p>اكتبلي اللي عندك بالعربي أو English، وأنا أضيفه للمهام والجدول، أو أدوّرلك في نوتسك.</p>${suggestions.map(s => `<button type="button" class="as-suggest" data-assistant-suggest="${E(s)}">${E(s)}</button>`).join('')}</div>`;
    log().innerHTML = (messages.length ? items : empty) + (busy ? '<div class="as-msg bot as-typing" aria-label="بيكتب"><span></span><span></span><span></span></div>' : '');
    log().scrollTop = log().scrollHeight;
  }
  async function loadStatus() {
    if (status) return status;
    try { status = await StudyHttp.json('/api/assistant/status', {credentials:'same-origin'}); } catch { status = {configured:false}; return status; }
    const m = $('#assistant-model'); if (m) m.textContent = status.configured ? status.model + (status.fallback ? ' · احتياطي: ' + status.fallback : '') : 'محتاج إعداد';
    return status;
  }
  async function send(text) {
    text = (text || '').trim(); if (!text || busy) return;
    messages.push({role:'user', content:text}); busy = true; render(); save();
    try {
      const lang = window.StudyI18n?.lang() || 'ar';
      const result = await StudyHttp.json('/api/assistant', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({messages: messages.map(({role, content}) => ({role, content})), lang})}, {timeoutMs: 150000});
      messages.push({role:'assistant', content: result.reply || '…', actions: result.actions || []});
      if (result.changed) await refresh();
    } catch (error) {
      messages.push({role:'assistant', content: error.message || 'حصلت مشكلة، جرّب تاني.'});
    } finally { busy = false; render(); save(); }
  }
  async function open() { const p = panel(); if (!p) return; p.hidden = false; document.body.classList.add('assistant-open'); await loadStatus(); render(); setTimeout(() => $('#assistant-form textarea')?.focus(), 50); }
  function close() { const p = panel(); if (p) p.hidden = true; document.body.classList.remove('assistant-open'); $('#assistant-fab')?.focus(); }
  function setVisible(on) { const f = $('#assistant-fab'); if (f) f.hidden = !on; if (!on) close(); }
  document.addEventListener('click', event => {
    const b = event.target.closest('[data-assistant-open],[data-assistant-close],[data-assistant-suggest],[data-assistant-clear]'); if (!b) return;
    if (b.dataset.assistantOpen !== undefined) open();
    else if (b.dataset.assistantClose !== undefined) close();
    else if (b.dataset.assistantClear !== undefined) { messages = []; save(); render(); }
    else send(b.textContent.trim());
  });
  document.addEventListener('submit', event => {
    if (event.target.id !== 'assistant-form') return;
    event.preventDefault(); const field = event.target.elements.q; const text = field.value; field.value = ''; send(text);
  });
  document.addEventListener('keydown', event => {
    if (event.target.matches?.('#assistant-form textarea') && event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); event.target.form.requestSubmit(); }
    if (event.key === 'Escape' && !panel()?.hidden) close();
  });
  async function refreshVisibility() { await loadStatus(); setVisible(true); }
  function reset() { status = null; loadStatus(); }
  return {setVisible, open, refreshVisibility, reset, close};
})();

/* ---------- deadline voice reminders ---------- */
let voices = null, recorder = null, recordStage = '';
const VoicePlayer = (() => {
  let audio = null;
  const bar = () => $('#voice-banner');
  function show(url, title, playing) {
    const b = bar(); if (!b) return;
    b.innerHTML = `<span class="vb-icon" aria-hidden="true">${I('clock')}</span><span class="vb-text"><b>${E(title || 'تذكير الديدلاين')}</b></span>${playing ? `<button type="button" class="button secondary" data-voice-stop>إيقاف الصوت</button>` : `<button type="button" class="button" data-voice-play="${E(url)}" data-voice-title="${E(title||'')}">شغّل الفويس</button>`}<button type="button" class="as-icon" data-voice-close aria-label="إغلاق"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button>`;
    b.hidden = false;
  }
  async function play(url, title) {
    if (!url || !url.startsWith('/api/voices/')) return;
    try { audio?.pause(); } catch {}
    audio = new Audio(url);
    audio.addEventListener('ended', () => show(url, title, false));
    try { await audio.play(); show(url, title, true); }
    catch { show(url, title, false); }   // autoplay blocked: one tap plays it
  }
  function stop() { try { audio?.pause(); } catch {} }
  function hide() { stop(); const b = bar(); if (b) b.hidden = true; }
  return {play, stop, hide};
})();
function stageWhen(minutes) {
  if (minutes % 1440 === 0) return minutes === 1440 ? 'قبل الديدلاين بيوم' : `قبل الديدلاين بـ ${minutes/1440} أيام`;
  if (minutes % 60 === 0) return minutes === 60 ? 'قبل الديدلاين بساعة' : `قبل الديدلاين بـ ${minutes/60} ساعة`;
  return `قبل الديدلاين بـ ${minutes} دقيقة`;
}
function voicesView() {
  if (!voices) return '';
  const canRecord = !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  return `<section class="settings-block voices"><h2>تذكيرات الديدلاين بصوتك</h2>
    <p>أي مهمة ليها ديدلاين ولسه مخلصتش، هيوصلك عنها تذكير في كل مرحلة من دول. حط في كل مرحلة فويس أو أكتر، وكل تذكير هيشغّل واحد منهم عشوائي.</p>
    ${voices.stages.map(s => `<div class="voice-stage">
      <div class="voice-head"><div><b>${E(s.title)}</b><small>${stageWhen(s.minutes)}</small></div><span class="tag">${s.clips.length} فويس</span></div>
      ${s.clips.length ? `<div class="voice-clips">${s.clips.map(c => `<div class="voice-clip"><button type="button" class="as-icon" data-voice-play="${E(c.url)}" data-voice-title="${E(s.title)}" aria-label="تشغيل"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4v16l13-8z"/></svg></button><span dir="auto">${E(c.name)}</span><button type="button" class="button ghost" data-voice-delete="${E(s.id)}" data-voice-file="${E(c.file)}">حذف</button></div>`).join('')}</div>` : `<p class="small muted">لسه مفيش فويسات هنا، فالتذكير هيوصل بصوت الإشعار العادي.</p>`}
      <div class="voice-actions">
        <label class="button secondary">${I('plus')} رفع فويس<input type="file" accept="audio/*" multiple data-voice-upload="${E(s.id)}" hidden></label>
        ${canRecord ? `<button type="button" class="button secondary ${recordStage===s.id?'recording':''}" data-voice-record="${E(s.id)}">${recordStage===s.id?'وقّف التسجيل واحفظ':'سجّل بصوتك'}</button>` : ''}
        <button type="button" class="button ghost" data-voice-test="${E(s.id)}">جرّب المرحلة دي</button>
      </div></div>`).join('')}
    <p class="small">على الموبايل، الإشعار نفسه بيرن بصوت الإشعارات العادي، لأن المتصفحات مش بتسمح بصوت مخصوص. لما تدوس على الإشعار، أو لو الموقع مفتوح، الفويس بتاعك بيشتغل على طول.</p>
  </section>`;
}
async function reloadVoices() { voices = await api('/api/voices'); if (page === 'settings') await renderPage(); }
async function uploadVoice(stageId, blob, name) {
  if (blob.size > 10 * 1024 * 1024) throw new Error('الفويس لازم يكون أقل من ١٠ ميجا');
  const response = await fetch(`/api/voices/${encodeURIComponent(stageId)}?name=${encodeURIComponent(name)}`, {method:'POST', body:blob, credentials:'same-origin'});
  if (!response.ok) { let detail = ''; try { detail = (await response.json()).detail; } catch {} throw new Error(detail || 'فشل رفع الفويس'); }
}
document.addEventListener('click', async event => {
  const b = event.target.closest('[data-voice-play],[data-voice-stop],[data-voice-close],[data-voice-delete],[data-voice-test],[data-voice-record]'); if (!b) return;
  try {
    if (b.dataset.voicePlay !== undefined) return VoicePlayer.play(b.dataset.voicePlay, b.dataset.voiceTitle);
    if (b.dataset.voiceStop !== undefined) return VoicePlayer.hide();
    if (b.dataset.voiceClose !== undefined) return VoicePlayer.hide();
    if (b.dataset.voiceDelete !== undefined) { b.disabled = true; await api(`/api/voices/${encodeURIComponent(b.dataset.voiceDelete)}/${encodeURIComponent(b.dataset.voiceFile)}`, 'DELETE'); toast('اتحذف الفويس'); return await reloadVoices(); }
    if (b.dataset.voiceTest !== undefined) { b.disabled = true; await api(`/api/voices/${encodeURIComponent(b.dataset.voiceTest)}/test`, 'POST', {}); toast('اتبعت إشعار تجريبي لكل أجهزتك'); b.disabled = false; return; }
    if (b.dataset.voiceRecord !== undefined) {
      const id = b.dataset.voiceRecord;
      if (recorder && recordStage === id) { recorder.stop(); return; }
      if (recorder) return toast('خلّص التسجيل اللي شغال الأول');
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      const type = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(t => MediaRecorder.isTypeSupported?.(t)) || '';
      const chunks = []; recorder = new MediaRecorder(stream, type ? {mimeType:type} : undefined); recordStage = id;
      recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const mime = recorder.mimeType || type || 'audio/webm'; recorder = null; recordStage = '';
        const ext = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
        try { await uploadVoice(id, new Blob(chunks, {type:mime}), `تسجيل ${new Date().toLocaleString('en-GB',{hour12:false}).replace(/[/:,]/g,'-')}.${ext}`); toast('اتحفظ التسجيل'); }
        catch (error) { toast(error.message); }
        await reloadVoices();
      };
      recorder.start(); toast('بيسجّل… دوس تاني عشان توقف وتحفظ'); await renderPage();
    }
  } catch (error) { toast(error.message || 'حصلت مشكلة'); b.disabled = false; }
});
document.addEventListener('change', async event => {
  const input = event.target.closest?.('input[data-voice-upload]'); if (!input || !input.files.length) return;
  try { for (const file of input.files) await uploadVoice(input.dataset.voiceUpload, file, file.name); toast('اترفع الفويس'); }
  catch (error) { toast(error.message); }
  input.value = ''; await reloadVoices();
});
if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', event => {
  if (event.data?.type === 'play-voice') VoicePlayer.play(event.data.url, event.data.title?.replace(/^مساحتي — /, ''));
});

/* ---------- accounts (multi-user site) ---------- */
let AUTH = {}, account = null, newToken = null;
const userName = () => (S.user?.name || '');
const firstName = () => userName().trim().split(/\s+/)[0];
const VAULT_EXT = ['md','pdf','ppt','pptx','doc','docx','xls','xlsx','csv','txt','ipynb','py','zip','mp4','mp3','wav','png','jpg','jpeg','webp','gif','svg','canvas','html'];
function accountView() {
  if (!AUTH.multiuser || !account) return '';
  const used = account.used_mb, quota = account.quota_mb, pct = Math.min(100, Math.round(used / quota * 100));
  return `<section class="settings-block account-block"><h2>حسابك</h2>
    <div class="account-row"><div class="avatar">${E(firstName().slice(0,1))}</div><div><b dir="auto">${E(account.user.name)}</b><small dir="ltr">@${E(account.user.username)}</small></div></div>
    <div class="quota"><div class="quota-bar"><span style="width:${pct}%"></span></div><small>${used} من ${quota} ميجا مستخدمين</small></div>
    <h3>أرشيف الكلية</h3>
    ${account.user.college ? `<p>✓ حسابك متسجّل كطالب في ${E(AUTH.college_name || 'الكلية')}، وأرشيف الكلية ظاهر عندك.</p>`
      : `<p>أرشيف الكلية وتاسكات الكلية لطلاب ${E(AUTH.college_name || 'الكلية')} بس.${AUTH.college_code ? ' لو انت منهم، اكتب كود الكلية اللي في جروب الدفعة واختار سنتك.' : ' لو انت منهم، اختار سنتك وفعّله من هنا.'}</p>
         <form id="college-form" class="form-grid college-join">${AUTH.college_code ? '<label for="college-code">كود الكلية</label><input id="college-code" name="code" dir="ltr" required>' : ''}${collegeFields().replace('data-college-select','required')}<button type="submit" class="button secondary">أنا من الكلية</button></form>`}
    <h3>وصّل Claude بمساحتك (MCP)</h3>
    <p>اعمل token، وحط الرابط في Claude ← Settings ← Connectors ← Add custom connector. Claude هيقدر يقرا ويضيف مهامك ومواعيدك ونوتسك.</p>
    ${newToken ? `<div class="token-box"><label for="token-url">رابط الـ connector (بيظهر مرة واحدة بس)</label><div class="token-row"><input id="token-url" readonly dir="ltr" value="${E(newToken.connector_url)}"><button type="button" class="button" data-copy-token>نسخ</button></div><p class="small">متبعتش الرابط ده لحد، هو زي الباسورد.</p></div>` : ''}
    <div class="toolbar">${act('create-token', account.token_created ? 'اعمل token جديد (القديم هيقف)' : 'اعمل token')}${account.token_created ? act('revoke-token','وقّف الـ token','button ghost') : ''}</div>
    <h3>تغيير الباسورد</h3>
    <form id="password-form" class="form-inline"><label class="sr-only" for="pw-old">الباسورد الحالي</label><input id="pw-old" name="old" type="password" autocomplete="current-password" placeholder="الباسورد الحالي" required><label class="sr-only" for="pw-new">الباسورد الجديد</label><input id="pw-new" name="new" type="password" autocomplete="new-password" minlength="8" placeholder="الباسورد الجديد (٨ حروف+)" required><button class="button secondary" type="submit">غيّر الباسورد</button></form>
  </section>`;
}
let aiKey = null, boardInfo = null;
const L = (ar, en) => window.StudyI18n?.lang() === 'en' ? en : ar;
function aiKeyView() {
  if (!aiKey) return '';
  const p = aiKey.providers || {}, groq = p.groq || {};
  const current = aiKey.set ? `<div class="notice">✓ المساعد شغال بالـ key بتاعك: <b>${E(p[aiKey.provider]?.name || aiKey.provider)}</b> · <span dir="ltr">${E(aiKey.model)}</span> · <span dir="ltr">••••${E(aiKey.last4)}</span></div>
    <div class="toolbar">${act('assistant-open-now','جرّب المساعد','button')}${act('delete-ai-key','امسح الـ key','button ghost')}</div>` : '';
  return `<section class="settings-block ai-key" id="ai-key"><h2>المساعد الذكي: الـ API key بتاعك</h2>
    <p>المساعد بيضيفلك مهام ومواعيد من رسالة واحدة، وبيدوّر في نوتسك. عشان يشتغل محتاج API key مجاني من مزوّد AI. كل واحد بيحط الـ key بتاعه، فمحدش بيستهلك حدود التاني والمساعد بيفضل سريع لما ناس كتير تستخدمه في نفس الوقت.</p>
    ${current}
    <details class="ai-guide" data-no-i18n ${aiKey.set ? '' : 'open'}>${L(`<summary>إزاي تجيب key مجاني من Groq (دقيقتين)</summary>
      <ol>
        <li>افتح <a href="${E(groq.keys_url || 'https://console.groq.com/keys')}" target="_blank" rel="noopener noreferrer" dir="ltr">console.groq.com/keys</a> وسجّل دخول بحساب Google. مجاني ومش محتاج فيزا.</li>
        <li>دوس <b>Create API Key</b>، اكتب أي اسم (مثلاً <span dir="ltr">StudyHub</span>)، وبعدين <b>Submit</b>.</li>
        <li>انسخ الـ key. بيبدأ بـ <code dir="ltr">gsk_</code> وبيظهر <b>مرة واحدة بس</b>، فانسخه على طول.</li>
        <li>ارجع هنا، اختار <b>Groq</b>، الصق الـ key، ودوس <b>حفظ وتجربة</b>.</li>
      </ol>
      <p class="small">الـ key بيتحفظ <b>مشفّر</b> على السيرفر، ومحدش يقدر يشوفه ولا حتى صاحب الموقع من الواجهة. متبعتهوش لحد ولا تحطه في شات، وتقدر تمسحه أو تعمل واحد جديد من Groq في أي وقت.</p>`,
      `<summary>How to get a free key from Groq (2 minutes)</summary>
      <ol>
        <li>Open <a href="${E(groq.keys_url || 'https://console.groq.com/keys')}" target="_blank" rel="noopener noreferrer">console.groq.com/keys</a> and sign in with Google. It's free, no card needed.</li>
        <li>Click <b>Create API Key</b>, give it any name (for example StudyHub), then <b>Submit</b>.</li>
        <li>Copy the key. It starts with <code>gsk_</code> and is shown <b>only once</b>, so copy it right away.</li>
        <li>Come back here, pick <b>Groq</b>, paste the key and click <b>Save and test</b>.</li>
      </ol>
      <p class="small">Your key is stored <b>encrypted</b> on the server; nobody can see it in the app, not even the site owner. Never send it to anyone or paste it in a chat. You can delete it here or create a new one on Groq any time.</p>`)}
    </details>
    <form id="ai-key-form" class="form-grid college-join">
      <div class="field"><label for="ai-provider">المزوّد</label><select id="ai-provider" name="provider">${Object.entries(p).map(([k,v])=>`<option value="${k}" ${(aiKey.provider||'groq')===k?'selected':''}>${E(v.name)}${k==='groq'?L(' (الأسهل)',' (easiest)'):''}</option>`).join('')}</select></div>
      <div class="field"><label for="ai-key-input">الـ API key</label><input id="ai-key-input" name="key" type="password" dir="ltr" autocomplete="off" spellcheck="false" placeholder="gsk_..." required></div>
      <button type="submit" class="button">حفظ وتجربة</button><div class="error field full" role="alert"></div>
    </form></section>`;
}
function collegeProfileView() {
  if (!S.college?.college) return '';
  const c=S.college;
  return `<section class="settings-block"><h2>سنتك في الكلية</h2>
    <p>على أساسها بتوصلك تاسكات الكلية من قناة الدفعة، وبعدين أرشيف سنتك.</p>
    <form id="profile-form" class="form-grid college-join">${collegeFields(c.level||'', c.program||'').replace('data-college-select','required')}<button type="submit" class="button secondary">حفظ</button></form>
    ${boardInfo && boardInfo.available ? `<h3>تاسكات الكلية من قناة الدفعة</h3>
    <p data-no-i18n>${L('أي assignment بينزل على قنوات الكلية لسنتك وبرنامجك بيتضاف لمهامك لوحده، بالديدلاين والمادة ولينك البوست. البيانات جاية من','Every assignment posted on the faculty channels for your level and program is added to your tasks automatically, with the deadline, the course and a link to the post. The data comes from')} <a href="https://github.com/moustafasayed068/hnu-board" target="_blank" rel="noopener noreferrer">HNU Board</a> ${L('اللي عمله مصطفى سيد.','by Moustafa Sayed.')}</p>
    <label class="checkbox-option"><input type="checkbox" data-board-toggle ${boardInfo.enabled?'checked':''}> ضيف تاسكات الكلية لمهامي أوتوماتيك</label>
    <div class="toolbar">${act('board-sync','زامن دلوقتي','button secondary')}<span class="small muted">${boardInfo.last ? 'آخر مزامنة: '+dateFormat(new Date(boardInfo.last*1000),{weekday:'short',hour:'2-digit',minute:'2-digit'}) : 'لسه متزامنتش'}${boardInfo.imported ? ' · '+boardInfo.imported+' بوست' : ''}${boardInfo.error ? ' · آخر محاولة فشلت' : ''}</span></div>` : ''}</section>`;
}
function vaultUploadView() {
  return `<section class="settings-block"><h2>فولدرات Obsidian بتاعتك</h2>
    <p>ارفع فولدر النوتس بتاعك، أو ملف ZIP منه، وهيظهر في المكتبة. لما تحدّث نوتسك ارفعه تاني بنفس الاسم وهيتحدّث.</p>
    ${S.mounts.length ? S.mounts.map(m=>`<div class="mount"><b dir="auto">${E(m)}</b><button type="button" class="button ghost" data-vault-remove="${E(m)}">حذف</button></div>`).join('') : '<p class="small muted">لسه مرفعتش فولدرات.</p>'}
    <div class="toolbar"><label class="button">${I('plus')} رفع فولدر<input type="file" webkitdirectory directory multiple hidden data-vault-folder></label><label class="button secondary">${I('plus')} رفع ZIP<input type="file" accept=".zip,application/zip" hidden data-vault-zip></label></div>
    <div id="vault-progress" class="small" role="status" aria-live="polite"></div>
    <p class="small">على الموبايل استخدم ZIP. الملفات اللي بتترفع: نوتس md، و pdf، و pptx، و docx، و xlsx، وصور، وكود. فولدر .obsidian مش بيترفع.</p></section>`;
}
async function uploadBlob(url, blob) {
  const response = await fetch(url, {method:'POST', body:blob, credentials:'same-origin'});
  let data = {}; try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.detail || 'فشل الرفع');
  return data;
}
document.addEventListener('change', async event => {
  const folderInput = event.target.closest?.('input[data-vault-folder]'), zipInput = event.target.closest?.('input[data-vault-zip]');
  if (!folderInput && !zipInput) return;
  const progress = $('#vault-progress');
  try {
    if (zipInput && zipInput.files[0]) {
      const file = zipInput.files[0]; progress.textContent = `بيرفع ${file.name}…`;
      const result = await uploadBlob(`/api/vault/upload-zip?name=${encodeURIComponent(file.name)}`, file);
      toast(`اترفع ${result.files} ملف في ${result.folder}`);
    } else if (folderInput && folderInput.files.length) {
      const files = [...folderInput.files].filter(f => { const p = f.webkitRelativePath || f.name; return !p.split('/').some(x => x.startsWith('.')) && VAULT_EXT.includes((p.split('.').pop()||'').toLowerCase()); });
      if (!files.length) throw new Error('مفيش ملفات مدعومة في الفولدر ده');
      let done = 0, failed = 0;
      const queue = [...files];
      const worker = async () => { while (queue.length) { const f = queue.shift();
        try { await uploadBlob(`/api/vault/upload-file?path=${encodeURIComponent(f.webkitRelativePath || f.name)}`, f); } catch (error) { failed++; if (/مساحتك|المساحة/.test(error.message)) { queue.length = 0; toast(error.message); } }
        done++; progress.textContent = `اترفع ${done} من ${files.length}${failed ? ` · ${failed} فشلوا` : ''}`; } };
      await Promise.all([worker(), worker(), worker(), worker()]);
      toast(`خلص رفع ${done - failed} ملف`);
    }
    await refresh(false); if (AUTH.multiuser) account = await api('/api/account').catch(()=>account);
    await renderPage();
  } catch (error) { toast(error.message); if (progress) progress.textContent = ''; }
  event.target.value = '';
});
document.addEventListener('click', async event => {
  const b = event.target.closest('[data-vault-remove],[data-copy-token],[data-auth-mode]'); if (!b) return;
  try {
    if (b.dataset.authMode) return loginView(b.dataset.authMode);
    if (b.dataset.copyToken !== undefined) { const input = $('#token-url'); input.select(); try { await navigator.clipboard.writeText(input.value); } catch { document.execCommand('copy'); } return toast('اتنسخ الرابط'); }
    if (b.dataset.vaultRemove) {
      if (!confirm(`تحذف فولدر «${b.dataset.vaultRemove}» من السيرفر؟ النسخة اللي على جهازك مش هتتأثر.`)) return;
      b.disabled = true; await api(`/api/vault/folder?name=${encodeURIComponent(b.dataset.vaultRemove)}`, 'DELETE');
      await refresh(false); account = await api('/api/account').catch(()=>account); toast('اتحذف الفولدر'); return await renderPage();
    }
  } catch (error) { toast(error.message); b.disabled = false; }
});

/* StudyHub study tools: exams + study plan, flashcards (spaced repetition), competitive programming,
   the calendar link and sharing a course. Loaded after app.js and uses its helpers (S, api, E, I, act,
   modal, field, …); app.js calls into window.StudyExtra for these pages. */
'use strict';
(function(){
  const KINDS = () => ({final:L('فاينال','Final'),midterm:L('ميدترم','Midterm'),quiz:L('كويز','Quiz'),practical:L('عملي','Practical'),oral:L('شفوي','Oral')});
  const WEIGHTS = () => [[1,L('خفيف','Light')],[2,L('سهل','Easy')],[3,L('متوسط','Medium')],[4,L('تقيل','Heavy')],[5,L('تقيل جدًا','Very heavy')]];
  const DAY_SHORT = () => window.StudyI18n?.lang()==='en' ? ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'] : dayNames;
  let plan = null, planSettings = null, cards = null, cardCourse = '', session = null, cp = null, calendar = null, calendarLink = null, drafts = null;

  const daysUntil = day => Math.round((new Date(day+'T12:00:00Z') - new Date(todayDate()+'T12:00:00Z')) / 86400000);
  function countdown(day) {
    const n = daysUntil(day);
    if (n < 0) return L('خلص','Done');
    if (n === 0) return L('النهارده','Today');
    if (n === 1) return L('بكرة','Tomorrow');
    return L(`بعد ${n} يوم`,`In ${n} days`);
  }
  const examName = e => (e.course_id && S.courses.find(c=>c.id===e.course_id)?.title) || e.title || L('امتحان','Exam');
  const examLabel = e => e.title && !e.course_id ? e.title : `${examName(e)} · ${KINDS()[e.kind]||''}`;
  const upcoming = () => (S.exams||[]).filter(e => daysUntil(e.date) >= 0);
  const longDate = (day, time) => dateFormat(day+'T'+(time||'12:00')+':00+03:00',{weekday:'long',day:'numeric',month:'long'})+(time?' · '+time:'');

  /* ------------------------------------------------------------ exams + plan */
  function examsView() {
    const next = upcoming(), past = (S.exams||[]).filter(e => daysUntil(e.date) < 0).reverse();
    const planTasks = S.tasks.filter(t => t.plan), planDone = planTasks.filter(t => t.status==='done').length;
    const ps = plan?.request || planSettings || {minutes_per_day:180, session_minutes:50, rest_days:[]};
    return heading(L('الامتحانات وخطة المذاكرة','Exams and study plan'), L('ضيف جدول امتحاناتك، والموقع يوزّع المذاكرة على الأيام اللي فاضلة.','Add your exam timetable and StudyHub spreads the studying over the days left.'), act('x-exam', I('plus')+' '+L('امتحان جديد','New exam'))) +
      `<section class="panel"><div class="panel-head"><h2>${L('الامتحانات الجاية','Upcoming exams')}</h2><span class="muted small">${next.length}</span></div>` +
      (next.length ? `<div class="exam-list">${next.map(e => `<div class="exam-row ${daysUntil(e.date)<=1?'soon':''}"><div class="exam-count"><b>${daysUntil(e.date)}</b><small>${L('يوم','days')}</small></div><div class="exam-main"><button type="button" class="task-title" dir="auto" data-x="exam-edit" data-ref="${E(e.id)}">${E(examLabel(e))}</button><div class="task-meta"><span>${E(longDate(e.date,e.time))}</span>${e.location?`<span dir="auto">${E(e.location)}</span>`:''}<span class="tag">${E(countdown(e.date))}</span><span class="weight" title="${E(L('تقل المادة','How heavy'))}">${'●'.repeat(e.weight)}${'○'.repeat(5-e.weight)}</span></div></div></div>`).join('')}</div>`
        : empty(L('مفيش امتحانات لسه','No exams yet'), L('ضيف أول امتحان بتاريخه، وبعدها اعمل خطة مذاكرة.','Add your first exam with its date, then make a study plan.'), 'x-exam', L('ضيف امتحان','Add an exam'))) + `</section>` +
      `<section class="panel plan-panel"><div class="panel-head"><h2>${L('خطة المذاكرة','Study plan')}</h2>${planTasks.length?`<span class="muted small">${planDone} / ${planTasks.length} ${L('جلسة خلصت','sessions done')}</span>`:''}</div><div class="panel-body">` +
      `<form id="plan-form" class="form-grid">${field(L('وقت المذاكرة في اليوم','Study time per day'), select('minutes_per_day', [60,90,120,180,240,300,360,480].map(m=>[String(m), m<120?L(`${m} دقيقة`,`${m} min`):L(`${m/60} ساعات`,`${m/60} hours`)]), String(ps.minutes_per_day)))}${field(L('طول الجلسة','Session length'), select('session_minutes', [25,40,50,60,90].map(m=>[String(m), L(`${m} دقيقة`,`${m} min`)]), String(ps.session_minutes)))}` +
      `<fieldset class="field full rest-days"><legend>${L('أيام الراحة','Rest days')}</legend>${DAY_SHORT().map((d,i)=>`<label class="chip-check"><input type="checkbox" name="rest" value="${i}" ${ps.rest_days.includes(i)?'checked':''}> ${E(d)}</label>`).join('')}</fieldset>` +
      `<div class="field full plan-actions"><button type="submit" class="button" data-plan="preview" ${next.length?'':'disabled'}>${L('شوف الخطة','Preview the plan')}</button>${planTasks.some(t=>t.status!=='done')?act('x-plan-clear', L('امسح الخطة','Clear the plan'), 'button ghost'):''}</div><div class="error" role="alert"></div></form>` +
      (plan ? planPreview() : `<p class="small muted">${L('اليوم اللي قبل كل امتحان بيبقى مراجعة نهائية ليه، والباقي بيروح للامتحان الأقرب والأتقل. الجلسات بتنزل كمهام في «قائمة يومي».','The day before each exam is its final review; other days go to the closest, heaviest exam. Sessions appear as tasks in "My day".')}</p>`) +
      `</div></section>` +
      (past.length ? `<details class="panel past-exams"><summary class="panel-head"><h2>${L('امتحانات خلصت','Past exams')}</h2><span class="muted small">${past.length}</span></summary>${past.map(e=>`<div class="exam-row past"><div class="exam-main"><button type="button" class="task-title" dir="auto" data-x="exam-edit" data-ref="${E(e.id)}">${E(examLabel(e))}</button><div class="task-meta"><span>${E(longDate(e.date,e.time))}</span></div></div></div>`).join('')}</details>` : '');
  }
  function planPreview() {
    const max = Math.max(...plan.by_exam.map(x=>x.minutes));
    return `<div class="plan-preview"><p><b>${L(`${plan.sessions} جلسة على ${plan.days} يوم`,`${plan.sessions} sessions over ${plan.days} days`)}</b> · ${E(dateFormat(plan.first+'T12:00:00Z'))} → ${E(dateFormat(plan.last+'T12:00:00Z'))}</p>` +
      plan.by_exam.map(x => { const e = (S.exams||[]).find(e=>e.id===x.exam_id); return `<div class="plan-bar"><span dir="auto">${E(e?examLabel(e):x.label)}</span><div class="wk-track"><span style="width:${Math.round(x.minutes/max*100)}%"></span></div><small>${Math.round(x.minutes/60*10)/10} ${L('ساعة','h')}</small></div>`; }).join('') +
      `<div class="modal-actions">${act('x-plan-make', L('اعتمد الخطة وضيفها لمهامي','Use this plan and add it to my tasks'))}${act('x-plan-cancel', L('إلغاء','Cancel'), 'button secondary')}</div><p class="small muted">${L('لو عندك خطة قديمة، الجلسات اللي لسه ماخلصتش هتتبدّل. اللي خلصتها بتفضل.','Unfinished sessions of an older plan are replaced; finished ones stay.')}</p></div>`;
  }
  function examEditor(item={}) {
    const kinds = KINDS();
    modal(item.id?L('تعديل الامتحان','Edit exam'):L('امتحان جديد','New exam'), `<form id="exam-form" data-ref="${E(item.id||'')}"><div class="form-grid">` +
      field(L('المادة','Course'), `<select name="course_id">${courseOptions(item.course_id||'', L('من غير مادة','No course'))}</select>`) +
      field(L('نوع الامتحان','Exam type'), select('kind', Object.entries(kinds), item.kind||'final')) +
      field(L('اسم الامتحان · لو مالوش مادة','Exam name · if it has no course'), input('title', item.title||'', 'text', 'maxlength="200"'), true) +
      field(L('اليوم','Date'), input('date', item.date||'', 'date', 'required')) + field(L('الساعة · اختياري','Time · optional'), input('time', item.time||'', 'time')) +
      field(L('المدة بالدقايق','Duration in minutes'), input('duration_minutes', item.duration_minutes??120, 'number', 'min="0" max="600"')) + field(L('المكان','Place'), input('location', item.location||'', 'text', 'maxlength="200"')) +
      field(L('المادة تقيلة قد إيه؟','How heavy is it?'), select('weight', WEIGHTS().map(([v,t])=>[String(v),t]), String(item.weight||3)), true) +
      `</div><div class="error" role="alert"></div><div class="modal-actions"><button type="submit" class="button">${L('حفظ','Save')}</button>${act('close',L('إلغاء','Cancel'),'button secondary')}${item.id?act('x-exam-delete',L('حذف','Delete'),'button danger',`data-ref="${E(item.id)}"`):''}</div></form>`);
  }

  /* ------------------------------------------------------------ flashcards */
  function nextInterval(card, grade) {
    const ease = card.ease||2.5, interval = card.interval||0, reps = card.reps||0;
    if (grade==='again') return 0;
    if (grade==='hard') return Math.max(1, Math.round(interval*1.2));
    if (grade==='good') return reps===0?1:reps===1?3:Math.max(interval+1, Math.round(interval*ease));
    return reps===0?4:Math.max(interval+1, Math.round(interval*ease*1.3));
  }
  const intervalText = d => d===0 ? L('تاني دلوقتي','again now') : d===1 ? L('بكرة','1 day') : d<30 ? L(`${d} يوم`,`${d} days`) : L(`${Math.round(d/30)} شهر`,`${Math.round(d/30)} mo`);
  function reviewView() {
    if (!cards) return '';
    const list = cards.cards.filter(c => !cardCourse || c.course_id===cardCourse);
    const due = list.filter(c => c.due <= cards.today);
    const head = heading(L('المراجعة بالكروت','Flashcard review'), L('أسئلة قصيرة بتراجعها على أيام متباعدة، عشان المعلومة تثبت قبل الامتحان.','Short questions reviewed on spaced days so they stick before the exam.'), act('x-card', I('plus')+' '+L('كارت جديد','New card')));
    if (session) return head + sessionView();
    return head + `<div class="stats cards-stats">${[[L('عليك النهارده','Due today'),due.length],[L('كل الكروت','All cards'),list.length],[L('اتعلمتها','Learned'),list.filter(c=>c.reps>0).length],[L('راجعت النهارده','Reviewed today'),cards.reviewed_today]].map(([a,b])=>`<div class="stat"><div class="stat-top">${a}</div><b>${b}</b></div>`).join('')}</div>` +
      `<div class="toolbar">${act('x-review-start', L(`ابدأ المراجعة (${due.length})`,`Start review (${due.length})`), 'button', due.length?'':'disabled')}${act('x-cards-from-note', I('file')+' '+L('كروت من نوت','Cards from a note'), 'button secondary')}<select data-x-change="card-course" aria-label="${E(L('المادة','Course'))}">${courseOptions(cardCourse, L('كل المواد','All courses'))}</select></div>` +
      `<section class="panel"><div class="panel-head"><h2>${L('الكروت','Cards')}</h2><span class="muted small">${list.length}</span></div>` +
      (list.length ? list.slice(0,80).map(c => `<div class="card-row"><button type="button" class="card-front" dir="auto" data-x="card-edit" data-ref="${E(c.id)}">${E(c.front)}</button><div class="task-meta"><span dir="auto">${E(c.back.slice(0,120))}</span><span>${E(courseName(c.course_id))}</span><span class="tag ${c.due<=cards.today?'green':'grey'}">${c.due<=cards.today?L('عليك النهارده','Due today'):E(dateFormat(c.due+'T12:00:00Z'))}</span></div></div>`).join('') + (list.length>80?`<p class="small muted panel-body">${L(`وكمان ${list.length-80} كارت`,`and ${list.length-80} more`)}</p>`:'')
        : empty(L('لسه مفيش كروت','No cards yet'), L('اكتب كارت بإيدك، أو طلّع كروت من نوت عندك (بصيغة Obsidian «سؤال :: إجابة» أو بالمساعد).','Write a card, or make cards from one of your notes (Obsidian "question :: answer" syntax, or with the assistant).'), 'x-card', L('كارت جديد','New card'))) + `</section>`;
  }
  function sessionView() {
    const card = session.queue[0];
    if (!card) return `<section class="panel review-done"><div class="empty"><b>${L('خلصت مراجعة النهارده 🎉','Done for today 🎉')}</b>${L(`راجعت ${session.done} كارت.`,`You reviewed ${session.done} cards.`)}<div>${act('x-review-end', L('رجوع للكروت','Back to the cards'))}</div></div></section>`;
    return `<section class="review-card" aria-live="polite"><div class="review-top"><span>${E(courseName(card.course_id))}</span><span>${L(`فاضل ${session.queue.length}`,`${session.queue.length} left`)}</span></div>` +
      `<div class="review-front" dir="auto">${E(card.front)}</div>` +
      (session.flipped ? `<div class="review-back" dir="auto">${E(card.back)}</div><div class="grade-row">${[['again',L('تاني','Again'),'danger'],['hard',L('صعب','Hard'),'secondary'],['good',L('تمام','Good'),''],['easy',L('سهل','Easy'),'secondary']].map(([g,t,c],n)=>`<button type="button" class="button ${c}" data-x="grade" data-ref="${g}"><span>${t}</span><small>${E(intervalText(nextInterval(card,g)))}</small><kbd>${n+1}</kbd></button>`).join('')}</div>`
        : `<div class="grade-row">${act('x-flip', L('اظهر الإجابة','Show answer')+' <kbd>Space</kbd>', 'button')}</div>`) +
      `<div class="review-bottom">${act('x-review-end', L('وقّف المراجعة','Stop'), 'button ghost')}</div></section>`;
  }
  function cardEditor(item={}) {
    modal(item.id?L('تعديل الكارت','Edit card'):L('كارت جديد','New card'), `<form id="card-form" data-ref="${E(item.id||'')}"><div class="form-grid">` +
      field(L('السؤال','Question'), `<textarea name="front" required maxlength="2000" rows="3" dir="auto">${E(item.front||'')}</textarea>`, true) +
      field(L('الإجابة','Answer'), `<textarea name="back" required maxlength="4000" rows="4" dir="auto">${E(item.back||'')}</textarea>`, true) +
      field(L('المادة','Course'), `<select name="course_id">${courseOptions(item.course_id ?? cardCourse, L('من غير مادة','No course'))}</select>`, true) +
      `</div><div class="error" role="alert"></div><div class="modal-actions"><button type="submit" class="button">${L('حفظ','Save')}</button>${act('close',L('إلغاء','Cancel'),'button secondary')}${item.id?act('x-card-delete',L('حذف','Delete'),'button danger',`data-ref="${E(item.id)}"`):''}</div></form>`);
  }
  async function notePicker() {
    const vault = (await api('/api/vault').catch(()=>({files:[]}))).files.filter(f=>f.kind==='note');
    const options = [...S.notes.map(n=>[`note:${n.id}`, n.title]), ...vault.map(f=>[`path:${f.path}`, f.path])];
    modal(L('كروت من نوت','Cards from a note'), options.length ? `<form id="draft-form"><div class="form-grid">${field(L('النوت','Note'), `<select name="source" required>${options.map(([v,t])=>`<option value="${E(v)}">${E(t)}</option>`).join('')}</select>`, true)}` +
      field(L('عدد الكروت (للمساعد)','How many (assistant)'), select('count', [5,10,15,20].map(n=>[String(n),String(n)]), '10')) +
      `</div><p class="small muted">${L('«استخرج» بيقرا الكروت المكتوبة في النوت بصيغة Obsidian: «سؤال :: إجابة»، أو سؤال وتحته سطر فيه ? وبعده الإجابة. «ولّد بالمساعد» بيكتب أسئلة من محتوى النوت.','"Extract" reads cards already written in the note in Obsidian syntax: "question :: answer", or a question, a line with ?, then the answer. "Generate" writes questions from the note with the assistant.')}</p><div class="error" role="alert"></div><div class="modal-actions"><button type="submit" class="button" data-mode="extract">${L('استخرج من النوت','Extract from the note')}</button><button type="submit" class="button secondary" data-mode="generate">${L('ولّد بالمساعد','Generate with the assistant')}</button>${act('close',L('إلغاء','Cancel'),'button ghost')}</div></form>`
      : `<p>${L('مفيش نوتس لسه. اكتب نوت أو اربط فولدر Obsidian من الإعدادات.','No notes yet. Write a note or connect an Obsidian folder in the settings.')}</p>`);
  }
  function draftsView() {
    modal(L('اختار الكروت اللي تحفظها','Pick the cards to keep'), drafts.cards.length ? `<form id="drafts-save"><div class="draft-list">${drafts.cards.map((c,i)=>`<label class="draft"><input type="checkbox" name="pick" value="${i}" checked><span><b dir="auto">${E(c.front)}</b><span dir="auto">${E(c.back)}</span></span></label>`).join('')}</div>` +
      `<div class="form-grid">${field(L('المادة','Course'), `<select name="course_id">${courseOptions(drafts.course_id||cardCourse, L('من غير مادة','No course'))}</select>`, true)}</div><div class="error" role="alert"></div><div class="modal-actions"><button type="submit" class="button">${L(`احفظ المختار`,`Save the selected`)}</button>${act('close',L('إلغاء','Cancel'),'button secondary')}</div></form>`
      : `<p>${L('مالقيتش كروت بالصيغة دي في النوت. جرّب «ولّد بالمساعد».','No cards in that syntax in this note. Try "Generate with the assistant".')}</p><div class="modal-actions">${act('x-cards-from-note',L('رجوع','Back'),'button secondary')}</div>`);
  }

  /* ------------------------------------------------------------ competitive programming */
  const cfRankClass = rank => (rank||'').replace(/\s+/g,'-');
  function cpView() {
    if (!cp) return '';
    const h = cp.handles, has = h.codeforces || h.leetcode;
    const top = heading(L('البرمجة التنافسية','Competitive programming'), L('Codeforces وLeetCode في مكان واحد: الريتنج، المسائل اللي حليتها، مسألة كل يوم والمسابقات الجاية.','Codeforces and LeetCode together: rating, solved problems, a daily problem and upcoming contests.'), has?`<div class="section-actions">${act('x-cp-refresh', I('refresh')+' '+L('تحديث','Refresh'), 'button secondary')}${act('x-cp-handles', I('edit')+' '+L('الحسابات','Accounts'), 'button ghost')}</div>`:'');
    if (!has) return top + `<section class="settings-block cp-setup"><h2>${L('اربط حساباتك','Link your accounts')}</h2><p>${L('اكتب الهاندل بتاعك، والموقع هيجيب أرقامك من الـ API العام. مش محتاج باسورد.','Enter your handles; StudyHub reads your numbers from the public APIs. No password needed.')}</p>${handlesForm()}</section>` + contestsPanel();
    const errors = Object.entries(cp.errors||{}).filter(([,v])=>v==='network').map(([k])=>k==='codeforces'?'Codeforces':'LeetCode');
    return top + (errors.length?`<div class="notice warn">${L(`مش قادر أوصل لـ ${errors.join(' و')} دلوقتي، دي آخر أرقام اتجابت.`,`Can't reach ${errors.join(' and ')} right now; showing the last numbers.`)}</div>`:'') +
      `<div class="cp-grid">${cp.codeforces?cfCard(cp.codeforces):''}${cp.leetcode?lcCard(cp.leetcode):''}</div>` + dailyPanel() + contestsPanel() + recentPanel();
  }
  function handlesForm() {
    const h = cp.handles;
    return `<form id="cp-form" class="form-grid">${field('Codeforces handle', input('codeforces', h.codeforces, 'text', 'maxlength="40" autocomplete="off" dir="ltr" placeholder="tourist"'))}${field('LeetCode username', input('leetcode', h.leetcode, 'text', 'maxlength="40" autocomplete="off" dir="ltr" placeholder="neal_wu"'))}` +
      `<label class="field full check-line"><input type="checkbox" name="contests_in_calendar" ${h.contests_in_calendar!==false?'checked':''}> ${L('حط المسابقات الجاية في لينك التقويم بتاعي','Put upcoming contests in my calendar link')}</label><div class="error" role="alert"></div><div class="field full"><button type="submit" class="button">${L('حفظ','Save')}</button></div></form>`;
  }
  function statBox(label, value, sub='') { return `<div class="cp-stat"><small>${label}</small><b>${value ?? '—'}</b>${sub?`<span>${sub}</span>`:''}</div>`; }
  function cfCard(s) {
    return `<section class="panel cp-card"><div class="panel-head"><h2>Codeforces</h2><a class="button ghost" href="${E(s.url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${E(s.handle)} ↗</a></div><div class="cp-stats">` +
      statBox(L('الريتنج','Rating'), s.rating, `<span class="cf-rank ${E(cfRankClass(s.rank))}">${E(s.rank)}</span>`) + statBox(L('أعلى ريتنج','Max rating'), s.max_rating, E(s.max_rank)) +
      statBox(L('مسائل اتحلت','Solved'), s.solved, L(`${s.solved_week} الأسبوع ده`,`${s.solved_week} this week`)) + statBox(L('أيام ورا بعض','Streak'), s.streak, L('يوم','days')) + `</div>` +
      (s.top_tags?.length?`<div class="cp-tags">${s.top_tags.map(([t,n])=>`<span class="tag grey" dir="ltr">${E(t)} · ${n}</span>`).join('')}</div>`:'') + `</section>`;
  }
  function lcCard(s) {
    return `<section class="panel cp-card"><div class="panel-head"><h2>LeetCode</h2><a class="button ghost" href="${E(s.url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${E(s.username)} ↗</a></div><div class="cp-stats">` +
      statBox(L('مسائل اتحلت','Solved'), s.solved, `<span class="lc-e">${s.easy}</span> · <span class="lc-m">${s.medium}</span> · <span class="lc-h">${s.hard}</span>`) +
      statBox(L('ريتنج المسابقات','Contest rating'), s.rating, L(`${s.contests} مسابقة`,`${s.contests} contests`)) + statBox(L('أيام ورا بعض','Streak'), s.streak, L('يوم','days')) + statBox(L('أيام نشاط','Active days'), s.active_days) + `</div></section>`;
  }
  function problemRow(p, platform, meta) {
    return `<div class="contest-row"><span class="platform ${platform.toLowerCase()}">${platform}</span><div class="contest-main"><a href="${E(p.url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${E(p.name||p.title)}</a><small>${meta}</small></div>${act('x-cp-task', I('plus')+' '+L('لمهامي','To my tasks'), 'button ghost', `data-ref="${E(p.url)}" data-title="${E(p.name||p.title)}"`)}</div>`;
  }
  function dailyPanel() {
    const d = cp.daily||{};
    if (!d.codeforces && !d.leetcode) return '';
    return `<section class="panel"><div class="panel-head"><h2>${L('مسألة النهارده','Problem of the day')}</h2></div>` +
      (d.codeforces?problemRow(d.codeforces,'Codeforces',L(`ريتنج ${d.codeforces.rating} · أعلى من مستواك بشوية`,`Rated ${d.codeforces.rating} · a little above your level`)):'') +
      (d.leetcode?problemRow(d.leetcode,'LeetCode',E(d.leetcode.difficulty)+' · '+L('مسألة LeetCode اليومية','LeetCode daily question')):'') + `</section>`;
  }
  function contestsPanel() {
    const list = cp.contests||[];
    return `<section class="panel"><div class="panel-head"><h2>${L('المسابقات الجاية','Upcoming contests')}</h2><span class="muted small">Codeforces · LeetCode</span></div>` +
      (list.length ? list.map(c => { const start = new Date(c.start); const local = cairoLocal(c.start); return `<div class="contest-row"><span class="platform ${c.platform.toLowerCase()}">${c.platform}</span><div class="contest-main"><a href="${E(c.url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${E(c.name)}</a><small>${E(dateFormat(start,{weekday:'long',day:'numeric',month:'short'}))} · ${E(local.slice(11))} · ${Math.round(c.duration_minutes/6)/10} ${L('ساعة','h')} · ${E(countdown(local.slice(0,10)))}</small></div>${act('x-contest-task', I('calendar')+' '+L('لجدولي','To my plan'), 'button ghost', `data-ref="${E(c.id)}"`)}</div>`; }).join('')
        : `<div class="panel-body small muted">${L('مفيش مسابقات معروفة في الأسبوعين الجايين، أو المصادر مش بترد دلوقتي.','No contests in the next two weeks, or the sources are not answering right now.')}</div>`) + `</section>`;
  }
  function recentPanel() {
    const recent = [...(cp.codeforces?.recent||[]).map(r=>({...r,platform:'Codeforces'})), ...(cp.leetcode?.recent||[]).map(r=>({...r,platform:'LeetCode'}))].sort((a,b)=>b.at-a.at).slice(0,8);
    if (!recent.length) return '';
    return `<section class="panel"><div class="panel-head"><h2>${L('آخر مسائل حليتها','Recently solved')}</h2></div>${recent.map(r=>`<div class="contest-row"><span class="platform ${r.platform.toLowerCase()}">${r.platform}</span><div class="contest-main"><a href="${E(r.url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${E(r.name)}</a><small>${r.rating?E(r.rating)+' · ':''}${E(dateFormat(new Date(r.at*1000)))}</small></div></div>`).join('')}</section>`;
  }

  /* ------------------------------------------------------------ settings: calendar link */
  function calendarBlock() {
    const on = calendar?.active;
    return `<section class="settings-block" id="calendar-link"><h2>${L('ربط بتقويم جوجل أو الآيفون','Google or iPhone calendar')}</h2><p>${L('لينك سري بيخلّي مواعيد التسليم، المهام اللي ليها ساعة، جدولك الأسبوعي والامتحانات تظهر في تقويم موبايلك وبتتحدّث لوحدها.','A private link that puts your deadlines, timed tasks, weekly timetable and exams in your phone\'s calendar and keeps them updated.')}</p>` +
      (calendarLink ? `<div class="token-box"><input id="calendar-url" readonly value="${E(calendarLink.url)}" dir="ltr" aria-label="${E(L('لينك التقويم','Calendar link'))}">${act('x-copy-calendar', L('نسخ','Copy'), 'button secondary')}</div>` +
          `<ol class="small calendar-steps"><li>${L('Google Calendar على الكمبيوتر: «Other calendars» ← «+» ← «From URL» والصق اللينك.','Google Calendar on a computer: "Other calendars" → "+" → "From URL" and paste the link.')}</li><li>${L('آيفون: افتح','iPhone: open')} <a href="${E(calendarLink.webcal)}">${L('اللينك ده','this link')}</a> ${L('واختار «Subscribe».','and choose "Subscribe".')}</li><li>${L('اللينك بيظهر مرة واحدة. احفظه، ومتبعتوش لحد.','The link is shown once. Keep it private.')}</li></ol>`
        : `<p class="small">${on ? L('اللينك شغّال. لو ضاع منك اعمل لينك جديد، والقديم هيقف.','A link is active. If you lost it, make a new one; the old one stops.') : L('لسه مفيش لينك.','No link yet.')}</p>`) +
      `<div class="form-inline">${act('x-calendar-new', on?L('لينك جديد','New link'):L('اعمل لينك التقويم','Make my calendar link'), on?'button secondary':'button')}${on?act('x-calendar-stop', L('وقّف اللينك','Stop the link'), 'button ghost'):''}</div></section>`;
  }

  /* ------------------------------------------------------------ sharing a course */
  async function shareModal(courseId) {
    const status = await api(`/api/courses/${courseId}/share`);
    const course = S.courses.find(c=>c.id===courseId);
    modal(L('شارك المادة مع زمايلك','Share the course with friends'), `<p dir="auto"><b>${E(course?.title||'')}</b></p><p>${L('أي حد معاه الكود بياخد نسخة خاصة بيه من النوتس والمهام المفتوحة والمواعيد والامتحانات والكروت. ملفات Obsidian والملفات المرفوعة مش بتتنقل.','Anyone with the code gets their own copy of the notes, open tasks, timetable, exams and cards. Obsidian files and uploads are not copied.')}</p>` +
      (status.active ? `<div class="share-code" dir="ltr">${E(status.code)}</div><p class="small muted">${L('الكود شغّال لحد','The code works until')} ${E(dateFormat(new Date(status.expires*1000)))}</p><div class="modal-actions">${act('x-copy-share', L('نسخ الكود','Copy the code'), 'button', `data-ref="${E(status.code)}"`)}${act('x-share-stop', L('وقّف الكود','Stop the code'), 'button danger', `data-ref="${E(courseId)}"`)}${act('close',L('إغلاق','Close'),'button secondary')}</div>`
        : `<div class="modal-actions">${act('x-share-make', I('share')+' '+L('اعمل كود مشاركة','Make a share code'), 'button', `data-ref="${E(courseId)}"`)}${act('close',L('إلغاء','Cancel'),'button secondary')}</div>`));
  }
  function importModal(preview=null, code='') {
    modal(L('مادة من زميل','A course from a friend'), preview ? `<p>${L(`<b dir="auto">${E(preview.owner)}</b> شاركك:`,`<b dir="auto">${E(preview.owner)}</b> shared:`)}</p><p dir="auto"><b>${E(preview.course)}</b></p><p class="small">${[['notes',L('نوتس','notes')],['tasks',L('مهام','tasks')],['slots',L('مواعيد','slots')],['exams',L('امتحانات','exams')],['cards',L('كروت','cards')]].map(([k,t])=>`${preview.counts[k]} ${t}`).join(' · ')}</p>` +
        (preview.own ? `<p class="notice">${L('ده كود مادة من عندك إنت.','This is a code for one of your own courses.')}</p><div class="modal-actions">${act('close',L('إغلاق','Close'),'button secondary')}</div>` : `<div class="modal-actions">${act('x-share-import', L('ضيفها لموادي','Add it to my courses'), 'button', `data-ref="${E(code)}"`)}${act('close',L('إلغاء','Cancel'),'button secondary')}</div>`)
      : `<form id="share-code-form"><div class="form-grid">${field(L('كود المشاركة','Share code'), input('code', code, 'text', 'required maxlength="12" autocomplete="off" dir="ltr" placeholder="ABCD2345"'), true)}</div><div class="error" role="alert"></div><div class="modal-actions"><button type="submit" class="button">${L('دوّر','Look it up')}</button>${act('close',L('إلغاء','Cancel'),'button secondary')}</div></form>`);
  }

  /* ------------------------------------------------------------ overview strip */
  function homeStrip() {
    const next = upcoming().slice(0,3), due = S.cards_due||0;
    if (!next.length && !due) return '';
    return `<div class="home-strip">${next.map(e=>`<button type="button" class="strip-item ${daysUntil(e.date)<=2?'soon':''}" data-page="exams">${I('exam')}<span><b dir="auto">${E(examLabel(e))}</b><small>${E(countdown(e.date))}</small></span></button>`).join('')}${due?`<button type="button" class="strip-item" data-page="review">${I('cards')}<span><b>${L(`${due} كارت عليك النهارده`,`${due} cards due today`)}</b><small>${L('مراجعة سريعة','Quick review')}</small></span></button>`:''}</div>`;
  }

  /* ------------------------------------------------------------ events */
  async function reloadCards() { cards = await api('/api/cards'); }
  async function draftsFrom(form, mode) {
    const [type, ...rest] = form.elements.source.value.split(':'); const value = rest.join(':');
    const body = {count:Number(form.elements.count.value), lang:window.StudyI18n?.lang()||'ar', ...(type==='note'?{note_id:value}:{path:value})};
    drafts = await api('/api/cards/'+mode, 'POST', body); draftsView();
  }
  async function addTask(task) { await api('/api/tasks','POST',task); await refresh(); toast(L('اتضافت لمهامك','Added to your tasks')); }
  function copy(text) { navigator.clipboard?.writeText(text).then(()=>toast(L('اتنسخ','Copied'))).catch(()=>toast(text)); }

  document.addEventListener('click', async event => {
    const b = event.target.closest('button,a'); if (!b) return;
    const x = b.dataset.x || (b.dataset.action||'').replace(/^x-/, '');
    if (!b.dataset.x && !(b.dataset.action||'').startsWith('x-') && !['share-course','import-share'].includes(b.dataset.action)) return;
    try {
      if (x==='exam') return examEditor();
      if (x==='exam-edit') return examEditor(S.exams.find(e=>e.id===b.dataset.ref));
      if (x==='exam-delete') { b.disabled=true; await api('/api/exams/'+b.dataset.ref,'DELETE'); $('#modal').close(); await refresh(); return toast(L('اتمسح الامتحان','Exam deleted')); }
      if (x==='plan-make') { b.disabled=true; const r = await api('/api/exams/plan','POST',{...plan.request, dry_run:false}); plan=null; planSettings=await api('/api/exams/plan/settings'); await refresh(); return toast(L(`اتضافت ${r.created} جلسة لمهامك`,`${r.created} sessions added to your tasks`)); }
      if (x==='plan-cancel') { plan=null; return renderPage(); }
      if (x==='plan-clear') { b.disabled=true; const r = await api('/api/exams/plan','DELETE'); await refresh(); return toast(L(`اتشالت ${r.removed} جلسة`,`${r.removed} sessions removed`)); }
      if (x==='card') return cardEditor();
      if (x==='card-edit') return cardEditor(cards.cards.find(c=>c.id===b.dataset.ref));
      if (x==='card-delete') { b.disabled=true; await api('/api/cards/'+b.dataset.ref,'DELETE'); $('#modal').close(); await reloadCards(); await refresh(); return toast(L('اتمسح الكارت','Card deleted')); }
      if (x==='cards-from-note') return await notePicker();
      if (x==='review-start') { const today = cards.today; session = {queue: cards.cards.filter(c=>c.due<=today && (!cardCourse||c.course_id===cardCourse)), flipped:false, done:0}; return renderPage(); }
      if (x==='flip') { session.flipped = true; return renderPage(); }
      if (x==='grade') return await grade(b.dataset.ref, b);
      if (x==='review-end') { session=null; await reloadCards(); return refresh(); }
      if (x==='cp-handles') return modal(L('حساباتك','Your accounts'), handlesForm());
      if (x==='cp-refresh') { b.disabled=true; cp = await api('/api/cp?refresh=true'); await renderPage(); return toast(L('الأرقام اتحدّثت','Numbers updated')); }
      if (x==='cp-task') return await addTask({title:b.dataset.title.slice(0,300), planned_date:todayDate(), details:b.dataset.ref});
      if (x==='contest-task') { const c = cp.contests.find(c=>c.id===b.dataset.ref); const local = cairoLocal(c.start); return await addTask({title:('🏁 '+c.name).slice(0,300), planned_date:local.slice(0,10), planned_time:local.slice(11,16), duration_minutes:c.duration_minutes, details:c.url}); }
      if (x==='calendar-new') { b.disabled=true; calendarLink = await api('/api/calendar/token?lang='+(window.StudyI18n?.lang()||'ar'),'POST'); calendar = {active:true}; await renderPage(); document.getElementById('calendar-link')?.scrollIntoView({block:'center'}); return; }
      if (x==='calendar-stop') { b.disabled=true; await api('/api/calendar/token','DELETE'); calendar = {active:false}; calendarLink=null; await renderPage(); return toast(L('اللينك وقف','The link stopped')); }
      if (x==='copy-calendar') return copy(calendarLink.url);
      if (b.dataset.action==='share-course') return await shareModal(b.dataset.id);
      if (x==='share-make') { b.disabled=true; await api(`/api/courses/${b.dataset.ref}/share`,'POST'); return await shareModal(b.dataset.ref); }
      if (x==='share-stop') { b.disabled=true; await api(`/api/courses/${b.dataset.ref}/share`,'DELETE'); return await shareModal(b.dataset.ref); }
      if (x==='copy-share') return copy(b.dataset.ref);
      if (b.dataset.action==='import-share') return importModal();
      if (x==='share-import') { b.disabled=true; const r = await api(`/api/shares/${encodeURIComponent(b.dataset.ref)}/import`,'POST'); $('#modal').close(); await refresh(false); activeCourse=r.course_id; page='course'; await renderPage(); return toast(L('المادة اتضافت لموادك','The course was added')); }
    } catch (error) { toast(error.message); b.disabled=false; }
  });
  async function grade(g, b) {
    const card = session.queue.shift(); if (b) b.disabled = true;
    try { const updated = await api(`/api/cards/${card.id}/review`,'POST',{grade:g}); if (g==='again') session.queue.push(updated); else session.done++; }
    catch (error) { session.queue.unshift(card); toast(error.message); }
    session.flipped = false; await renderPage();
  }
  document.addEventListener('keydown', event => {
    if (page!=='review' || !session?.queue.length || event.target.closest?.('input,textarea,select') || $('#modal').open) return;
    if (!session.flipped && (event.key===' ' || event.key==='Enter')) { event.preventDefault(); session.flipped=true; renderPage(); }
    else if (session.flipped && ['1','2','3','4'].includes(event.key)) { event.preventDefault(); grade(['again','hard','good','easy'][Number(event.key)-1]); }
  });
  document.addEventListener('change', async event => {
    if (event.target.dataset.xChange==='card-course') { cardCourse = event.target.value; await renderPage(); }
  });
  document.addEventListener('submit', async event => {
    const form = event.target, ids = ['exam-form','plan-form','card-form','draft-form','drafts-save','cp-form','share-code-form'];
    if (!ids.includes(form.id)) return;
    event.preventDefault();
    const button = event.submitter || form.querySelector('[type="submit"]'), error = form.querySelector('.error');
    button.disabled = true; if (error) error.textContent = '';
    const data = Object.fromEntries(new FormData(form));
    try {
      if (form.id==='exam-form') {
        const body = {...data, duration_minutes:Number(data.duration_minutes||0), weight:Number(data.weight)};
        await api('/api/exams'+(form.dataset.ref?'/'+form.dataset.ref:''), form.dataset.ref?'PUT':'POST', body);
        $('#modal').close(); await refresh(); return toast(L('اتحفظ الامتحان','Exam saved'));
      }
      if (form.id==='plan-form') {
        const request = {lang:window.StudyI18n?.lang()||'ar', minutes_per_day:Number(data.minutes_per_day), session_minutes:Number(data.session_minutes), rest_days:[...form.querySelectorAll('[name="rest"]:checked')].map(c=>Number(c.value))};
        plan = {...await api('/api/exams/plan','POST',{...request, dry_run:true}), request};
        return renderPage();
      }
      if (form.id==='card-form') {
        if (form.dataset.ref) await api('/api/cards/'+form.dataset.ref,'PUT',data);
        else await api('/api/cards','POST',{cards:[data]});
        $('#modal').close(); await reloadCards(); await refresh(); return toast(L('اتحفظ الكارت','Card saved'));
      }
      if (form.id==='draft-form') { button.textContent = L('لحظة…','One moment…'); return await draftsFrom(form, event.submitter?.dataset.mode||'extract'); }
      if (form.id==='drafts-save') {
        const picked = [...form.querySelectorAll('[name="pick"]:checked')].map(c=>drafts.cards[Number(c.value)]);
        if (!picked.length) throw new Error(L('اختار كارت واحد على الأقل','Pick at least one card'));
        const r = await api('/api/cards','POST',{cards:picked.map(c=>({...c, course_id:data.course_id, source:drafts.source}))});
        $('#modal').close(); drafts=null; await reloadCards(); await refresh();
        return toast(L(`اتحفظ ${r.added} كارت`,`${r.added} cards saved`)+(r.skipped?L(` (${r.skipped} كانوا موجودين)`,` (${r.skipped} already there)`):''));
      }
      if (form.id==='cp-form') {
        cp = await api('/api/cp','PUT',{codeforces:data.codeforces||'', leetcode:data.leetcode||'', contests_in_calendar:!!data.contests_in_calendar});
        if ($('#modal').open) $('#modal').close(); await renderPage(); return toast(L('اتحفظت حساباتك','Accounts saved'));
      }
      if (form.id==='share-code-form') { const code = data.code.trim(); return importModal(await api('/api/shares/'+encodeURIComponent(code)), code); }
    } catch (e) { if (error) error.textContent = e.message; else toast(e.message); }
    finally { button.disabled = false; }
  });

  window.StudyExtra = {
    pages: {
      exams: {view: examsView, load: async () => { plan = null; planSettings = await api('/api/exams/plan/settings').catch(()=>null); }},
      review: {view: reviewView, load: async () => { session = null; await reloadCards(); }},
      cp: {view: cpView, load: async () => { cp = await api('/api/cp'); }},
    },
    after() {},
    homeStrip,
    settingsBlocks: () => calendarBlock(),
    async loadSettings() { calendar = await api('/api/calendar').catch(()=>null); calendarLink = null; },
  };
})();

"""Weekly review numbers for the dashboard page and the MCP weekly_review tool."""
import json
import time
from datetime import date, datetime, timedelta, time as clock_time
from zoneinfo import ZoneInfo

from . import app as core

CAIRO = ZoneInfo('Africa/Cairo')


def week_start(day):
    """Saturday that starts the Sat–Fri study week containing `day`."""
    return day - timedelta(days=(day.weekday() - 5) % 7)


def focus_sessions():
    """Logged focus segments plus the live part of any timer that is running right now."""
    with core.database() as db:
        sessions = [json.loads(r['data']) for r in db.execute('SELECT data FROM study_sessions')]
    now = time.time()
    for task in core.rows('tasks'):
        timer = task.get('timer') or {}
        start = timer.get('segment_start')
        if timer.get('state') == 'running' and start:
            end = min(now, timer.get('ends_at') or now)
            if end - start >= 30:
                sessions.append({'task_id': task['id'], 'course_id': task.get('course_id', ''),
                                 'start': start, 'end': end, 'minutes': round((end - start) / 60, 1), 'live': True})
    return sessions


def local_day(ts):
    return datetime.fromtimestamp(ts, CAIRO).date()


def weekly_report(weeks_ago=0):
    today = datetime.now(CAIRO).date()
    start_day = week_start(today) - timedelta(weeks=weeks_ago)
    days = [start_day + timedelta(days=i) for i in range(7)]
    start_ts = datetime.combine(start_day, clock_time(0), CAIRO).timestamp()
    end_ts = start_ts + 7 * 86400
    prev_start, prev_end = start_ts - 7 * 86400, start_ts
    courses = {c['id']: c['title'] for c in core.rows('courses')}
    tasks = core.rows('tasks')
    sessions = focus_sessions()

    def minutes_between(a, b):
        return sum(s['minutes'] for s in sessions if a <= s['start'] < b)

    per_day = {d: 0.0 for d in days}
    per_course = {}
    week_sessions = [s for s in sessions if start_ts <= s['start'] < end_ts]
    for s in week_sessions:
        d = local_day(s['start'])
        if d in per_day:
            per_day[d] += s['minutes']
        name = courses.get(s.get('course_id', ''), 'عام')
        per_course.setdefault(name, {'minutes': 0.0, 'done': 0})['minutes'] += s['minutes']

    def done_in(a, b):
        return [t for t in tasks if t.get('status') == 'done' and a <= (t.get('updated') or 0) < b]

    done = done_in(start_ts, end_ts)
    for t in done:
        per_course.setdefault(courses.get(t.get('course_id', ''), 'عام'), {'minutes': 0.0, 'done': 0})['done'] += 1

    def due_ts(t):
        try:
            return datetime.fromisoformat(t['due']).timestamp() if t.get('due') else None
        except ValueError:
            return None

    due_week = [t for t in tasks if due_ts(t) and start_ts <= due_ts(t) < end_ts]
    now = time.time()
    missed = [t for t in due_week if t.get('status') != 'done' and due_ts(t) < now]

    # streak: consecutive days (ending today, or yesterday if today is still empty) with focus time or a finished task
    active_days = {local_day(s['start']) for s in sessions} | {local_day(t['updated']) for t in tasks
                                                             if t.get('status') == 'done' and t.get('updated')}
    streak, cursor = 0, today if today in active_days else today - timedelta(days=1)
    while cursor in active_days:
        streak += 1
        cursor -= timedelta(days=1)

    # 12-week heatmap of focus minutes (columns = weeks, rows = Sat..Fri)
    heat_start = week_start(today) - timedelta(weeks=11)
    heat = {}
    for s in sessions:
        d = local_day(s['start'])
        if d >= heat_start:
            heat[d] = heat.get(d, 0) + s['minutes']
    heatmap = [[round(heat.get(heat_start + timedelta(weeks=w, days=i), 0)) if heat_start + timedelta(weeks=w, days=i) <= today else None
                for i in range(7)] for w in range(12)]

    def view(t):
        return {'id': t['id'], 'title': t['title'], 'course': courses.get(t.get('course_id', ''), ''),
                'due': t.get('due', ''), 'updated': t.get('updated')}

    total = sum(per_day.values())
    planned = [t for t in tasks if start_day.isoformat() <= (t.get('planned_date') or '') <= days[-1].isoformat()]
    return {
        'week_start': start_day.isoformat(), 'week_end': days[-1].isoformat(), 'weeks_ago': weeks_ago,
        'today': today.isoformat(),
        'focus_minutes': round(total), 'focus_minutes_prev': round(minutes_between(prev_start, prev_end)),
        'sessions': len(week_sessions),
        'avg_session_minutes': round(total / len(week_sessions)) if week_sessions else 0,
        'done_count': len(done), 'done_prev': len(done_in(prev_start, prev_end)),
        'planned_count': len(planned), 'planned_done': sum(t.get('status') == 'done' for t in planned),
        'created_count': sum(start_ts <= (t.get('created') or 0) < end_ts for t in tasks),
        'days': [{'date': d.isoformat(), 'weekday': (d.weekday() + 1) % 7, 'minutes': round(per_day[d])} for d in days],
        'courses': sorted(({'name': k, 'minutes': round(v['minutes']), 'done': v['done']} for k, v in per_course.items()),
                          key=lambda c: (-c['minutes'], -c['done'])),
        'done': [view(t) for t in sorted(done, key=lambda t: -(t.get('updated') or 0))],
        'missed': [view(t) for t in missed],
        'streak_days': streak,
        'heatmap_start': heat_start.isoformat(), 'heatmap': heatmap,
        'daily_goal_minutes': core.setting('daily_goal_minutes', 90),
    }

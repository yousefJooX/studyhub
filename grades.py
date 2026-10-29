"""College grades: sheets, quizzes, the midterm and the final, per college course.

Each grade is a score out of a maximum. A course can also have a marks split (how many of the course
marks each kind is worth, e.g. sheets 10, quizzes 10, midterm 20, final 60). With a split, a kind
counts as its share of those marks; without one, the course total is the plain sum of the scores.
Sending the same course + kind + title again updates that grade, so a re-sent sheet is not doubled.
"""
import re
import time
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from . import app as core

router = APIRouter(prefix='/api/grades', dependencies=[Depends(core.authorized)])
KINDS = ('sheet', 'quiz', 'midterm', 'final')
KINDS_EN = {'sheet': 'Sheet', 'quiz': 'Quiz', 'midterm': 'Midterm', 'final': 'Final'}
NUMBERED = ('sheet', 'quiz')  # these come many per course; the midterm and the final come once


class Grade(BaseModel):
    course_id: str = Field(min_length=1)
    kind: str = Field(default='sheet', pattern='^(sheet|quiz|midterm|final)$')
    title: str = Field(default='', max_length=120)
    score: float = Field(ge=0, le=1000)
    out_of: float = Field(gt=0, le=1000)
    date: str = ''
    note: str = Field(default='', max_length=500)

    @field_validator('date')
    @classmethod
    def valid_date(cls, value):
        if value:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError('Use YYYY-MM-DD')
            date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def score_fits(self):
        if self.score > self.out_of:
            raise ValueError('The score is more than the maximum')
        return self


class GradeBatch(BaseModel):
    grades: list[Grade] = Field(min_length=1, max_length=100)


class Split(BaseModel):
    sheet: float = Field(default=0, ge=0, le=1000)
    quiz: float = Field(default=0, ge=0, le=1000)
    midterm: float = Field(default=0, ge=0, le=1000)
    final: float = Field(default=0, ge=0, le=1000)


def college_course(course_id):
    course = core.get('courses', course_id)
    if course.get('kind') != 'college':
        raise HTTPException(400, 'الدرجات لمواد الكلية بس')
    return course


def key(grade):
    return grade['course_id'], grade['kind'], grade.get('title', '').strip().lower()


def add_grades(batch: GradeBatch):
    """Add or update (same course + kind + title) → {'added', 'updated', 'grades'}."""
    existing = {key(g): g for g in core.rows('grades')}
    added = updated = 0
    saved = []
    for body in batch.grades:
        college_course(body.course_id)
        value = body.model_dump() | {'title': body.title.strip()}
        if not value['title'] and body.kind in NUMBERED:
            count = sum(1 for k in existing if k[:2] == (body.course_id, body.kind))
            value['title'] = f"{KINDS_EN[body.kind]} {count + 1}"
        old = existing.get(key(value))
        if old:
            updated += 1
        else:
            added += 1
            old = {'id': uuid.uuid4().hex, 'created': time.time()}
        grade = core.put('grades', old | value | {'updated': time.time()})
        existing[key(grade)] = grade
        saved.append(grade)
    return {'added': added, 'updated': updated, 'grades': saved}


def round2(value):
    return round(value, 2)


def summary(course, grades):
    """One course: per kind totals, then the course total so far (out of what has been graded)."""
    split = course.get('grade_split') or {}
    has_split = any(split.get(k) for k in KINDS)
    kinds, earned, counted = {}, 0.0, 0.0
    for kind in KINDS:
        mine = [g for g in grades if g['kind'] == kind]
        score, out_of = sum(g['score'] for g in mine), sum(g['out_of'] for g in mine)
        row = {'count': len(mine), 'score': round2(score), 'out_of': round2(out_of),
               'percent': round(score / out_of * 100) if out_of else None, 'worth': split.get(kind, 0) if has_split else None}
        if has_split:
            row['marks'] = round2(score / out_of * split.get(kind, 0)) if out_of else None
            if out_of and split.get(kind):
                earned, counted = earned + row['marks'], counted + split[kind]
        elif out_of:
            earned, counted = earned + score, counted + out_of
        kinds[kind] = row
    return {'course_id': course['id'], 'title': course['title'], 'split': {k: split.get(k, 0) for k in KINDS} if has_split else None,
            'kinds': kinds, 'earned': round2(earned), 'counted': round2(counted),
            'total': round2(sum(split.get(k, 0) for k in KINDS)) if has_split else None,
            'percent': round(earned / counted * 100) if counted else None}


def overview(course_id=''):
    courses = [c for c in core.rows('courses') if c.get('kind') == 'college' and (not course_id or c['id'] == course_id)]
    grades = core.rows('grades')
    grades.sort(key=lambda g: (g.get('date') or '9999', g.get('created', 0)))
    courses.sort(key=lambda c: c['title'].lower())
    return {'courses': [summary(c, [g for g in grades if g['course_id'] == c['id']]) for c in courses],
            'grades': [g for g in grades if not course_id or g['course_id'] == course_id]}


@router.get('')
def list_grades(course_id: str = ''):
    return overview(course_id)


@router.post('')
def post_grades(body: GradeBatch):
    return add_grades(body)


def edit(grade_id, body: Grade):
    old = core.get('grades', grade_id)
    college_course(body.course_id)
    clash = next((g for g in core.rows('grades') if g['id'] != grade_id and key(g) == key(body.model_dump() | {'title': body.title.strip()})), None)
    if clash:
        raise HTTPException(400, 'فيه درجة بنفس الاسم في نفس المادة')
    return core.put('grades', old | body.model_dump() | {'title': body.title.strip(), 'updated': time.time()})


@router.put('/{grade_id}')
def put_grade(grade_id: str, body: Grade):
    return edit(grade_id, body)


@router.delete('/{grade_id}')
def delete_grade(grade_id: str):
    core.get('grades', grade_id)
    with core.database() as db:
        db.execute('DELETE FROM grades WHERE id=?', (grade_id,))
    return {'ok': True}


@router.put('/split/{course_id}')
def set_split(course_id: str, body: Split):
    """How many of the course marks each kind is worth. All zeros removes the split."""
    course = college_course(course_id)
    split = body.model_dump()
    course = core.put('courses', course | {'grade_split': split if any(split.values()) else {}})
    return summary(course, [g for g in core.rows('grades') if g['course_id'] == course_id])

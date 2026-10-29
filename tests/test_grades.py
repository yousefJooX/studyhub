"""College grades: sheets, quizzes, the midterm and the final; the marks split; the MCP tools."""
import json

from fastapi.testclient import TestClient

from studyhub.app import app

from .test_mcp_unit_cli import call, mcp


def test_grades_crud_totals_and_split(personal):
    college = personal.post('/api/courses', json={'title': 'Robotics', 'kind': 'college'}).json()
    own = personal.post('/api/courses', json={'title': 'Guitar'}).json()
    assert personal.post('/api/grades', json={'grades': [{'course_id': own['id'], 'score': 5, 'out_of': 10}]}).status_code == 400
    assert personal.post('/api/grades', json={'grades': [{'course_id': college['id'], 'score': 11, 'out_of': 10}]}).status_code == 422
    assert personal.post('/api/grades', json={'grades': [{'course_id': college['id'], 'kind': 'project', 'score': 1, 'out_of': 2}]}).status_code == 422
    made = personal.post('/api/grades', json={'grades': [
        {'course_id': college['id'], 'kind': 'sheet', 'score': 8, 'out_of': 10},
        {'course_id': college['id'], 'kind': 'sheet', 'score': 10, 'out_of': 10},
        {'course_id': college['id'], 'kind': 'quiz', 'title': 'Quiz 1', 'score': 4, 'out_of': 5},
        {'course_id': college['id'], 'kind': 'midterm', 'score': 15, 'out_of': 20, 'date': '2026-11-10'}]}).json()
    assert made['added'] == 4 and [g['title'] for g in made['grades']] == ['Sheet 1', 'Sheet 2', 'Quiz 1', '']
    # the same course + kind + title again updates instead of adding a copy
    again = personal.post('/api/grades', json={'grades': [{'course_id': college['id'], 'kind': 'quiz', 'title': 'quiz 1', 'score': 5, 'out_of': 5}]}).json()
    assert (again['added'], again['updated']) == (0, 1)
    data = personal.get('/api/grades').json()
    assert [c['title'] for c in data['courses']] == ['Robotics'] and len(data['grades']) == 4   # personal courses have no grades
    robotics = data['courses'][0]
    assert robotics['kinds']['sheet'] == {'count': 2, 'score': 18, 'out_of': 20, 'percent': 90, 'worth': None}
    assert (robotics['earned'], robotics['counted'], robotics['percent'], robotics['total']) == (38, 45, 84, None)
    # with a split each kind counts as its share of the course marks; the final is not graded yet
    split = personal.put(f"/api/grades/split/{college['id']}", json={'sheet': 10, 'quiz': 10, 'midterm': 20, 'final': 60}).json()
    assert split['kinds']['sheet']['marks'] == 9 and split['kinds']['midterm']['marks'] == 15 and split['kinds']['final']['marks'] is None
    assert (split['earned'], split['counted'], split['total'], split['percent']) == (34, 40, 100, 85)
    assert personal.put(f"/api/grades/split/{college['id']}", json={}).json()['split'] is None
    # edit, a clash with another grade's name, delete
    sheet = next(g for g in data['grades'] if g['title'] == 'Sheet 1')
    edited = personal.put(f"/api/grades/{sheet['id']}", json=sheet | {'score': 9}).json()
    assert edited['score'] == 9
    assert personal.put(f"/api/grades/{sheet['id']}", json=sheet | {'title': 'Sheet 2'}).status_code == 400
    assert personal.delete(f"/api/grades/{sheet['id']}").json() == {'ok': True}
    # deleting the course takes its grades with it
    assert personal.delete(f"/api/courses/{college['id']}").json()['grades_removed'] == 3
    assert personal.get('/api/grades').json() == {'courses': [], 'grades': []}


def test_mcp_grade_tools(users, env):
    alice = users('grader')
    token = alice.post('/api/account/token').json()['token']
    client = TestClient(app)
    call(client, token, 'create_course', title='Computer Vision')
    call(client, token, 'create_course', title='Piano', kind='personal')
    saved = call(client, token, 'add_grades', course='vision', grades=[
        {'kind': 'sheet', 'score': 9, 'out_of': 10}, {'kind': 'quiz', 'title': 'Quiz 1', 'score': 7, 'out_of': 10},
        {'kind': 'midterm', 'score': 17.5, 'out_of': 20}])
    assert saved['added'] == 3 and saved['grades'][0]['title'] == 'Sheet 1' and saved['grades'][2]['course'] == 'Computer Vision'
    assert call(client, token, 'add_grades', grades=[{'course': 'Computer Vision', 'kind': 'quiz', 'title': 'Quiz 1', 'score': 8, 'out_of': 10}])['updated_existing'] == 1
    split = call(client, token, 'set_grade_split', course='vision', sheet=10, quiz=10, midterm=20, final=60)['updated']
    assert split['course_total'] == 100 and split['total_so_far'] == '34.5 / 40'
    listed = call(client, token, 'list_grades')
    assert listed['courses'][0]['percent'] == 86 and set(listed['courses'][0]['by_kind']) == {'sheet', 'quiz', 'midterm', 'final'}
    quiz = next(g for g in listed['grades'] if g['kind'] == 'quiz')
    assert call(client, token, 'update_grade', grade_id=quiz['id'], score=10)['updated']['score'] == 10
    assert call(client, token, 'delete_grade', grade_id=quiz['id'])['deleted']['title'] == 'Quiz 1'
    assert len(call(client, token, 'list_grades', course='Computer Vision')['grades']) == 2
    # personal courses, unknown courses and impossible scores are tool errors, not crashes
    for arguments in ({'course': 'Piano', 'grades': [{'kind': 'sheet', 'score': 1, 'out_of': 2}]},
                      {'grades': [{'kind': 'sheet', 'score': 1, 'out_of': 2}]},
                      {'course': 'vision', 'grades': [{'kind': 'final', 'score': 70, 'out_of': 60}]}):
        result = mcp(client, 'tools/call', {'name': 'add_grades', 'arguments': arguments}, token)['result']
        assert result['isError'], json.dumps(arguments)

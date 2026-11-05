from app.models import AuditLog, Paper, Question, db
from tests.conftest import add_approved, login, post


def test_login_required(client):
    assert client.get("/admin/").status_code == 302
    assert client.get("/setter/").status_code == 302


def test_csrf_enforced(client):
    assert client.post("/login", data={"username": "admin", "password": "adminpass"}).status_code == 400


def test_setter_cannot_access_admin(client):
    login(client, "s1", "setterpass")
    assert client.get("/admin/").status_code == 403
    assert client.get("/admin/generate").status_code == 403


def test_setter_adds_question_and_admin_approves(app, client):
    login(client, "s1", "setterpass")
    r = post(client, "/setter/questions/new", {"text": "What is 2 + 2 equal to?", "subject": "Maths",
                                               "topic": "Arithmetic", "difficulty": "easy", "marks": "1",
                                               "est_minutes": "1", "answer": "4"})
    assert b"submitted for review" in r.data
    # Duplicate is caught
    r = post(client, "/setter/questions/new", {"text": "What is 2+2 equal to ?", "subject": "maths",
                                               "difficulty": "easy", "marks": "1", "est_minutes": "1"})
    assert b"very similar question" in r.data
    with app.app_context():
        qid = Question.query.one().id
    client.post("/logout", data={"csrf_token": "x"})
    client.get("/")  # reset
    with client.session_transaction() as s:
        s.clear()
    login(client, "admin", "adminpass")
    r = post(client, "/admin/review", {"ids": str(qid), "action": "approve"})
    assert b"1 question(s) approved" in r.data
    with app.app_context():
        assert db.session.get(Question, qid).status == "approved"


def test_setter_cannot_edit_others_or_approved(app, client):
    add_approved(app, 1)
    with app.app_context():
        q = Question.query.first()
        qid, owner = q.id, q.setter.username
    login(client, owner, "setterpass")
    r = client.get(f"/setter/questions/{qid}/edit", follow_redirects=True)
    assert b"locked" in r.data
    with client.session_transaction() as s:
        s.clear()
    other = "s2" if owner != "s2" else "s3"
    login(client, other, "setterpass")
    assert client.get(f"/setter/questions/{qid}/edit").status_code == 404


def test_generate_finalize_and_print(app, client):
    add_approved(app, 6)
    login(client, "admin", "adminpass")
    r = client.get("/admin/pool?subject=Maths&easy=2&medium=2&difficult=1")
    data = r.get_json()
    assert data["available"] == {"easy": 6, "medium": 6, "difficult": 6}
    assert 17 in data["achievable_totals"]
    r = post(client, "/admin/generate", {
        "subject": "Maths", "title": "Test", "total_marks": "17", "duration_minutes": "60",
        "easy": "2", "medium": "2", "difficult": "1", "exclude_recent": "1", "num_sets": "2",
        "balance_setters": "1", "cover_topics": "1", "order": "difficulty"})
    assert b"Fairness report" in r.data
    with app.app_context():
        p = Paper.query.one()
        assert p.total_marks == 17 and len(p.items) == 5 and p.status == "draft"
        pid, used_ids = p.id, [it.question_id for it in p.items]
        assert AuditLog.query.filter_by(action="paper_generate").count() == 1
    r = client.get(f"/admin/papers/{pid}/print?set=1")
    assert b"SET B" in r.data and b"DRAFT" in r.data
    post(client, f"/admin/papers/{pid}/finalize", {})
    with app.app_context():
        assert db.session.get(Paper, pid).status == "final"
        assert all(db.session.get(Question, i).times_used == 1 for i in used_ids)
    # Next paper excludes the questions of the last finalised paper.
    post(client, "/admin/generate", {"subject": "Maths", "easy": "2", "medium": "2", "difficult": "1",
                                     "exclude_recent": "1", "num_sets": "1", "duration_minutes": "60"})
    with app.app_context():
        p2 = Paper.query.filter(Paper.id != pid).one()
        assert not set(it.question_id for it in p2.items) & set(used_ids)


def test_generate_error_when_pool_too_small(app, client):
    add_approved(app, 2)
    login(client, "admin", "adminpass")
    r = post(client, "/admin/generate", {"subject": "Maths", "easy": "5", "duration_minutes": "60"})
    assert b"Not enough approved questions" in r.data


def test_discard_requires_reason(app, client):
    add_approved(app, 3)
    login(client, "admin", "adminpass")
    post(client, "/admin/generate", {"subject": "Maths", "easy": "1", "duration_minutes": "60"})
    with app.app_context():
        pid = Paper.query.one().id
    r = post(client, f"/admin/papers/{pid}/discard", {"reason": ""})
    assert b"Give a reason" in r.data
    post(client, f"/admin/papers/{pid}/discard", {"reason": "typo in Q1"})
    with app.app_context():
        assert db.session.get(Paper, pid).status == "discarded"


def test_csv_import(app, client):
    import io
    login(client, "s2", "setterpass")
    csv = ("text,subject,topic,difficulty,marks,est_minutes,answer\n"
           "Define velocity and give units,Physics,Kinematics,easy,2,3,m/s\n"
           "Bad row,Physics,,impossible,2,3,\n")
    from tests.conftest import csrf_of
    token = csrf_of(client, "/setter/import")
    r = client.post("/setter/import", data={"csrf_token": token, "file": (io.BytesIO(csv.encode()), "q.csv")},
                    content_type="multipart/form-data")
    assert b"<strong>1</strong> question(s) added" in r.data
    assert b"Line 3" in r.data

import json

from app.models import AuditLog, Paper, Question, db
from tests.conftest import add_approved, login, post

BLUEPRINT = {"subject": "Maths", "easy": "2", "medium": "2", "difficult": "1", "duration_minutes": "60",
             "exclude_recent": "0", "num_sets": "1", "balance_setters": "1", "cover_topics": "1"}


def snapshot_questions(app):
    cols = [c.name for c in Question.__table__.columns]
    with app.app_context():
        return {q.id: tuple(getattr(q, c) for c in cols) for q in Question.query.order_by(Question.id)}


def ids_by_level(app):
    with app.app_context():
        out = {}
        for q in Question.query.order_by(Question.id):
            out.setdefault(q.difficulty, []).append(q.id)
        return out


def test_include_and_exclude(app, client):
    add_approved(app, 6)
    lv = ids_by_level(app)
    inc, exc = lv["easy"][0], lv["medium"][0]
    login(client, "admin", "adminpass")
    for _ in range(8):
        post(client, "/admin/generate", {**BLUEPRINT, f"pick_{inc}": "include", f"pick_{exc}": "exclude",
                                         f"pick_{lv['easy'][1]}": ""})
    with app.app_context():
        papers = Paper.query.all()
        assert len(papers) == 8
        for p in papers:
            ids = [it.question_id for it in p.items]
            assert inc in ids and exc not in ids and len(ids) == 5
            assert p.config["include_ids"] == [inc] and p.config["exclude_ids"] == [exc]
            assert p.report["included"] == 1
        assert f"included=Q{inc}" in AuditLog.query.filter_by(action="paper_generate").first().detail
    page = client.get("/admin/papers/1").get_data(as_text=True)
    assert "included by admin" in page and "chosen by the admin" in page


def test_include_too_many_shows_error(app, client):
    add_approved(app, 6)
    lv = ids_by_level(app)
    login(client, "admin", "adminpass")
    data = {**BLUEPRINT, "difficult": "0", **{f"pick_{i}": "include" for i in lv["difficult"][:1]}}
    r = post(client, "/admin/generate", data)
    assert b"more questions than the blueprint allows" in r.data
    # the choice is kept on the form so the admin does not lose it
    assert f'"{lv["difficult"][0]}": "include"'.encode() in r.data


def test_pool_info_reports_included(app, client):
    add_approved(app, 6)
    lv = ids_by_level(app)
    login(client, "admin", "adminpass")
    d = client.get(f"/admin/pool?subject=Maths&easy=2&include={lv['easy'][0]}&exclude={lv['easy'][1]}").get_json()
    assert d["included"]["easy"] == 1 and d["excluded"] == 1 and d["available"]["easy"] == 4
    qs = client.get("/admin/pool/questions?subject=Maths").get_json()
    assert len(qs) == 18 and {"id", "text", "difficulty", "marks"} <= set(qs[0])


def test_swap_question_in_draft(app, client):
    add_approved(app, 6)
    login(client, "admin", "adminpass")
    post(client, "/admin/generate", {**BLUEPRINT, "total_marks": "17"})
    with app.app_context():
        p = Paper.query.one()
        item = p.items[0]
        iid, old_qid, old_level, old_marks = item.id, item.question_id, item.difficulty, item.marks
    r = post(client, f"/admin/papers/1/items/{iid}/replace", {"reason": "too similar to last year"})
    assert b"was replaced by a randomly drawn" in r.data and b"swapped in" in r.data
    with app.app_context():
        p = db.session.get(Paper, 1)
        it = next(i for i in p.items if i.id == iid)
        assert it.question_id != old_qid and it.difficulty == old_level and it.marks == old_marks
        assert old_qid not in [i.question_id for i in p.items]
        assert p.total_marks == 17 == sum(i.marks for i in p.items)
        assert p.config["replaced"][0]["old"] == old_qid and old_qid in p.config["exclude_ids"]
        assert "too similar" in AuditLog.query.filter_by(action="paper_replace_question").one().detail
    # Not allowed once final
    post(client, "/admin/papers/1/finalize", {})
    r = post(client, f"/admin/papers/1/items/{iid}/replace", {})
    assert b"only be swapped while the paper is a draft" in r.data


def test_rich_instructions_and_draft_edit(app, client):
    add_approved(app, 3)
    login(client, "admin", "adminpass")
    post(client, "/admin/generate", {**BLUEPRINT, "easy": "1", "medium": "0", "difficult": "0",
                                     "instructions": "<ol><li>Answer <b>all</b></li></ol><script>x()</script>"})
    with app.app_context():
        assert db.session.get(Paper, 1).instructions == "<ol><li>Answer <b>all</b></li></ol>"
    page = client.get("/admin/papers/1/print").get_data(as_text=True)
    assert "<ol><li>Answer <b>all</b></li></ol>" in page
    post(client, "/admin/papers/1/details", {"title": "New title", "duration_minutes": "90",
                                             "instructions": "<p>Use <i>blue</i> ink</p>"})
    with app.app_context():
        p = db.session.get(Paper, 1)
        assert (p.title, p.duration_minutes, p.instructions) == ("New title", 90, "<p>Use <i>blue</i> ink</p>")
        assert json.loads(p.config_json)["duration_minutes"] == 90


def test_existing_questions_are_never_altered(app, client):
    """Generating with include/exclude, swapping and editing paper details leave the question bank untouched."""
    add_approved(app, 6)
    with app.app_context():  # an old plain-text question with line breaks
        q = Question.query.first()
        q.set_text("Old plain question\nwith two lines and a < b")
        db.session.commit()
    before = snapshot_questions(app)
    lv = ids_by_level(app)
    login(client, "admin", "adminpass")
    post(client, "/admin/generate", {**BLUEPRINT, f"pick_{lv['easy'][0]}": "include",
                                     f"pick_{lv['medium'][0]}": "exclude"})
    with app.app_context():
        iid = Paper.query.one().items[-1].id
    post(client, f"/admin/papers/1/items/{iid}/replace", {})
    post(client, "/admin/papers/1/details", {"title": "T", "duration_minutes": "45", "instructions": "<p>x</p>"})
    post(client, "/admin/papers/1/discard", {"reason": "test"})
    client.get("/admin/papers/1"), client.get("/admin/papers/1/print?key=1"), client.get("/admin/generate")
    assert snapshot_questions(app) == before

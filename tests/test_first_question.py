import re

from app.models import AuditLog, Paper, db
from tests.conftest import add_approved, login, post
from tests.test_include_exclude import BLUEPRINT, ids_by_level, snapshot_questions


def printed_first(client, pid, set_index):
    html = client.get(f"/admin/papers/{pid}/print?set={set_index}").get_data(as_text=True)
    first_li = html.split('<ol class="questions">', 1)[1].split("</li>", 1)[0]
    return re.sub(r"\s+", " ", first_li)


def test_generate_with_first_question(app, client):
    add_approved(app, 6)
    lv = ids_by_level(app)
    first = lv["difficult"][2]
    login(client, "admin", "adminpass")
    # Marked as excluded too: choosing it as first wins and includes it.
    post(client, "/admin/generate", {**BLUEPRINT, "num_sets": "4", "first_question": str(first),
                                     f"pick_{first}": "exclude"})
    with app.app_context():
        p = Paper.query.one()
        assert p.items[0].question_id == first and p.config["first_id"] == first
        assert first in p.config["include_ids"] and first not in p.config["exclude_ids"]
        text = p.items[0].text
        assert f"first=Q{first}" in AuditLog.query.filter_by(action="paper_generate").one().detail
    for s in range(4):
        assert text in printed_first(client, 1, s)
    assert "1st in every set" in client.get("/admin/papers/1").get_data(as_text=True)


def test_first_question_conflicting_with_blueprint(app, client):
    add_approved(app, 6)
    lv = ids_by_level(app)
    login(client, "admin", "adminpass")
    r = post(client, "/admin/generate", {**BLUEPRINT, "difficult": "0", "first_question": str(lv["difficult"][0])})
    assert b"more questions than the blueprint allows" in r.data
    assert f'name="first_question" id="first_question" value="{lv["difficult"][0]}"'.encode() in r.data


def test_set_and_clear_first_on_draft_and_swap_keeps_slot(app, client):
    add_approved(app, 6)
    before = snapshot_questions(app)
    login(client, "admin", "adminpass")
    post(client, "/admin/generate", {**BLUEPRINT, "num_sets": "3"})
    with app.app_context():
        last = Paper.query.one().items[-1]
        iid, qid, text = last.id, last.question_id, last.text
    post(client, "/admin/papers/1/first", {"item": str(iid)})
    with app.app_context():
        p = db.session.get(Paper, 1)
        assert p.items[0].id == iid and p.config["first_id"] == qid
        assert [it.position for it in p.items] == [1, 2, 3, 4, 5]
    for s in range(3):
        assert text in printed_first(client, 1, s)
    # Swapping the first question: the new one takes over the first slot.
    post(client, f"/admin/papers/1/items/{iid}/replace", {})
    with app.app_context():
        p = db.session.get(Paper, 1)
        assert p.items[0].id == iid and p.items[0].question_id != qid
        assert p.config["first_id"] == p.items[0].question_id
    post(client, "/admin/papers/1/first", {"item": "none"})
    with app.app_context():
        assert db.session.get(Paper, 1).config["first_id"] is None
    # Not allowed once final
    post(client, "/admin/papers/1/finalize", {})
    r = post(client, "/admin/papers/1/first", {"item": str(iid)})
    assert b"only be changed while the paper is a draft" in r.data
    # The question bank was not altered (apart from finalising's usage counter).
    after = snapshot_questions(app)
    from app.models import Question
    cols = [c.name for c in Question.__table__.columns]
    skip = {cols.index("times_used"), cols.index("last_used_at"), cols.index("updated_at")}
    strip = lambda snap: {k: tuple(v for i, v in enumerate(row) if i not in skip) for k, row in snap.items()}
    assert strip(after) == strip(before)

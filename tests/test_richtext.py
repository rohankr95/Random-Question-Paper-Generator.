from app.richtext import clean_input, is_html, render, to_plain
from tests.conftest import add_approved, login, post

TABLE = ("<p>Study the <b>table</b>:</p><table style='width:300px' class='x'><tbody>"
         "<tr><th>Item</th><th>Price</th></tr><tr><td colspan='1'>Pen</td><td>10</td></tr></tbody></table>")


def test_plain_text_is_kept_and_rendered_with_line_breaks():
    assert not is_html("If x < y and y > 3, then what is x?")
    assert clean_input("  a < b\nline 2 ") == "a < b\nline 2"
    assert str(render("a < b\nline 2")) == "a &lt; b<br>line 2"


def test_formatting_and_tables_survive():
    out = clean_input(TABLE)
    assert "<b>table</b>" in out and "<table" in out and "<td colspan=\"1\">Pen</td>" in out
    assert "width" not in out and "class" not in out


def test_dangerous_html_removed():
    out = clean_input('<p onclick="x()">Hi <b>there</b></p><script>alert(1)</script>'
                      '<img src=x onerror=alert(1)><a href="javascript:alert(1)">link</a>'
                      '<p style="color:red;text-align:center;background:url(x)">c</p>')
    assert "script" not in out and "onclick" not in out and "img" not in out and "javascript" not in out
    assert "<a" not in out and "link" in out
    assert 'style="text-align:center"' in out.replace("; ", ";").replace(": ", ":")


def test_empty_editor_counts_as_empty():
    assert clean_input("<p><br></p>") == ""


def test_plain_version():
    assert to_plain(TABLE) == "Study the table:\nItem Price\nPen 10"


def test_setter_can_save_table_question(app, client):
    login(client, "s1", "setterpass")
    r = post(client, "/setter/questions/new", {"text": TABLE + "<script>alert(1)</script>", "subject": "Maths",
                                               "difficulty": "easy", "marks": "2", "est_minutes": "3",
                                               "answer": "<ul><li>Pen costs <b>10</b></li></ul>"})
    assert b"submitted for review" in r.data
    page = client.get("/setter/questions").get_data(as_text=True)
    assert "<table>" in page and "<b>table</b>" in page and "alert(1)" not in page
    # Too-short formatted text is rejected
    r = post(client, "/setter/questions/new", {"text": "<p><b>Hi</b></p>", "subject": "Maths",
                                               "difficulty": "easy", "marks": "2", "est_minutes": "3"})
    assert b"too short" in r.data


def test_printed_paper_shows_table(app, client):
    from app.models import Question, db
    add_approved(app, 2)
    with app.app_context():
        for q in Question.query.filter_by(difficulty="easy"):
            q.set_text(TABLE)
            q.answer = "<p>Answer with <i>italics</i></p>"
        db.session.commit()
    login(client, "admin", "adminpass")
    post(client, "/admin/generate", {"subject": "Maths", "easy": "1", "duration_minutes": "60"})
    page = client.get("/admin/papers/1/print?key=1").get_data(as_text=True)
    assert "<th>Item</th>" in page and "<i>italics</i>" in page


def test_edit_form_converts_old_plain_text(app, client):
    from app.models import Question, User, db
    with app.app_context():
        s1 = User.query.filter_by(username="s1").one()
        q = Question(subject="Maths", difficulty="easy", marks=1, est_minutes=1, setter_id=s1.id)
        q.set_text("Line one with a < b\nLine two")
        db.session.add(q)
        db.session.commit()
        qid = q.id
    login(client, "s1", "setterpass")
    page = client.get(f"/setter/questions/{qid}/edit").get_data(as_text=True)
    # The editor receives HTML (escaped inside the textarea) with the line break preserved.
    assert "Line one with a &amp;lt; b&lt;br&gt;Line two" in page

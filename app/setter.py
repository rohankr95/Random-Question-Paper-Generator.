from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .models import DIFFICULTIES, Question, db, log
from .questions import apply_values, find_similar, parse_csv, parse_question_form, subjects, topics
from .utils import role_required

bp = Blueprint("setter", __name__, url_prefix="/setter")

EDITABLE = ("pending", "rejected")


def _own_question(qid):
    q = db.session.get(Question, qid)
    if q is None or q.setter_id != g.user.id:
        abort(404)
    return q


@bp.route("/")
@role_required("setter")
def dashboard():
    qs = Question.query.filter_by(setter_id=g.user.id)
    stats = {d: {s: 0 for s in ("pending", "approved", "rejected")} for d in DIFFICULTIES}
    for q in qs:
        stats[q.difficulty][q.status] += 1
    rejected = qs.filter_by(status="rejected").order_by(Question.updated_at.desc()).limit(5).all()
    return render_template("setter/dashboard.html", stats=stats, total=qs.count(), rejected=rejected)


@bp.route("/questions")
@role_required("setter")
def questions():
    query = Question.query.filter_by(setter_id=g.user.id)
    f = {k: request.args.get(k, "") for k in ("subject", "difficulty", "status", "q")}
    if f["subject"]:
        query = query.filter(Question.subject == f["subject"])
    if f["difficulty"]:
        query = query.filter(Question.difficulty == f["difficulty"])
    if f["status"]:
        query = query.filter(Question.status == f["status"])
    if f["q"]:
        query = query.filter(Question.text.ilike(f"%{f['q']}%"))
    items = query.order_by(Question.created_at.desc()).all()
    my_subjects = sorted({q.subject for q in Question.query.filter_by(setter_id=g.user.id)})
    return render_template("setter/questions.html", items=items, f=f, subjects=my_subjects)


def _save(question, is_new):
    form = request.form
    try:
        values = parse_question_form(form)
    except ValueError as exc:
        flash(str(exc), "danger")
        return None
    similar = find_similar(values["text"], values["subject"], exclude_id=None if is_new else question.id)
    if similar and not form.get("confirm_duplicate"):
        flash(
            f"A very similar question ({similar[1]:.0%} match) already exists in the {values['subject']} pool. "
            "Please rephrase it, or tick 'This is not a duplicate' to save anyway.",
            "warning",
        )
        return "duplicate"
    apply_values(question, values)
    question.status = "pending"
    question.review_note = ""
    if is_new:
        question.setter_id = g.user.id
        db.session.add(question)
    db.session.flush()
    log(g.user, "question_add" if is_new else "question_edit", f"Q{question.id}")
    db.session.commit()
    return question


@bp.route("/questions/new", methods=["GET", "POST"])
@role_required("setter")
def new_question():
    q = Question()
    dup = False
    if request.method == "POST":
        result = _save(q, is_new=True)
        if isinstance(result, Question):
            flash("Question submitted for review.", "success")
            if request.form.get("add_another"):
                return redirect(url_for("setter.new_question", subject=result.subject, topic=result.topic))
            return redirect(url_for("setter.questions"))
        dup = result == "duplicate"
    return render_template("setter/question_form.html", q=q, form=request.form or request.args,
                           is_new=True, dup=dup, subjects=subjects(), topics=topics())


@bp.route("/questions/<int:qid>/edit", methods=["GET", "POST"])
@role_required("setter")
def edit_question(qid):
    q = _own_question(qid)
    if q.status not in EDITABLE:
        flash("Approved questions are locked. Ask the admin if a change is needed.", "warning")
        return redirect(url_for("setter.questions"))
    dup = False
    if request.method == "POST":
        result = _save(q, is_new=False)
        if isinstance(result, Question):
            flash("Question updated and re-submitted for review.", "success")
            return redirect(url_for("setter.questions"))
        dup = result == "duplicate"
    form = request.form if request.method == "POST" else {
        "text": q.text, "subject": q.subject, "topic": q.topic, "difficulty": q.difficulty,
        "marks": q.marks, "est_minutes": q.est_minutes, "answer": q.answer,
    }
    return render_template("setter/question_form.html", q=q, form=form, is_new=False, dup=dup,
                           subjects=subjects(), topics=topics())


@bp.route("/questions/<int:qid>/delete", methods=["POST"])
@role_required("setter")
def delete_question(qid):
    q = _own_question(qid)
    if q.status not in EDITABLE:
        flash("Approved questions cannot be deleted by setters.", "warning")
    else:
        log(g.user, "question_delete", f"Q{q.id}")
        db.session.delete(q)
        db.session.commit()
        flash("Question deleted.", "info")
    return redirect(url_for("setter.questions"))


@bp.route("/import", methods=["GET", "POST"])
@role_required("setter")
def import_csv():
    report = None
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose a CSV file.", "danger")
        else:
            report = {"added": 0, "errors": [], "duplicates": []}
            try:
                for line_no, values, error in parse_csv(file):
                    if error:
                        report["errors"].append(f"Line {line_no}: {error}")
                        continue
                    similar = find_similar(values["text"], values["subject"])
                    if similar and not request.form.get("allow_duplicates"):
                        report["duplicates"].append(f"Line {line_no}: {similar[1]:.0%} similar to an existing question - skipped")
                        continue
                    q = Question(setter_id=g.user.id, status="pending")
                    apply_values(q, values)
                    db.session.add(q)
                    db.session.flush()  # so later rows are checked against this one too
                    report["added"] += 1
                log(g.user, "question_import", f"{report['added']} questions")
                db.session.commit()
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                report = None
    return render_template("setter/import.html", report=report)

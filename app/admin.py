import json
import secrets
from collections import defaultdict

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import generator
from .models import (DIFFICULTIES, AuditLog, Paper, PaperQuestion, Question, User, db, log, utcnow)
from .questions import apply_values, find_similar, parse_question_form, subjects, topics
from .utils import parse_int, role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
@role_required("admin")
def dashboard():
    pool = defaultdict(lambda: {d: 0 for d in DIFFICULTIES})
    for subject, diff, n in (db.session.query(Question.subject, Question.difficulty, db.func.count())
                             .filter(Question.status == "approved")
                             .group_by(Question.subject, Question.difficulty)):
        pool[subject][diff] = n
    pending = Question.query.filter_by(status="pending").count()
    setters = []
    for u in User.query.filter_by(role="setter").order_by(User.username):
        counts = defaultdict(int)
        for status, n in (db.session.query(Question.status, db.func.count())
                          .filter(Question.setter_id == u.id).group_by(Question.status)):
            counts[status] = n
        setters.append((u, counts))
    recent = Paper.query.order_by(Paper.created_at.desc()).limit(5).all()
    return render_template("admin/dashboard.html", pool=dict(sorted(pool.items())), pending=pending,
                           setters=setters, recent=recent)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@bp.route("/users", methods=["GET", "POST"])
@role_required("admin")
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "setter")
        if role not in ("setter", "admin"):
            abort(400)
        if not username or not username.replace("_", "").replace(".", "").isalnum():
            flash("Username may only contain letters, digits, '.' and '_'.", "danger")
        elif User.query.filter(db.func.lower(User.username) == username).first():
            flash("That username is taken.", "danger")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
        else:
            u = User(username=username, full_name=full_name, role=role)
            u.set_password(password)
            db.session.add(u)
            log(g.user, "user_create", f"{username} ({role})")
            db.session.commit()
            flash(f"User '{username}' created. Share the password with them privately.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/users.html", users=User.query.order_by(User.role, User.username).all())


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@role_required("admin")
def toggle_user(uid):
    u = db.session.get(User, uid) or abort(404)
    if u.id == g.user.id:
        flash("You cannot deactivate yourself.", "warning")
    else:
        u.is_active = not u.is_active
        log(g.user, "user_toggle", f"{u.username} active={u.is_active}")
        db.session.commit()
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:uid>/password", methods=["POST"])
@role_required("admin")
def reset_password(uid):
    u = db.session.get(User, uid) or abort(404)
    new = secrets.token_urlsafe(9)
    u.set_password(new)
    log(g.user, "user_password_reset", u.username)
    db.session.commit()
    flash(f"New password for '{u.username}': {new}  (shown once - share it privately)", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Question review & bank
# ---------------------------------------------------------------------------

@bp.route("/questions")
@role_required("admin")
def questions():
    f = {k: request.args.get(k, "") for k in ("subject", "difficulty", "status", "setter", "q", "topic")}
    query = Question.query
    if f["subject"]:
        query = query.filter(Question.subject == f["subject"])
    if f["difficulty"]:
        query = query.filter(Question.difficulty == f["difficulty"])
    if f["status"]:
        query = query.filter(Question.status == f["status"])
    if f["setter"].isdigit():
        query = query.filter(Question.setter_id == int(f["setter"]))
    if f["topic"]:
        query = query.filter(Question.topic == f["topic"])
    if f["q"]:
        query = query.filter(Question.text.ilike(f"%{f['q']}%"))
    items = query.order_by(Question.status.desc(), Question.created_at.desc()).all()
    setters = User.query.filter_by(role="setter").order_by(User.username).all()
    return render_template("admin/questions.html", items=items, f=f, subjects=subjects(),
                           topics=topics(), setters=setters)


@bp.route("/review", methods=["GET", "POST"])
@role_required("admin")
def review():
    if request.method == "POST":
        action = request.form.get("action")
        ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
        note = request.form.get("note", "").strip()[:500]
        if action not in ("approve", "reject") or not ids:
            flash("Select at least one question and an action.", "warning")
        elif action == "reject" and not note:
            flash("Please give the setter a reason when rejecting.", "warning")
        else:
            qs = Question.query.filter(Question.id.in_(ids), Question.status == "pending").all()
            for q in qs:
                q.status = "approved" if action == "approve" else "rejected"
                q.review_note = note
            log(g.user, f"question_{action}", ", ".join(f"Q{q.id}" for q in qs) + (f" - {note}" if note else ""))
            db.session.commit()
            flash(f"{len(qs)} question(s) {action}d.", "success")
        return redirect(url_for("admin.review"))
    items = Question.query.filter_by(status="pending").order_by(Question.created_at).all()
    # Pre-compute duplicate hints for the reviewer.
    hints = {}
    for q in items:
        sim = find_similar(q.text, q.subject, exclude_id=q.id)
        if sim:
            hints[q.id] = sim
    return render_template("admin/review.html", items=items, hints=hints)


@bp.route("/questions/<int:qid>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_question(qid):
    q = db.session.get(Question, qid) or abort(404)
    if request.method == "POST":
        try:
            values = parse_question_form(request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
        else:
            apply_values(q, values)
            status = request.form.get("status")
            if status in ("pending", "approved", "rejected"):
                q.status = status
            q.review_note = request.form.get("review_note", "").strip()[:500]
            log(g.user, "question_admin_edit", f"Q{q.id}")
            db.session.commit()
            flash("Question saved. Papers already generated keep their original wording.", "success")
            return redirect(request.args.get("back") or url_for("admin.questions"))
    form = request.form if request.method == "POST" else {
        "text": q.text, "subject": q.subject, "topic": q.topic, "difficulty": q.difficulty,
        "marks": q.marks, "est_minutes": q.est_minutes, "answer": q.answer,
        "status": q.status, "review_note": q.review_note,
    }
    return render_template("admin/question_edit.html", q=q, form=form, subjects=subjects(), topics=topics())


@bp.route("/questions/<int:qid>/delete", methods=["POST"])
@role_required("admin")
def delete_question(qid):
    q = db.session.get(Question, qid) or abort(404)
    if PaperQuestion.query.filter_by(question_id=q.id).first():
        q.status = "rejected"
        q.review_note = "Retired by admin"
        flash("This question appears in a generated paper, so it was retired (rejected) instead of deleted.", "info")
        log(g.user, "question_retire", f"Q{q.id}")
    else:
        db.session.delete(q)
        log(g.user, "question_delete", f"Q{q.id}")
        flash("Question deleted.", "info")
    db.session.commit()
    return redirect(url_for("admin.questions"))


# ---------------------------------------------------------------------------
# Paper generation
# ---------------------------------------------------------------------------

def _eligible_pool(subject, exclude_recent, fresh_only, max_uses=None):
    query = Question.query.filter(Question.status == "approved",
                                  db.func.lower(Question.subject) == subject.strip().lower())
    if fresh_only:
        query = query.filter(Question.times_used == 0)
    if max_uses is not None:
        query = query.filter(Question.times_used < max_uses)
    if exclude_recent:
        recent_ids = [p.id for p in Paper.query.filter(Paper.status == "final",
                                                       db.func.lower(Paper.subject) == subject.strip().lower())
                      .order_by(Paper.finalized_at.desc()).limit(exclude_recent)]
        if recent_ids:
            used = db.session.query(PaperQuestion.question_id).filter(PaperQuestion.paper_id.in_(recent_ids))
            query = query.filter(~Question.id.in_(used))
    return query.all()


def _to_candidate(q):
    return generator.Candidate(id=q.id, difficulty=q.difficulty, marks=q.marks, est_minutes=q.est_minutes,
                               setter_id=q.setter_id, topic=q.topic, times_used=q.times_used)


def _read_generate_form(form):
    cfg = {
        "subject": (form.get("subject") or "").strip(),
        "title": (form.get("title") or "").strip()[:200],
        "total_marks": parse_int(form.get("total_marks"), "Total marks", 0, 10000, default=0),
        "duration_minutes": parse_int(form.get("duration_minutes"), "Duration", 1, 1440, default=180),
        "easy": parse_int(form.get("easy"), "Easy questions", 0, 500, default=0),
        "medium": parse_int(form.get("medium"), "Medium questions", 0, 500, default=0),
        "difficult": parse_int(form.get("difficult"), "Difficult questions", 0, 500, default=0),
        "exclude_recent": parse_int(form.get("exclude_recent"), "Exclude recent papers", 0, 100, default=0),
        "num_sets": parse_int(form.get("num_sets"), "Number of sets", 1, 6, default=1),
        "fresh_only": bool(form.get("fresh_only")),
        "balance_setters": bool(form.get("balance_setters")),
        "cover_topics": bool(form.get("cover_topics")),
        "order": "random" if form.get("order") == "random" else "difficulty",
        "instructions": (form.get("instructions") or "").strip(),
    }
    if not cfg["subject"]:
        raise ValueError("Choose a subject.")
    return cfg


@bp.route("/pool")
@role_required("admin")
def pool_info():
    """JSON used by the generate page to show what the pool can support before generating."""
    subject = request.args.get("subject", "")
    try:
        exclude_recent = parse_int(request.args.get("exclude_recent"), "x", 0, 100, default=0)
        counts = {d: parse_int(request.args.get(d), d, 0, 500, default=0) for d in DIFFICULTIES}
    except ValueError:
        return jsonify({"error": "bad input"}), 400
    pool = _eligible_pool(subject, exclude_recent, bool(request.args.get("fresh_only")))
    by_diff = {d: defaultdict(int) for d in DIFFICULTIES}
    for q in pool:
        by_diff[q.difficulty][q.marks] += 1
    result = {
        "available": {d: sum(by_diff[d].values()) for d in DIFFICULTIES},
        "marks_breakdown": {d: {str(k): v for k, v in sorted(by_diff[d].items())} for d in DIFFICULTIES},
        "setters": len({q.setter_id for q in pool}),
        "topics": len({q.topic.lower() for q in pool if q.topic}),
    }
    if sum(counts.values()):
        cands = [_to_candidate(q) for q in pool]
        totals = generator.achievable_totals(cands, counts)
        result["achievable_totals"] = totals[:400]
    return jsonify(result)


@bp.route("/generate", methods=["GET", "POST"])
@role_required("admin")
def generate():
    form = request.form if request.method == "POST" else {
        "duration_minutes": 180, "num_sets": 1, "exclude_recent": 1, "balance_setters": "1",
        "cover_topics": "1", "order": "difficulty", "subject": request.args.get("subject", ""),
        "instructions": "Answer all questions.\nMarks for each question are shown on the right.",
    }
    if request.method == "POST":
        try:
            cfg = _read_generate_form(request.form)
            pool = _eligible_pool(cfg["subject"], cfg["exclude_recent"], cfg["fresh_only"])
            counts = {d: cfg[d] for d in DIFFICULTIES}
            selection = generator.select_questions(
                [_to_candidate(q) for q in pool], counts, cfg["total_marks"] or None,
                balance_setters=cfg["balance_setters"], cover_topics=cfg["cover_topics"], order=cfg["order"],
            )
        except (ValueError, generator.GenerationError) as exc:
            flash(str(exc), "danger")
            return render_template("admin/generate.html", form=form, subjects=subjects())

        by_id = {q.id: q for q in pool}
        chosen = [by_id[c.id] for c in selection.questions]
        setter_names = {u.id: u.display_name for u in User.query.filter_by(role="setter")}
        report = generator.fairness_report(selection.questions, cfg["duration_minutes"], setter_names)
        report["pool_size"] = len(pool)
        total = sum(q.marks for q in chosen)
        paper = Paper(
            code=_paper_code(), title=cfg["title"] or f"{cfg['subject']} Examination",
            subject=cfg["subject"], total_marks=total, duration_minutes=cfg["duration_minutes"],
            instructions=cfg["instructions"], num_sets=cfg["num_sets"], seed=selection.seed,
            config_json=json.dumps(cfg), report_json=json.dumps(report), created_by_id=g.user.id,
        )
        for pos, q in enumerate(chosen, start=1):
            paper.items.append(PaperQuestion(
                question_id=q.id, position=pos, text=q.text, answer=q.answer, difficulty=q.difficulty,
                marks=q.marks, est_minutes=q.est_minutes, topic=q.topic,
            ))
        db.session.add(paper)
        db.session.flush()
        attempts = Paper.query.filter(Paper.subject == paper.subject, Paper.status != "final").count()
        log(g.user, "paper_generate",
            f"{paper.code} '{paper.title}' seed={paper.seed} questions={len(chosen)} marks={total} "
            f"(unfinalised drafts/discards for this subject: {attempts})")
        db.session.commit()
        flash("Paper generated. Review the fairness report, then finalise or discard it.", "success")
        return redirect(url_for("admin.paper", pid=paper.id))
    return render_template("admin/generate.html", form=form, subjects=subjects())


def _paper_code():
    while True:
        code = "QP-" + secrets.token_hex(3).upper()
        if not Paper.query.filter_by(code=code).first():
            return code


@bp.route("/papers")
@role_required("admin")
def papers():
    status = request.args.get("status", "")
    query = Paper.query
    if status:
        query = query.filter_by(status=status)
    return render_template("admin/papers.html", papers=query.order_by(Paper.created_at.desc()).all(), status=status)


@bp.route("/papers/<int:pid>")
@role_required("admin")
def paper(pid):
    p = db.session.get(Paper, pid) or abort(404)
    return render_template("admin/paper.html", p=p, report=p.report, cfg=p.config)


@bp.route("/papers/<int:pid>/finalize", methods=["POST"])
@role_required("admin")
def finalize_paper(pid):
    p = db.session.get(Paper, pid) or abort(404)
    if p.status != "draft":
        flash("Only draft papers can be finalised.", "warning")
        return redirect(url_for("admin.paper", pid=p.id))
    now = utcnow()
    for item in p.items:
        if item.question:
            item.question.times_used += 1
            item.question.last_used_at = now
    p.status = "final"
    p.finalized_at = now
    log(g.user, "paper_finalize", f"{p.code}")
    db.session.commit()
    flash("Paper finalised. Its questions now count as 'used' for future papers.", "success")
    return redirect(url_for("admin.paper", pid=p.id))


@bp.route("/papers/<int:pid>/discard", methods=["POST"])
@role_required("admin")
def discard_paper(pid):
    p = db.session.get(Paper, pid) or abort(404)
    reason = request.form.get("reason", "").strip()[:500]
    if p.status != "draft":
        flash("Only draft papers can be discarded.", "warning")
    elif not reason:
        flash("Give a reason for discarding - it is kept in the audit log.", "warning")
    else:
        p.status = "discarded"
        p.status_note = reason
        log(g.user, "paper_discard", f"{p.code}: {reason}")
        db.session.commit()
        flash("Paper discarded. It stays in the history for transparency.", "info")
    return redirect(url_for("admin.paper", pid=p.id))


@bp.route("/papers/<int:pid>/print")
@role_required("admin")
def print_paper(pid):
    p = db.session.get(Paper, pid) or abort(404)
    raw = request.args.get("set", "0")
    set_index = min(int(raw), p.num_sets - 1) if raw.isdigit() else 0
    items = generator.shuffled_set(p.items, p.seed, set_index, lambda it: it.difficulty,
                                   p.config.get("order", "difficulty"))
    show_key = request.args.get("key") == "1"
    log(g.user, "paper_print", f"{p.code} set={chr(65 + set_index)} key={show_key}")
    db.session.commit()
    return render_template("admin/print.html", p=p, items=items, set_label=chr(65 + set_index),
                           show_key=show_key, show_meta=request.args.get("meta") == "1")


@bp.route("/audit")
@role_required("admin")
def audit():
    entries = AuditLog.query.order_by(AuditLog.at.desc()).limit(500).all()
    return render_template("admin/audit.html", entries=entries)

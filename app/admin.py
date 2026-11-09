import json
import secrets
from collections import defaultdict

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import generator
from .models import (DIFFICULTIES, AuditLog, Paper, PaperQuestion, Question, User, db, log, utcnow)
from .questions import apply_values, editor_form_values, find_similar, parse_question_form, subjects, topics
from .richtext import clean_input, render, to_plain
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
        **editor_form_values(q), "status": q.status, "review_note": q.review_note,
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
        "instructions": clean_input(form.get("instructions")),
        "include_ids": sorted(_picked_ids(form, "include")),
        "exclude_ids": sorted(_picked_ids(form, "exclude")),
        "first_id": None,
    }
    if not cfg["subject"]:
        raise ValueError("Choose a subject.")
    first = (form.get("first_question") or "").strip()
    if first.isdigit():
        # The first question is always part of the paper.
        cfg["first_id"] = int(first)
        cfg["include_ids"] = sorted(set(cfg["include_ids"]) | {cfg["first_id"]})
        cfg["exclude_ids"] = [i for i in cfg["exclude_ids"] if i != cfg["first_id"]]
    return cfg


def _picked_ids(form, choice):
    """Question ids the admin marked as "include" / "exclude" (form fields pick_<id>=<choice>)."""
    ids = set()
    for key, value in form.items():
        if key.startswith("pick_") and key[5:].isdigit() and value == choice:
            ids.add(int(key[5:]))
    return ids


def _id_list(raw):
    return {int(x) for x in (raw or "").split(",") if x.strip().isdigit()}


def _forced_questions(subject, include_ids):
    """The approved questions of this subject the admin chose to always include."""
    if not include_ids:
        return []
    found = Question.query.filter(Question.id.in_(include_ids), Question.status == "approved",
                                  db.func.lower(Question.subject) == subject.strip().lower()).all()
    missing = set(include_ids) - {q.id for q in found}
    if missing:
        raise ValueError("These questions can no longer be included (not approved or not in this subject): "
                         + ", ".join(f"#{i}" for i in sorted(missing)))
    return found


def _pool_for(cfg):
    """Eligible random-draw pool for a blueprint (fairness filters + the admin's exclusions)."""
    excluded = set(cfg.get("exclude_ids") or []) | set(cfg.get("include_ids") or [])
    return [q for q in _eligible_pool(cfg["subject"], cfg.get("exclude_recent", 0), cfg.get("fresh_only"))
            if q.id not in excluded]


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
    include_ids, exclude_ids = _id_list(request.args.get("include")), _id_list(request.args.get("exclude"))
    eligible = _eligible_pool(subject, exclude_recent, bool(request.args.get("fresh_only")))
    eligible_ids = [q.id for q in eligible]
    pool = [q for q in eligible if q.id not in exclude_ids and q.id not in include_ids]
    try:
        forced = _forced_questions(subject, include_ids)
    except ValueError:
        forced = []
    by_diff = {d: defaultdict(int) for d in DIFFICULTIES}
    for q in pool:
        by_diff[q.difficulty][q.marks] += 1
    result = {
        "available": {d: sum(by_diff[d].values()) for d in DIFFICULTIES},
        "marks_breakdown": {d: {str(k): v for k, v in sorted(by_diff[d].items())} for d in DIFFICULTIES},
        "setters": len({q.setter_id for q in pool}),
        "topics": len({q.topic.lower() for q in pool if q.topic}),
        "eligible_ids": eligible_ids,
        "included": {d: sum(1 for q in forced if q.difficulty == d) for d in DIFFICULTIES},
        "included_marks": sum(q.marks for q in forced),
        "excluded": len(exclude_ids),
    }
    if sum(counts.values()):
        totals, error = generator.blueprint_totals([_to_candidate(q) for q in pool], counts,
                                                   [_to_candidate(q) for q in forced])
        result["achievable_totals"] = totals[:400]
        if error:
            result["blueprint_error"] = error
    return jsonify(result)


@bp.route("/pool/questions")
@role_required("admin")
def pool_questions():
    """All approved questions of a subject, for the include/exclude picker on the generate page."""
    subject = request.args.get("subject", "")
    qs = (Question.query.filter(Question.status == "approved",
                                db.func.lower(Question.subject) == subject.strip().lower())
          .order_by(Question.difficulty, Question.topic, Question.id).all())
    rank = {d: i for i, d in enumerate(DIFFICULTIES)}
    qs.sort(key=lambda q: (rank.get(q.difficulty, 9), q.topic.lower(), q.id))
    return jsonify([{
        "id": q.id, "text": to_plain(q.text)[:220], "has_table": "<table" in q.text.lower(),
        "difficulty": q.difficulty, "marks": q.marks, "minutes": q.est_minutes, "topic": q.topic,
        "used": q.times_used,
    } for q in qs])


@bp.route("/generate", methods=["GET", "POST"])
@role_required("admin")
def generate():
    form = request.form if request.method == "POST" else {
        "duration_minutes": 180, "num_sets": 1, "exclude_recent": 1, "balance_setters": "1",
        "cover_topics": "1", "order": "difficulty", "subject": request.args.get("subject", ""),
        "instructions": "<ol><li>Answer <strong>all</strong> questions.</li>"
                        "<li>Marks for each question are shown on the right.</li></ol>",
    }
    if request.method == "POST":
        try:
            cfg = _read_generate_form(request.form)
            forced = _forced_questions(cfg["subject"], cfg["include_ids"])
            pool = _pool_for(cfg)
            counts = {d: cfg[d] for d in DIFFICULTIES}
            selection = generator.select_questions(
                [_to_candidate(q) for q in pool], counts, cfg["total_marks"] or None,
                forced=[_to_candidate(q) for q in forced], first_id=cfg["first_id"],
                balance_setters=cfg["balance_setters"], cover_topics=cfg["cover_topics"], order=cfg["order"],
            )
        except (ValueError, generator.GenerationError) as exc:
            flash(str(exc), "danger")
            return _render_generate(form)

        by_id = {q.id: q for q in pool + forced}
        chosen = [by_id[c.id] for c in selection.questions]
        report = _build_report(chosen, cfg, pool_size=len(pool) + len(forced))
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
        manual = ""
        if cfg["include_ids"] or cfg["exclude_ids"]:
            manual = (" included=" + ",".join(f"Q{i}" for i in cfg["include_ids"])
                      + " excluded=" + ",".join(f"Q{i}" for i in cfg["exclude_ids"]))
        if cfg["first_id"]:
            manual += f" first=Q{cfg['first_id']}"
        log(g.user, "paper_generate",
            f"{paper.code} '{paper.title}' seed={paper.seed} questions={len(chosen)} marks={total}{manual} "
            f"(unfinalised drafts/discards for this subject: {attempts})")
        db.session.commit()
        flash("Paper generated. Review the fairness report, then finalise or discard it.", "success")
        return redirect(url_for("admin.paper", pid=paper.id))
    return _render_generate(form)


def _render_generate(form):
    picks = {}
    for key, value in form.items():
        if key.startswith("pick_") and key[5:].isdigit() and value in ("include", "exclude"):
            picks[key[5:]] = value
    first = (form.get("first_question") or "").strip()
    return render_template("admin/generate.html", form=form, subjects=subjects(), picks=picks,
                           first_id=int(first) if first.isdigit() else None)


def _build_report(questions, cfg, pool_size):
    """Fairness report for a list of Question rows (used at generation and after a swap)."""
    setter_names = {u.id: u.display_name for u in User.query.filter_by(role="setter")}
    report = generator.fairness_report([_to_candidate(q) for q in questions], cfg["duration_minutes"],
                                       setter_names)
    report["pool_size"] = pool_size
    in_paper = {q.id for q in questions}
    included = [i for i in cfg.get("include_ids") or [] if i in in_paper]
    report["included"] = len(included)
    report["excluded"] = len(cfg.get("exclude_ids") or [])
    report["replaced"] = len(cfg.get("replaced") or [])
    if included:
        report["warnings"].append(
            f"{len(included)} question(s) were chosen by the admin instead of being drawn at random "
            "(marked 📌 below)."
        )
    return report


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
    cfg = p.config
    replaced_new = {r["new"] for r in cfg.get("replaced") or []}
    return render_template("admin/paper.html", p=p, report=p.report, cfg=cfg,
                           included=set(cfg.get("include_ids") or []), replaced_new=replaced_new,
                           first_id=cfg.get("first_id"),
                           instructions_html=str(render(p.instructions)))


@bp.route("/papers/<int:pid>/details", methods=["POST"])
@role_required("admin")
def paper_details(pid):
    """Edit title, duration and instructions of a draft (does not touch the questions)."""
    p = db.session.get(Paper, pid) or abort(404)
    if p.status != "draft":
        flash("Only draft papers can be edited.", "warning")
        return redirect(url_for("admin.paper", pid=p.id))
    try:
        duration = parse_int(request.form.get("duration_minutes"), "Duration", 1, 1440)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.paper", pid=p.id))
    p.title = (request.form.get("title") or "").strip()[:200] or p.title
    p.instructions = clean_input(request.form.get("instructions"))
    if duration != p.duration_minutes:
        p.duration_minutes = duration
        cfg = p.config
        cfg["duration_minutes"] = duration
        p.config_json = json.dumps(cfg)
        _refresh_report(p, cfg)
    log(g.user, "paper_edit_details", p.code)
    db.session.commit()
    flash("Paper details saved.", "success")
    return redirect(url_for("admin.paper", pid=p.id))


def _refresh_report(p, cfg):
    questions = [it.question for it in p.items]
    p.report_json = json.dumps(_build_report(questions, cfg, pool_size=p.report.get("pool_size", 0)))


@bp.route("/papers/<int:pid>/first", methods=["POST"])
@role_required("admin")
def set_first_question(pid):
    """Choose (or clear) the question that is question 1 in every set of a draft paper."""
    p = db.session.get(Paper, pid) or abort(404)
    if p.status != "draft":
        flash("The first question can only be changed while the paper is a draft.", "warning")
        return redirect(url_for("admin.paper", pid=p.id))
    cfg = p.config
    raw = request.form.get("item", "")
    if raw == "none":
        cfg["first_id"] = None
        log(g.user, "paper_first_question", f"{p.code}: cleared")
        flash("No question is fixed as first any more.", "info")
    else:
        item = next((it for it in p.items if str(it.id) == raw), None) or abort(404)
        cfg["first_id"] = item.question_id
        ordered = generator.put_first(list(p.items), lambda it: it.id, item.id)
        for pos, it in enumerate(ordered, start=1):
            it.position = pos
        log(g.user, "paper_first_question", f"{p.code}: Q{item.question_id}")
        flash("That question is now question 1 in every set.", "success")
    p.config_json = json.dumps(cfg)
    db.session.commit()
    return redirect(url_for("admin.paper", pid=p.id))


@bp.route("/papers/<int:pid>/items/<int:iid>/replace", methods=["POST"])
@role_required("admin")
def replace_question(pid, iid):
    """Swap one question of a draft for another randomly drawn one (same difficulty and marks)."""
    p = db.session.get(Paper, pid) or abort(404)
    item = next((it for it in p.items if it.id == iid), None) or abort(404)
    if p.status != "draft":
        flash("Questions can only be swapped while the paper is a draft.", "warning")
        return redirect(url_for("admin.paper", pid=p.id))
    reason = (request.form.get("reason") or "").strip()[:300]
    cfg = p.config
    pool = _pool_for(cfg)
    current = [_to_candidate(it.question) for it in p.items]
    old = next(c for c in current if c.id == item.question_id)
    try:
        new, seed = generator.pick_replacement(
            [_to_candidate(q) for q in pool], current, old,
            balance_setters=cfg.get("balance_setters", True), cover_topics=cfg.get("cover_topics", True),
        )
    except generator.GenerationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.paper", pid=p.id))
    q = db.session.get(Question, new.id)
    old_id = item.question_id
    item.question_id, item.question = q.id, q
    item.text, item.answer, item.topic = q.text, q.answer, q.topic
    item.difficulty, item.marks, item.est_minutes = q.difficulty, q.marks, q.est_minutes
    # The swapped-out question stays out of this paper's pool if it is swapped again.
    cfg["exclude_ids"] = sorted(set(cfg.get("exclude_ids") or []) | {old_id})
    cfg["include_ids"] = [i for i in cfg.get("include_ids") or [] if i != old_id]
    if cfg.get("first_id") == old_id:
        cfg["first_id"] = q.id  # the swapped-in question takes over the "first in every set" slot
    cfg.setdefault("replaced", []).append({"old": old_id, "new": q.id, "seed": seed, "reason": reason})
    p.config_json = json.dumps(cfg)
    _refresh_report(p, cfg)
    log(g.user, "paper_replace_question",
        f"{p.code}: Q{old_id} -> Q{q.id} (random, seed={seed})" + (f" - {reason}" if reason else ""))
    db.session.commit()
    flash(f"Question {item.position} was replaced by a randomly drawn {q.difficulty} question "
          f"worth {q.marks} mark(s).", "success")
    return redirect(url_for("admin.paper", pid=p.id) + f"#item-{item.id}")


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
                                   p.config.get("order", "difficulty"),
                                   first_id=p.config.get("first_id"), id_key=lambda it: it.question_id)
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

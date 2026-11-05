import time

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .models import User, db, log
from .utils import login_user

bp = Blueprint("auth", __name__)

# Very small in-memory brute-force guard: 5 failures per username -> 5 minute lockout.
_FAILS = {}
_MAX_FAILS, _LOCK_SECONDS = 5, 300


def _locked(username):
    count, since = _FAILS.get(username, (0, 0))
    if count >= _MAX_FAILS and time.time() - since < _LOCK_SECONDS:
        return True
    if count >= _MAX_FAILS:
        _FAILS.pop(username, None)
    return False


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if User.query.count() == 0:
        return redirect(url_for("auth.setup"))
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if _locked(username):
            flash("Too many failed attempts. Try again in a few minutes.", "danger")
            return render_template("auth/login.html"), 429
        user = User.query.filter(db.func.lower(User.username) == username).first()
        if user and user.is_active and user.check_password(password):
            _FAILS.pop(username, None)
            login_user(user)
            log(user, "login")
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or url_for("index"))
        count, _ = _FAILS.get(username, (0, 0))
        _FAILS[username] = (count + 1, time.time())
        flash("Invalid username or password.", "danger")
    return render_template("auth/login.html")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """One-time creation of the first admin account (only while the database has no users)."""
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < 8:
            flash("Username is required and the password must be at least 8 characters.", "danger")
        elif password != request.form.get("confirm", ""):
            flash("Passwords do not match.", "danger")
        else:
            user = User(username=username, full_name=full_name or "Administrator", role="admin")
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            log(user, "setup", "First admin account created")
            db.session.commit()
            login_user(user)
            flash("Admin account created. Next, add your question setters.", "success")
            return redirect(url_for("admin.users"))
    return render_template("auth/setup.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    if g.user is None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not g.user.check_password(current):
            flash("Current password is wrong.", "danger")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new != request.form.get("confirm", ""):
            flash("Passwords do not match.", "danger")
        else:
            g.user.set_password(new)
            log(g.user, "password_change")
            db.session.commit()
            login_user(g.user)
            flash("Password changed.", "success")
            return redirect(url_for("index"))
    return render_template("auth/password.html")

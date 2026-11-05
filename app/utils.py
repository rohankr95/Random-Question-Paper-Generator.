import hmac
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from .models import DIFFICULTIES, User, db


def init_app(app):
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid is not None:
            user = db.session.get(User, uid)
            if user and user.is_active and session.get("pw") == user.password_hash[-16:]:
                g.user = user
            else:
                session.clear()

    @app.before_request
    def check_csrf():
        if request.method == "POST" and not app.config.get("TESTING_NO_CSRF"):
            sent = request.form.get("csrf_token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Invalid or expired form. Please go back, reload the page and try again.")

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    # Globals (not context vars) so they also work inside imported macros.
    app.jinja_env.globals.update(csrf_token=csrf_token, DIFFICULTIES=DIFFICULTIES)

    @app.context_processor
    def inject():
        return {"current_user": g.get("user")}

    for code in (400, 403, 404, 429):
        @app.errorhandler(code)
        def handle(err, code=code):
            from flask import render_template
            return render_template("error.html", code=code, message=getattr(err, "description", "")), code

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp


def login_user(user):
    session.clear()
    session["uid"] = user.id
    # Changing the password invalidates existing sessions.
    session["pw"] = user.password_hash[-16:]
    session["csrf"] = secrets.token_urlsafe(32)
    session.permanent = False


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in.", "warning")
                return redirect(url_for("auth.login", next=request.path))
            if g.user.role != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def parse_int(value, name, minimum=None, maximum=None, default=None):
    """Parse a form integer. Raises ValueError with a friendly message."""
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise ValueError(f"{name} is required.")
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number.") from None
    if minimum is not None and n < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    if maximum is not None and n > maximum:
        raise ValueError(f"{name} must be at most {maximum}.")
    return n

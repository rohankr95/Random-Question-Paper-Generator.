import os
import secrets

from flask import Flask, redirect, url_for, g

from .models import db, User


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance"), exist_ok=True)
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", "qpg.sqlite3")
        return "sqlite:///" + path
    # Render / Heroku / Neon style URLs -> SQLAlchemy + psycopg 3 driver
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if not os.environ.get("SECRET_KEY") and not (test_config or {}).get("TESTING"):
        app.logger.warning("SECRET_KEY is not set - sessions will reset on every restart.")
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    from . import auth, setter, admin, utils
    app.register_blueprint(auth.bp)
    app.register_blueprint(setter.bp)
    app.register_blueprint(admin.bp)
    utils.init_app(app)

    with app.app_context():
        db.create_all()
        _bootstrap_admin(app)

    _register_cli(app)

    @app.route("/")
    def index():
        user = g.get("user")
        if user is None:
            return redirect(url_for("auth.login"))
        return redirect(url_for("admin.dashboard" if user.role == "admin" else "setter.dashboard"))

    return app


def _bootstrap_admin(app):
    """Create the first admin from ADMIN_USERNAME / ADMIN_PASSWORD env vars if no admin exists."""
    username = os.environ.get("ADMIN_USERNAME", "admin").strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not password:
        return
    if User.query.filter_by(role="admin").first():
        return
    user = User(username=username, full_name="Administrator", role="admin")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    app.logger.info("Created admin user %r from environment.", username)


def _register_cli(app):
    import click

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(["admin", "setter"]), default="setter")
    @click.option("--name", default="")
    @click.password_option()
    def create_user(username, role, name, password):
        """Create an admin or setter account from the command line."""
        if User.query.filter_by(username=username.lower()).first():
            raise click.ClickException("User already exists.")
        user = User(username=username.lower(), full_name=name, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created {role} {username}.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create demo accounts (admin/admin12345, setter1..3/setter12345) and ~60 approved questions."""
        import random as _r
        from .models import Question
        rng = _r.Random(42)
        if not User.query.filter_by(role="admin").first():
            admin = User(username="admin", full_name="Demo Admin", role="admin")
            admin.set_password("admin12345")
            db.session.add(admin)
        setters = []
        for i in range(1, 4):
            u = User.query.filter_by(username=f"setter{i}").first()
            if not u:
                u = User(username=f"setter{i}", full_name=f"Setter {i}", role="setter")
                u.set_password("setter12345")
                db.session.add(u)
            setters.append(u)
        db.session.flush()
        topics = ["Algebra", "Geometry", "Trigonometry", "Statistics", "Calculus"]
        spec = {"easy": ([1, 2], [2, 3, 4]), "medium": ([3, 4], [5, 6, 8]), "difficult": ([5, 6, 8], [10, 12, 15])}
        n = 0
        for diff, (marks, mins) in spec.items():
            for k in range(20):
                topic = topics[k % len(topics)]
                q = Question(subject="Mathematics", topic=topic, difficulty=diff, marks=rng.choice(marks),
                             est_minutes=rng.choice(mins), setter_id=setters[k % 3].id, status="approved",
                             answer=f"Model answer for demo question {diff}-{k + 1}.")
                q.set_text(f"[Demo] {topic} {diff} question #{k + 1}: solve problem {rng.randint(100, 999)}.")
                db.session.add(q)
                n += 1
        db.session.commit()
        click.echo(f"Seeded {n} demo questions. Log in as admin/admin12345 or setter1/setter12345.")

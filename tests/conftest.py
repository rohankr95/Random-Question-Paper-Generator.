import re

import pytest

from app import create_app
from app.models import Question, User, db


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/t.db",
                      "SECRET_KEY": "test"})
    with app.app_context():
        admin = User(username="admin", full_name="Admin", role="admin")
        admin.set_password("adminpass")
        db.session.add(admin)
        for i in (1, 2, 3):
            s = User(username=f"s{i}", full_name=f"Setter {i}", role="setter")
            s.set_password("setterpass")
            db.session.add(s)
        db.session.commit()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_of(client, url):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, username, password):
    token = csrf_of(client, "/login")
    return client.post("/login", data={"username": username, "password": password, "csrf_token": token},
                       follow_redirects=True)


def post(client, url, data, follow=True):
    token = csrf_of(client, "/account/password")
    return client.post(url, data={**data, "csrf_token": token}, follow_redirects=follow)


def add_approved(app, n_per_level=6):
    with app.app_context():
        setters = User.query.filter_by(role="setter").all()
        marks = {"easy": [1, 2], "medium": [3, 4], "difficult": [5, 6]}
        for d, ms in marks.items():
            for i in range(n_per_level):
                q = Question(subject="Maths", topic=f"T{i % 3}", difficulty=d, marks=ms[i % 2],
                             est_minutes=3, setter_id=setters[i % 3].id, status="approved")
                q.set_text(f"{d} question number {i} about topic {i % 3} {'x' * i}")
                db.session.add(q)
        db.session.commit()

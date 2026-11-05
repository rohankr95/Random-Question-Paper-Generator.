"""Database models."""
import json
import re
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

DIFFICULTIES = ("easy", "medium", "difficult")
QUESTION_STATUSES = ("pending", "approved", "rejected")
PAPER_STATUSES = ("draft", "final", "discarded")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_text(text):
    """Lower-case, strip formatting/punctuation and collapse whitespace (used for duplicate checks)."""
    from .richtext import to_plain
    text = re.sub(r"[^a-z0-9 ]+", " ", to_plain(text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False)  # "admin" or "setter"
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    questions = db.relationship("Question", back_populates="setter", foreign_keys="Question.setter_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self):
        return self.full_name or self.username


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    normalized = db.Column(db.Text, nullable=False, default="")
    answer = db.Column(db.Text, nullable=False, default="")  # model answer / key, admin-only in papers
    subject = db.Column(db.String(120), nullable=False, index=True)
    topic = db.Column(db.String(120), nullable=False, default="")
    difficulty = db.Column(db.String(16), nullable=False, index=True)
    marks = db.Column(db.Integer, nullable=False)
    est_minutes = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    review_note = db.Column(db.String(500), nullable=False, default="")
    setter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    times_used = db.Column(db.Integer, nullable=False, default=0)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    setter = db.relationship("User", back_populates="questions", foreign_keys=[setter_id])

    def set_text(self, text):
        self.text = text.strip()
        self.normalized = normalize_text(text)


class Paper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    total_marks = db.Column(db.Integer, nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    instructions = db.Column(db.Text, nullable=False, default="")
    num_sets = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(16), nullable=False, default="draft")
    status_note = db.Column(db.String(500), nullable=False, default="")
    seed = db.Column(db.String(32), nullable=False)
    config_json = db.Column(db.Text, nullable=False, default="{}")
    report_json = db.Column(db.Text, nullable=False, default="{}")
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    finalized_at = db.Column(db.DateTime, nullable=True)

    created_by = db.relationship("User")
    items = db.relationship(
        "PaperQuestion", back_populates="paper", order_by="PaperQuestion.position",
        cascade="all, delete-orphan",
    )

    @property
    def config(self):
        return json.loads(self.config_json or "{}")

    @property
    def report(self):
        return json.loads(self.report_json or "{}")


class PaperQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    paper_id = db.Column(db.Integer, db.ForeignKey("paper.id"), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    # Snapshot so a printed paper never changes even if the question is edited later.
    text = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False, default="")
    difficulty = db.Column(db.String(16), nullable=False)
    marks = db.Column(db.Integer, nullable=False)
    est_minutes = db.Column(db.Integer, nullable=False)
    topic = db.Column(db.String(120), nullable=False, default="")

    paper = db.relationship("Paper", back_populates="items")
    question = db.relationship("Question")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    detail = db.Column(db.Text, nullable=False, default="")

    user = db.relationship("User")


def log(user, action, detail=""):
    db.session.add(AuditLog(user_id=user.id if user else None, action=action, detail=detail))

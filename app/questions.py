"""Shared question helpers used by both setter and admin views."""
import csv
import io
from difflib import SequenceMatcher

from .models import DIFFICULTIES, Question, db, normalize_text
from .utils import parse_int

SIMILARITY_THRESHOLD = 0.85

DIFFICULTY_ALIASES = {
    "easy": "easy", "e": "easy",
    "medium": "medium", "med": "medium", "m": "medium",
    "difficult": "difficult", "diff": "difficult", "hard": "difficult", "h": "difficult", "d": "difficult",
}


def find_similar(text, subject, exclude_id=None):
    """Return (question, ratio) of the most similar existing question in the subject, or None."""
    norm = normalize_text(text)
    if not norm:
        return None
    best = None
    query = Question.query.filter(db.func.lower(Question.subject) == subject.strip().lower(),
                                  Question.status != "rejected")
    if exclude_id:
        query = query.filter(Question.id != exclude_id)
    for q in query.with_entities(Question.id, Question.normalized):
        sm = SequenceMatcher(None, norm, q.normalized, autojunk=False)
        if sm.real_quick_ratio() < SIMILARITY_THRESHOLD or sm.quick_ratio() < SIMILARITY_THRESHOLD:
            continue
        ratio = sm.ratio()
        if ratio >= SIMILARITY_THRESHOLD and (best is None or ratio > best[1]):
            best = (q.id, ratio)
    return best


def parse_question_form(form):
    """Validate a question form. Returns a dict of clean values or raises ValueError."""
    text = (form.get("text") or "").strip()
    if len(text) < 5:
        raise ValueError("Question text is too short.")
    subject = (form.get("subject") or "").strip()
    if not subject:
        raise ValueError("Subject is required.")
    difficulty = DIFFICULTY_ALIASES.get((form.get("difficulty") or "").strip().lower())
    if difficulty not in DIFFICULTIES:
        raise ValueError("Difficulty must be easy, medium or difficult.")
    return {
        "text": text,
        "subject": subject[:120],
        "topic": (form.get("topic") or "").strip()[:120],
        "difficulty": difficulty,
        "marks": parse_int(form.get("marks"), "Marks", 1, 100),
        "est_minutes": parse_int(form.get("est_minutes"), "Estimated time", 1, 600),
        "answer": (form.get("answer") or "").strip(),
    }


def apply_values(question, values):
    question.set_text(values["text"])
    for key in ("subject", "topic", "difficulty", "marks", "est_minutes", "answer"):
        setattr(question, key, values[key])


def subjects():
    rows = db.session.query(Question.subject).distinct().order_by(Question.subject).all()
    return [r[0] for r in rows]


def topics():
    rows = db.session.query(Question.topic).filter(Question.topic != "").distinct().order_by(Question.topic).all()
    return [r[0] for r in rows]


CSV_HEADERS = ["text", "subject", "topic", "difficulty", "marks", "est_minutes", "answer"]


def parse_csv(file_storage):
    """Yield (line_number, values_dict | None, error | None) for each data row."""
    raw = file_storage.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or "text" not in [h.strip().lower() for h in reader.fieldnames]:
        raise ValueError("CSV must have a header row with at least: " + ", ".join(CSV_HEADERS))
    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        if not any(v.strip() for v in row.values()):
            continue
        try:
            yield line_no, parse_question_form(row), None
        except ValueError as exc:
            yield line_no, None, str(exc)

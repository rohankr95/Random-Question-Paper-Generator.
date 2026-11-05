# Random Question Paper Generator

A small web app for setting exam papers fairly:

* **Question setters** (2–3 people or more) log in and add questions, each tagged
  **easy / medium / difficult**, with **marks** and an **estimated solving time**.
* The **admin** reviews the questions, then sets a blueprint (total marks, number of easy,
  medium and difficult questions) and clicks **Generate**. The app draws the paper **at random**,
  so nobody hand-picks it.

Built with Python + Flask + SQLite (or PostgreSQL). It has no build step and runs on free hosting.

---

## Features

### Question setter
| Feature | Notes |
|---|---|
| Login per setter | The admin creates the accounts. Each setter can change their own password. |
| Add / edit / delete questions | Text, subject, topic, difficulty, marks, estimated minutes and an optional model answer. |
| Formatted text & tables | The question and answer boxes are rich-text editors: **bold**, *italic*, underline, super/subscript, lists, alignment, symbols and **tables**. Tables pasted from Excel, Word or Google Docs/Sheets keep their rows and columns. The server strips anything unsafe. |
| CSV bulk import | See `samples/questions_sample.csv`. |
| Duplicate detection | Warns when a new question is ≥85% similar to one already in the subject pool. The other setter's text is not shown. |
| Review status | Each question is *pending*, *approved* or *rejected*, and a rejection comes with the reviewer's note. Approved questions are locked. |
| Confidentiality | Setters see only their own questions. They never see generated papers or which questions were used. |

### Admin
| Feature | Notes |
|---|---|
| Dashboard | Approved pool by subject × difficulty, each setter's contributions and recent papers. |
| User management | Create setters or admins, disable accounts and reset passwords. |
| Review queue | Bulk approve or reject, with a note to the setter. Possible duplicates are flagged. |
| Question bank | Filter by subject, topic, difficulty, status and setter. Edit any question. |
| **One-click generation** | Choose subject, total marks, duration and the number of easy / medium / difficult questions, then press **Generate**. |
| Live pool check | Before you generate, shows how many questions are available at each level and which totals are achievable with your mix. |
| Fairness report | Marks and time per difficulty, estimated time compared with exam duration, setter and topic coverage, and how many questions are fresh. |
| Draft → Final / Discard | Finalising marks the questions as used. A discard needs a reason, and discarded papers stay in the history. |
| Multiple sets (A–F) | Same questions in a different order, which discourages copying. Each set has its own answer key. |
| Print / PDF | A clean printable paper (use the browser's *Save as PDF*). Drafts carry a "DRAFT" watermark. |
| Audit log | Records logins, reviews, every generation with its random seed, finalisations, discards and prints. |

### How the app keeps papers fair
1. **No hand-picking.** The admin sets only the blueprint. Questions are drawn using a seed from
   the operating system's secure random generator (`secrets`), and the seed is stored with the paper.
2. **Exact marks with no bias.** When you set a total-marks target, a counting algorithm makes
   *every* set of questions that meets the blueprint equally likely to be chosen. It doesn't
   lean towards high- or low-mark questions. If no combination reaches the target, it tells you the
   closest achievable totals.
3. **Peer review.** Only approved questions enter the pool. This checks that the difficulty
   tags, marks and time estimates are sensible.
4. **Setter balance.** Every setter gets an equal chance at each slot, however many questions
   they wrote. That chance drops sharply once the setter already has a question in the paper,
   so one person can't dominate.
5. **Topic spread.** The draw prefers topics the paper doesn't cover yet.
6. **Freshness.** Questions used less often in earlier final papers are preferred. You can also
   exclude everything from the last *N* final papers, or use never-used questions only.
7. **Time check.** The paper is flagged if the estimated solving time is longer than the exam
   duration, or shorter than half of it.
8. **Transparency.** Every generated paper is kept, including discarded ones, and every draw is
   in the audit log. Regenerating until you like the result therefore leaves a visible trail.
9. **Snapshots.** A paper stores a copy of its questions, so a later edit can't change a paper
   that was already generated.
10. **Easy → difficult ordering.** Questions are shuffled within each level, and every set
    keeps the same structure.

---

## Run it locally

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
flask --app wsgi run
```

Open http://127.0.0.1:5000. On the first visit you are asked to create the admin account.
Then go to **Users** and add your question setters.

To try it with sample data (admin `admin` / `admin12345`, setters `setter1..3` / `setter12345`, and 60 approved maths questions):

```bash
flask --app wsgi seed-demo
```

Run the tests:

```bash
pip install -r requirements-dev.txt && pytest
```

### Configuration (environment variables)
| Variable | Purpose |
|---|---|
| `SECRET_KEY` | **Required in production.** A long random string that signs login cookies. |
| `DATABASE_URL` | Optional. Leave it empty for SQLite (`instance/qpg.sqlite3`), or give a PostgreSQL URL. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Optional. Creates the first admin on start-up if none exists, instead of using the `/setup` page. |
| `SESSION_COOKIE_SECURE` | Set to `1` when the site is served over HTTPS. |

---

## Free hosting

### Option A: PythonAnywhere (recommended: free, always on, keeps your SQLite data)
1. Create a free *Beginner* account at https://www.pythonanywhere.com.
2. Open a **Bash console** and run:
   ```bash
   git clone https://github.com/rohankr95/Random-Question-Paper-Generator..git qpg
   ```
   ```bash
   cd qpg && mkvirtualenv --python=python3.11 qpg && pip install -r requirements.txt
   ```
3. Go to **Web → Add a new web app → Manual configuration → Python 3.11**.
4. Under **Virtualenv**, enter `/home/<your-username>/.virtualenvs/qpg`.
5. Open the **WSGI configuration file** link, replace its contents with the following, and change the values:
   ```python
   import os, sys
   sys.path.insert(0, "/home/<your-username>/qpg")
   os.environ["SECRET_KEY"] = "paste-a-long-random-string-here"
   os.environ["SESSION_COOKIE_SECURE"] = "1"
   from wsgi import app as application
   ```
6. Turn on **Force HTTPS**, click **Reload**, and visit `https://<your-username>.pythonanywhere.com`.
   Create the admin account on the first-time setup page.
7. A free web app has to be renewed once every 3 months by clicking "Run until 3 months from today"
   on the Web tab. Your data is kept in `~/qpg/instance/qpg.sqlite3`. Download a copy of it now and then as a backup.

To update later, run `cd ~/qpg && git pull` and click **Reload**.

### Option B: Render (free web service) + Neon (free PostgreSQL)
Render's free disk is wiped on every restart, so use a free hosted PostgreSQL database:
1. Create a free database at https://neon.tech and copy its connection string.
2. On https://render.com choose **New → Blueprint** and select this repository. `render.yaml` is
   picked up automatically.
3. When prompted, set `DATABASE_URL` to the Neon connection string and choose an `ADMIN_PASSWORD`.
4. Free Render services sleep after about 15 minutes without traffic. The first request after that takes around 30–60 seconds.

---

## Project layout
```
app/
  __init__.py      app factory, config, CLI commands (create-user, seed-demo)
  models.py        User, Question, Paper, PaperQuestion, AuditLog
  generator.py     the fair random selection algorithm (pure Python, unit-tested)
  questions.py     validation, duplicate detection, CSV import
  richtext.py      sanitising/rendering of formatted (HTML) question text
  auth.py          login, logout, first-time setup, change password
  setter.py        question-setter pages
  admin.py         admin pages: review, bank, users, generate, papers, audit
  templates/       Jinja templates
  static/          CSS (screen + print), editor.js (rich-text editor set-up)
samples/           example CSV for bulk import
tests/             pytest suite
wsgi.py            WSGI entry point
render.yaml        Render blueprint
```

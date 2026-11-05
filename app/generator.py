"""Fair, random question-paper selection.

This module is pure Python (no Flask / DB) so it is easy to test.

How a paper is picked
---------------------
1. The caller passes the pool of eligible questions (already filtered by subject,
   approval status and any "recently used" exclusions).
2. If a total-marks target is given, we pick *which marks values* to use with an
   exact counting DP: every combination of questions that hits the requested
   counts and the requested total is equally likely to be chosen. There is no
   bias towards high- or low-mark questions, and nobody can steer the outcome.
3. Within each marks bucket the actual questions are drawn by weighted random
   sampling that favours:
     * questions used less often in earlier final papers (freshness),
     * an equal chance for every setter, lowered for setters already in this paper
       (setter balance),
     * topics not yet covered in this paper (syllabus coverage).
4. Randomness comes from a seed produced by the OS CSPRNG (``secrets``). The
   seed is stored with the paper so the draw is auditable.
"""
import random
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import comb

DIFFICULTY_ORDER = ("easy", "medium", "difficult")


class GenerationError(Exception):
    pass


@dataclass
class Candidate:
    id: int
    difficulty: str
    marks: int
    est_minutes: int = 0
    setter_id: int = 0
    topic: str = ""
    times_used: int = 0


@dataclass
class Selection:
    seed: str
    questions: list = field(default_factory=list)  # list[Candidate], in paper order


def new_seed():
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Counting DP over marks values
# ---------------------------------------------------------------------------

def _suffix_tables(buckets, max_k):
    """buckets: list of (marks_value, available_count).

    Returns tables[i] = {(k, s): number_of_question_subsets} using only buckets[i:],
    where k = number of questions and s = marks sum.
    """
    tables = [None] * (len(buckets) + 1)
    tables[len(buckets)] = {(0, 0): 1}
    for i in range(len(buckets) - 1, -1, -1):
        value, avail = buckets[i]
        nxt = tables[i + 1]
        cur = defaultdict(int)
        for (k, s), ways in nxt.items():
            for n in range(0, min(avail, max_k - k) + 1):
                cur[(k + n, s + n * value)] += ways * comb(avail, n)
        tables[i] = dict(cur)
    return tables


def _sample_proportional(rng, weighted_items):
    """weighted_items: list of (item, big_int_weight). Exact for arbitrarily large ints."""
    total = sum(w for _, w in weighted_items)
    if total <= 0:
        raise GenerationError("Nothing to choose from.")
    r = rng.randrange(total)
    for item, w in weighted_items:
        if r < w:
            return item
        r -= w
    return weighted_items[-1][0]  # pragma: no cover


class _DifficultyPlan:
    def __init__(self, pool, k):
        self.k = k
        by_marks = Counter(c.marks for c in pool)
        self.buckets = sorted(by_marks.items())
        self.tables = _suffix_tables(self.buckets, k)
        # ways[s] = number of k-question subsets summing to s
        self.ways = {s: w for (kk, s), w in self.tables[0].items() if kk == k}

    def sample_counts(self, rng, s):
        """Pick how many questions of each marks value to use (uniform over subsets)."""
        counts = {}
        k_left, s_left = self.k, s
        for i, (value, avail) in enumerate(self.buckets):
            nxt = self.tables[i + 1]
            options = []
            for n in range(0, min(avail, k_left) + 1):
                ways = nxt.get((k_left - n, s_left - n * value), 0)
                if ways:
                    options.append((n, ways * comb(avail, n)))
            n = _sample_proportional(rng, options)
            counts[value] = n
            k_left -= n
            s_left -= n * value
        assert k_left == 0 and s_left == 0
        return counts


def achievable_totals(pool, counts):
    """Return the sorted list of total marks reachable with the requested counts."""
    sums = {0}
    for d in DIFFICULTY_ORDER:
        k = counts.get(d, 0)
        dpool = [c for c in pool if c.difficulty == d]
        if k > len(dpool):
            return []
        if k == 0:
            continue
        plan = _DifficultyPlan(dpool, k)
        sums = {a + b for a in sums for b in plan.ways}
    return sorted(sums)


# ---------------------------------------------------------------------------
# Fairness-weighted draw inside a marks bucket
# ---------------------------------------------------------------------------

def _weight(c, setter_picks, topic_picks, balance_setters, cover_topics, setter_sizes):
    w = 1.0 / (1 + c.times_used)  # freshness
    if balance_setters:
        # Every setter gets the same chance per slot, no matter how many questions they
        # wrote, and that chance drops sharply once they already have questions in the paper.
        w /= setter_sizes[c.setter_id] * (1 + 4 * setter_picks[c.setter_id]) ** 2
    if cover_topics and c.topic:
        w /= 1 + topic_picks[c.topic.lower()]
    return w


def _weighted_pick(rng, items, weight_fn):
    weights = [weight_fn(c) for c in items]
    total = sum(weights)
    r = rng.random() * total
    for idx, w in enumerate(weights):
        if r < w:
            return idx
        r -= w
    return len(items) - 1


def select_questions(pool, counts, total_marks=None, *, seed=None,
                     balance_setters=True, cover_topics=True, order="difficulty"):
    """Pick questions for one paper.

    pool         -- iterable of Candidate
    counts       -- {"easy": n, "medium": n, "difficult": n}
    total_marks  -- exact total required, or None for "whatever the questions add up to"
    order        -- "difficulty" (easy -> difficult, shuffled inside each level) or "random"
    """
    seed = seed or new_seed()
    rng = random.Random(seed)
    pool = list(pool)

    requested = {d: int(counts.get(d, 0) or 0) for d in DIFFICULTY_ORDER}
    if any(v < 0 for v in requested.values()):
        raise GenerationError("Question counts cannot be negative.")
    if sum(requested.values()) == 0:
        raise GenerationError("Ask for at least one question.")

    by_diff = {d: [c for c in pool if c.difficulty == d] for d in DIFFICULTY_ORDER}
    short = [f"{d}: need {requested[d]}, only {len(by_diff[d])} available"
             for d in DIFFICULTY_ORDER if requested[d] > len(by_diff[d])]
    if short:
        raise GenerationError("Not enough approved questions in the pool - " + "; ".join(short) + ".")

    # Decide how many questions of each marks value are needed per difficulty.
    plan_counts = {}  # difficulty -> {marks_value: n} or None (any marks)
    if total_marks:
        plans = {d: _DifficultyPlan(by_diff[d], requested[d]) for d in DIFFICULTY_ORDER}
        e_ways, m_ways, h_ways = (plans[d].ways for d in DIFFICULTY_ORDER)
        triples = []
        for se, we in e_ways.items():
            for sm, wm in m_ways.items():
                sh = total_marks - se - sm
                wh = h_ways.get(sh)
                if wh:
                    triples.append(((se, sm, sh), we * wm * wh))
        if not triples:
            reachable = achievable_totals(pool, requested)
            if reachable:
                near = sorted(reachable, key=lambda t: (abs(t - total_marks), t))[:6]
                hint = "Closest achievable totals: " + ", ".join(map(str, sorted(near))) + "."
            else:
                hint = "No total is achievable with this pool."
            raise GenerationError(
                f"No combination of {requested['easy']} easy, {requested['medium']} medium and "
                f"{requested['difficult']} difficult questions adds up to exactly {total_marks} marks. "
                + hint
            )
        sums = _sample_proportional(rng, triples)
        for d, s in zip(DIFFICULTY_ORDER, sums):
            plan_counts[d] = plans[d].sample_counts(rng, s)
    else:
        plan_counts = {d: None for d in DIFFICULTY_ORDER}

    # Build the list of "slots" to fill, then fill them in random order so no
    # difficulty level or marks bucket gets first pick of the setters/topics.
    slots = []
    for d in DIFFICULTY_ORDER:
        if plan_counts[d] is None:
            slots += [(d, None)] * requested[d]
        else:
            for value, n in plan_counts[d].items():
                slots += [(d, value)] * n
    rng.shuffle(slots)

    remaining = {d: list(by_diff[d]) for d in DIFFICULTY_ORDER}
    for d in remaining:
        rng.shuffle(remaining[d])  # break ties between identical weights randomly
    setter_picks, topic_picks = Counter(), Counter()
    chosen = []
    for d, value in slots:
        items = [c for c in remaining[d] if value is None or c.marks == value]
        setter_sizes = Counter(c.setter_id for c in items)
        idx = _weighted_pick(
            rng, items,
            lambda c: _weight(c, setter_picks, topic_picks, balance_setters, cover_topics, setter_sizes),
        )
        pick = items[idx]
        remaining[d].remove(pick)
        setter_picks[pick.setter_id] += 1
        if pick.topic:
            topic_picks[pick.topic.lower()] += 1
        chosen.append(pick)

    chosen = order_questions(chosen, rng, order)
    return Selection(seed=seed, questions=chosen)


def order_questions(questions, rng, order="difficulty"):
    questions = list(questions)
    rng.shuffle(questions)
    if order == "difficulty":
        rank = {d: i for i, d in enumerate(DIFFICULTY_ORDER)}
        questions.sort(key=lambda c: rank.get(c.difficulty, 99))  # stable: keeps shuffle inside level
    return questions


def shuffled_set(items, seed, set_index, difficulty_key, order="difficulty"):
    """Deterministic alternate ordering for Set B, C, ... (Set A = index 0 = original order).

    Every set contains exactly the same questions; only the order changes, and when
    ``order == "difficulty"`` each set still goes easy -> medium -> difficult.
    """
    if set_index == 0:
        return list(items)
    rng = random.Random(f"{seed}:{set_index}")
    items = list(items)
    rng.shuffle(items)
    if order == "difficulty":
        rank = {d: i for i, d in enumerate(DIFFICULTY_ORDER)}
        items.sort(key=lambda it: rank.get(difficulty_key(it), 99))
    return items


def fairness_report(questions, duration_minutes, setter_names=None):
    """Summary numbers shown next to every generated paper."""
    setter_names = setter_names or {}
    total_marks = sum(q.marks for q in questions)
    total_time = sum(q.est_minutes for q in questions)
    by_diff = {}
    for d in DIFFICULTY_ORDER:
        qs = [q for q in questions if q.difficulty == d]
        marks = sum(q.marks for q in qs)
        by_diff[d] = {
            "count": len(qs),
            "marks": marks,
            "minutes": sum(q.est_minutes for q in qs),
            "marks_pct": round(100 * marks / total_marks, 1) if total_marks else 0,
        }
    setters = Counter(q.setter_id for q in questions)
    topics = Counter((q.topic or "(no topic)") for q in questions)
    warnings = []
    if duration_minutes and total_time > duration_minutes:
        warnings.append(
            f"Estimated solving time ({total_time} min) is longer than the exam duration "
            f"({duration_minutes} min)."
        )
    elif duration_minutes and total_time < 0.5 * duration_minutes:
        warnings.append(
            f"Estimated solving time ({total_time} min) is less than half the exam duration "
            f"({duration_minutes} min) - the paper may be too short."
        )
    if len(questions) >= 4 and setters and max(setters.values()) > 0.6 * len(questions) and len(setters) > 1:
        warnings.append("More than 60% of the questions come from a single setter.")
    return {
        "total_marks": total_marks,
        "total_minutes": total_time,
        "count": len(questions),
        "by_difficulty": by_diff,
        "by_setter": {setter_names.get(s, f"Setter #{s}"): n for s, n in setters.most_common()},
        "by_topic": dict(topics.most_common()),
        "fresh_questions": sum(1 for q in questions if q.times_used == 0),
        "warnings": warnings,
    }

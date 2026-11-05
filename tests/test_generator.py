from collections import Counter

import pytest

from app.generator import (Candidate, GenerationError, achievable_totals, fairness_report,
                           select_questions, shuffled_set)


def pool():
    out, i = [], 0
    for d, marks in (("easy", [1, 2]), ("medium", [3, 4]), ("difficult", [5, 10])):
        for k in range(10):
            i += 1
            out.append(Candidate(id=i, difficulty=d, marks=marks[k % 2], est_minutes=5,
                                 setter_id=k % 3, topic=f"t{k % 4}"))
    return out


def test_counts_and_exact_total():
    for seed in range(50):
        sel = select_questions(pool(), {"easy": 4, "medium": 3, "difficult": 2}, 30, seed=str(seed))
        c = Counter(q.difficulty for q in sel.questions)
        assert c == {"easy": 4, "medium": 3, "difficult": 2}
        assert sum(q.marks for q in sel.questions) == 30
        assert len({q.id for q in sel.questions}) == 9


def test_no_total_target():
    sel = select_questions(pool(), {"easy": 2, "medium": 2, "difficult": 2}, None, seed="x")
    assert len(sel.questions) == 6


def test_same_seed_is_reproducible():
    a = select_questions(pool(), {"easy": 3, "medium": 3, "difficult": 1}, 25, seed="abc")
    b = select_questions(pool(), {"easy": 3, "medium": 3, "difficult": 1}, 25, seed="abc")
    assert [q.id for q in a.questions] == [q.id for q in b.questions]


def test_order_easy_to_difficult():
    sel = select_questions(pool(), {"easy": 3, "medium": 3, "difficult": 3}, None, seed="o")
    rank = {"easy": 0, "medium": 1, "difficult": 2}
    ranks = [rank[q.difficulty] for q in sel.questions]
    assert ranks == sorted(ranks)


def test_impossible_total_gives_hint():
    with pytest.raises(GenerationError, match="Closest achievable"):
        select_questions(pool(), {"easy": 1, "medium": 1, "difficult": 1}, 1000, seed="s")


def test_not_enough_questions():
    with pytest.raises(GenerationError, match="Not enough"):
        select_questions(pool(), {"easy": 11}, None)


def test_achievable_totals():
    totals = achievable_totals(pool(), {"easy": 1, "difficult": 1})
    assert totals == [6, 7, 11, 12]


def test_randomness_covers_pool():
    seen = Counter()
    for seed in range(300):
        sel = select_questions(pool(), {"easy": 2}, None, seed=f"r{seed}")
        seen.update(q.id for q in sel.questions)
    assert len(seen) == 10  # every easy question gets picked sometimes


def test_freshness_prefers_unused_questions():
    p = pool()
    for c in p:
        if c.difficulty == "easy" and c.id <= 5:
            c.times_used = 5
    picks = Counter()
    for seed in range(400):
        sel = select_questions(p, {"easy": 2}, None, seed=f"f{seed}")
        picks.update("used" if q.times_used else "fresh" for q in sel.questions)
    assert picks["fresh"] > 3 * picks["used"]


def test_setter_balance():
    # 9 questions from setter 0, 3 from setter 1, 3 from setter 2.
    p = [Candidate(id=i, difficulty="easy", marks=1, setter_id=0) for i in range(9)]
    p += [Candidate(id=100 + i, difficulty="easy", marks=1, setter_id=1 + i % 2) for i in range(6)]
    spread = Counter()
    for seed in range(200):
        sel = select_questions(p, {"easy": 3}, None, seed=f"b{seed}")
        spread[len({q.setter_id for q in sel.questions})] += 1
    assert spread[3] > 160  # the vast majority of papers use all three setters


def test_sets_contain_same_questions_in_different_order():
    sel = select_questions(pool(), {"easy": 4, "medium": 4, "difficult": 2}, None, seed="sets")
    a = shuffled_set(sel.questions, "sets", 0, lambda q: q.difficulty)
    b = shuffled_set(sel.questions, "sets", 1, lambda q: q.difficulty)
    assert sorted(q.id for q in a) == sorted(q.id for q in b)
    assert [q.id for q in a] != [q.id for q in b]


def test_fairness_report_time_warning():
    qs = [Candidate(id=1, difficulty="easy", marks=2, est_minutes=100)]
    r = fairness_report(qs, 60)
    assert r["total_marks"] == 2 and r["warnings"]

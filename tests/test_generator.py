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


# --- always include / exclude / swap -------------------------------------------------

from app.generator import blueprint_totals, pick_replacement


def test_forced_questions_always_present_and_count_towards_blueprint():
    p = pool()
    forced = [p[0], p[25]]  # one easy (1 mark), one difficult (10 marks)
    for seed in range(40):
        sel = select_questions(p, {"easy": 3, "medium": 2, "difficult": 2}, 30, seed=f"F{seed}", forced=forced)
        ids = [q.id for q in sel.questions]
        assert p[0].id in ids and p[25].id in ids
        assert len(ids) == len(set(ids)) == 7
        assert Counter(q.difficulty for q in sel.questions) == {"easy": 3, "medium": 2, "difficult": 2}
        assert sum(q.marks for q in sel.questions) == 30


def test_forced_more_than_blueprint_is_rejected():
    p = pool()
    with pytest.raises(GenerationError, match="more questions than the blueprint allows"):
        select_questions(p, {"easy": 1}, None, forced=[p[0], p[1]])


def test_forced_marks_above_total_rejected():
    p = pool()
    with pytest.raises(GenerationError, match="already add up to"):
        select_questions(p, {"difficult": 2}, 12, forced=[p[21], p[23]])  # 10 + 10 > 12


def test_only_forced_questions():
    p = pool()
    sel = select_questions(p, {"easy": 2}, 3, forced=[p[0], p[1]])
    assert sorted(q.id for q in sel.questions) == [p[0].id, p[1].id]


def test_blueprint_totals_with_forced():
    p = pool()
    totals, err = blueprint_totals(p, {"easy": 1, "difficult": 1}, [p[21]])  # forced difficult 10
    assert err is None and totals == [11, 12]
    _, err = blueprint_totals(p, {"easy": 1}, [p[21]])
    assert "blueprint allows" in err


def test_pick_replacement_keeps_level_and_marks():
    p = pool()
    current = [p[0], p[2], p[10]]  # easy 1, easy 1, medium 3
    for seed in range(30):
        new, _ = pick_replacement(p, current, p[0], seed=f"R{seed}")
        assert new.difficulty == "easy" and new.marks == 1 and new.id not in {c.id for c in current}


def test_pick_replacement_when_nothing_left():
    p = [Candidate(id=1, difficulty="easy", marks=1), Candidate(id=2, difficulty="easy", marks=2)]
    with pytest.raises(GenerationError, match="no other approved"):
        pick_replacement(p, [p[0]], p[0])


# --- fixed first question -------------------------------------------------------------

def test_first_question_is_first_in_every_set():
    p = pool()
    first = p[25]  # a difficult question - still first, even with easy -> difficult ordering
    for seed in range(20):
        sel = select_questions(p, {"easy": 3, "medium": 3, "difficult": 2}, None, seed=f"1st{seed}",
                               forced=[first], first_id=first.id)
        assert sel.questions[0].id == first.id
        for set_index in range(6):
            items = shuffled_set(sel.questions, f"1st{seed}", set_index, lambda q: q.difficulty,
                                 first_id=first.id, id_key=lambda q: q.id)
            assert items[0].id == first.id
            assert sorted(q.id for q in items) == sorted(q.id for q in sel.questions)


def test_first_question_must_be_included():
    p = pool()
    with pytest.raises(GenerationError, match="must also be included"):
        select_questions(p, {"easy": 2}, None, first_id=p[0].id)

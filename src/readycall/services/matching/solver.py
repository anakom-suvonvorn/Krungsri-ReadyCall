"""Assignment solvers: greedy, and the Hungarian algorithm (`D22`).

**Why not `scipy.optimize.linear_sum_assignment`?** Same reasoning as `D31` on LLM
frameworks. This is ~90 lines of well-understood algorithm against a ~30MB dependency that
would be pulled onto the STT box too. A contact centre matrix is small — tens of calls by
tens of agents — so the constant factors are irrelevant, and owning the code means the
rationale we persist is genuinely ours to explain.

**Why not just greedy?** Greedy best-first is locally optimal and globally poor. It hands
the one bilingual agent to the first caller who asks, then strands the caller who actually
needed them. `best_greedy_gap()` exists to *measure* that on real inputs — the difference is
a demo point, not an assumption.

Both solvers maximise; internally Hungarian minimises, so scores are negated on the way in.
"""

from __future__ import annotations

from readycall.logging import get_logger

log = get_logger(__name__)

#: Pairs that failed a hard filter get this, so the solver can still be handed a full
#: rectangular matrix. Large enough to never be chosen, finite so the maths stays sane.
IMPOSSIBLE = float("-1e9")


def greedy(scores: list[list[float]]) -> list[int | None]:
    """Take the best remaining pair, repeatedly. Fast, and demonstrably worse."""
    n_calls = len(scores)
    n_agents = len(scores[0]) if n_calls else 0
    assignment: list[int | None] = [None] * n_calls
    taken: set[int] = set()

    order = sorted(
        ((scores[c][a], c, a) for c in range(n_calls) for a in range(n_agents)),
        key=lambda t: -t[0],
    )
    for score, call, agent in order:
        if score <= IMPOSSIBLE or assignment[call] is not None or agent in taken:
            continue
        assignment[call] = agent
        taken.add(agent)
    return assignment


def hungarian(scores: list[list[float]]) -> list[int | None]:
    """Globally optimal assignment, maximising total score.

    Jonker-Volgenant style shortest-augmenting-path over a padded square cost matrix —
    O(n^3), and the standard formulation. Rows are calls, columns are agents.
    """
    n_calls = len(scores)
    if n_calls == 0:
        return []
    n_agents = len(scores[0])
    if n_agents == 0:
        return [None] * n_calls

    n = max(n_calls, n_agents)
    # Pad to square with zero-cost dummies, and negate: we maximise, this minimises.
    cost = [[0.0] * n for _ in range(n)]
    for i in range(n_calls):
        for j in range(n_agents):
            cost[i][j] = -scores[i][j]

    INF = float("inf")
    u = [0.0] * (n + 1)  # potentials, rows
    v = [0.0] * (n + 1)  # potentials, columns
    p = [0] * (n + 1)  # p[j] = row matched to column j
    way = [0] * (n + 1)  # augmenting path predecessor

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        # Walk the augmenting path back, re-matching as we go.
        #
        # NOT `j0, p[j0] = way[j0], p[way[j0]]`. Python evaluates the right-hand side
        # first but then assigns LEFT TO RIGHT, so `j0` is rebound before `p[j0]` is
        # resolved and the write lands on the wrong column. The whole matching comes back
        # empty, silently, with no error. Three statements, on purpose.
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    assignment: list[int | None] = [None] * n_calls
    for j in range(1, n + 1):
        row = p[j] - 1
        col = j - 1
        # Drop pairs that only exist because of padding, or that failed a hard filter.
        in_range = 0 <= row < n_calls and 0 <= col < n_agents
        if in_range and scores[row][col] > IMPOSSIBLE:
            assignment[row] = col
    return assignment


def total_score(scores: list[list[float]], assignment: list[int | None]) -> float:
    return sum(
        scores[c][a]
        for c, a in enumerate(assignment)
        if a is not None and scores[c][a] > IMPOSSIBLE
    )


def best_greedy_gap(scores: list[list[float]]) -> tuple[float, float]:
    """`(hungarian_total, greedy_total)` on the same matrix.

    Used by the matching simulator to show the cost of greedy on real load rather than
    asserting it. If the gap is ever zero across a realistic run, that is worth knowing too.
    """
    return total_score(scores, hungarian(scores)), total_score(scores, greedy(scores))


__all__ = ["IMPOSSIBLE", "best_greedy_gap", "greedy", "hungarian", "total_score"]

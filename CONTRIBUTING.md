# Contributing to Warden

Thanks for wanting to contribute. Warden is an authorization library, so the bar for correctness is high — but the codebase is small, the tests are fast, and every change is welcome as long as the discipline in this file is preserved.

Read this file end to end before your first PR. It is short on purpose.

---

## The one non-negotiable rule

**The oracle is the seatbelt. Do not weaken it.**

`core/oracle.py` is a deliberately stupid, brute-force reference implementation. It is O(n²) on purpose. Every optimization is a place a bug can hide, and the whole point of the oracle is that it *cannot* have bugs the real engine might have.

- **Do not optimize `core/oracle.py`.** If it becomes hot, the fix is to use the real engine (`core/rebac.py`, W1+) instead.
- **Do not share code between `oracle.py` and `rebac.py`.** They must be able to have *different* bugs so the differential harness catches them.
- **Do not delete or skip a differential test to make a build pass.** If a differential test starts failing, the change under review is what's wrong — not the test.

---

## Setup

```bash
git clone https://github.com/geminimir/warden.git
cd warden
make install       # creates .venv, installs -e ".[dev]"
make oracle        # 15 handwritten fixtures — must be green
make test          # full suite (oracle + generators + differential)
```

Python 3.11+ required. macOS + Linux supported; Windows via WSL.

---

## Dev loop

```bash
make oracle          # fixture tests only — fast, run these first
make test            # oracle + generators + differential harness
make differential    # property tests against the oracle (currently xfail until W1)
make lint            # ruff + mypy
make format          # ruff format + autofix
```

Aim for **`make test` green** before opening a PR. If differential tests XPASS unexpectedly, that's a signal — the marker needs to come off (see `evals/differential.py`).

---

## How work is organized

- **[Milestones](https://github.com/geminimir/warden/milestones)** are the roadmap. W0 → W5, ship in order.
- **Issues** are the work packages inside each milestone. Every issue has explicit acceptance criteria.
- **Every PR references an issue** (`Closes #N`). If you want to work on something not tracked yet, open the issue first — that's where scope gets agreed on.

## Branch + PR flow

Branches are named `<milestone>/<short-slug>`, e.g.:

- `w1/rebac-check`
- `w2/label-materialization`
- `w4/scenario-3-stale-revocation`

PRs target `main`. Squash-merge with a Conventional Commit title:

- `feat(w1): add check() with reason paths`
- `fix(w2): correct GIN operator for barrier tag overlap`
- `docs: clarify invariant 2 in README`
- `test(w0): additional fixture for cyclic barrier`

The PR template (`.github/PULL_REQUEST_TEMPLATE.md`) has the invariant checklist. Please actually read it — it's short.

---

## Code style

- **Ruff** for lint + format (config in `pyproject.toml`).
- **mypy** for types on `core/` and `evals/`.
- **Comments explain the *why*, not the *what*.** If a well-named identifier already says what the code does, delete the comment. If there's a subtle invariant or a workaround for a specific class of bug, document it — that context does not survive git blame.
- **Tests are code.** Handwritten fixture tests get the same review as production code. If a test's assertion isn't obvious from reading it, add a one-line comment explaining what failure mode it protects against.

---

## Writing tests

Two categories, both matter:

1. **Handwritten fixture tests** (`tests/test_oracle.py`, `tests/test_generators.py`) — each targets a specific known failure mode. Names should describe the failure mode, not the mechanism. Example: `test_ethical_wall_overrides_grant`, not `test_check_returns_false`.

2. **Property tests** (`evals/differential.py`, plus similar harnesses in later milestones) — compare implementations against the oracle across randomly generated inputs. If you're adding a new invariant, add a property test *first* and let it drive the implementation.

If you find a bug, the workflow is:

1. Write a failing handwritten fixture test that reproduces it.
2. If the property tests missed it, add a generator that would have caught it.
3. Fix the bug.
4. Merge. All three artifacts stay.

Regression tests are permanent. Do not delete them once the bug is fixed.

---

## Security-sensitive changes

Anything touching `core/algebra.py`, `core/oracle.py`, `core/rebac.py` (once it exists), `core/barriers.py`, or the three-gate wiring in `gateway/` counts as security-sensitive. For those PRs:

- The differential harness must be green at the same or larger example count as the previous main.
- Adversarial scenarios (once they exist in W4) must be green.
- Reviewers will read the diff line by line. Please keep it small.

If you're reporting or fixing a **vulnerability**, do NOT open a public issue or PR. See [`SECURITY.md`](./SECURITY.md).

---

## Design docs

Two documents live outside the repo and are the source of truth for scope and invariants:

- `warden-design.md` — feasibility, tradeoffs, competitive analysis.
- `warden-engineering-doc.md` — what to build, in what order, when to stop.

If your change contradicts either of those, either the doc is wrong (open a PR to change it) or the change is wrong. There is no third option.

---

## Getting help

- **Bug** → open an issue with the bug template.
- **Feature idea** → open an issue with the feature template.
- **Security** → [`SECURITY.md`](./SECURITY.md).
- **General discussion** → [Discussions](https://github.com/geminimir/warden/discussions) (if enabled).

---

By contributing you agree that your contributions are licensed under the same license as the project (Apache-2.0), and you follow the [Code of Conduct](./CODE_OF_CONDUCT.md).

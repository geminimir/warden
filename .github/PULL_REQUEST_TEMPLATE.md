<!--
Please read CONTRIBUTING.md before your first PR. It is short.
Delete any section that doesn't apply — but do NOT delete the checklist.
-->

## Summary

<!-- What does this change do, and why? One or two sentences. -->

Closes #

## Type of change

- [ ] `feat` — new user-facing capability
- [ ] `fix` — bug fix (including security fixes coordinated privately first per SECURITY.md)
- [ ] `refactor` — internal restructure with no behavior change
- [ ] `perf` — measurable performance change
- [ ] `test` — tests added / adjusted
- [ ] `docs` — documentation only
- [ ] `chore` — build, CI, tooling

## Invariant checklist

<!-- These are load-bearing. If you can't check one, explain why below. -->

- [ ] `make oracle` is green.
- [ ] `make test` is green.
- [ ] If `make differential` was xfail before this PR, it is still xfail — OR the marker has been removed and the tests are truly green.
- [ ] No code was added to `core/oracle.py` that shares implementation with the real engine (`core/rebac.py` and friends). They must be able to have different bugs.
- [ ] If this change touches Gate 2 (authoritative check) semantics, a differential test was added that would have caught the previous behavior.
- [ ] If this change touches the pre-filter (Gate 1) or label materialization, the permissive-superset invariant (`LabelFilter(u) ⊇ Authorized(u)`) is still asserted somewhere in the test suite.

## Security-sensitive?

- [ ] This PR touches `core/algebra.py`, `core/oracle.py`, `core/rebac.py`, `core/barriers.py`, `core/labels.py`, or the gate wiring in `gateway/`.

If yes:

- [ ] I have read the invariants section of the README.
- [ ] I am NOT fixing an undisclosed vulnerability via this public PR. (If I am, I should be using GitHub Security Advisories instead — see SECURITY.md.)

## Test evidence

<!--
Paste the `make test` summary line or a screenshot. For benchmark-affecting
changes, include a before/after number.
-->

```
```

## Notes for reviewers

<!-- Anything worth flagging: tradeoffs made, alternatives considered, follow-ups deferred. -->

# Security Policy

Warden is an authorization library. A vulnerability here is not the same class of bug as a vulnerability in most repos — a single hole can leak documents the requesting principal was never entitled to see, cross organizational boundaries, or bypass information-barrier walls that exist for legal or compliance reasons.

We take these reports seriously. Please report them privately.

---

## Reporting a vulnerability

**Do not open a public GitHub issue or pull request for security vulnerabilities.**

Use GitHub's private vulnerability reporting:

> **[Report a vulnerability →](https://github.com/geminimir/warden/security/advisories/new)**

This creates a private security advisory visible only to you and the maintainers.

If GitHub advisory reporting is unavailable for any reason, email the maintainers at the address listed on the [organization profile](https://github.com/geminimir). Include enough detail that we can reproduce the issue without a follow-up round trip.

## What to include

A useful report contains:

- **A minimal reproduction** — the smallest tuple graph, sequence of calls, or configuration that demonstrates the leak.
- **What you expected** vs. **what happened**.
- **Which invariant it breaks** — safety (G1, no unauthorized doc reaches the model), fidelity (G2, no authorized doc is silently dropped), temporal (G4, revocation propagation), or audit (G5, tamper-evidence).
- **Impact scope** — is it cross-tenant, intra-tenant, temporal-only, or audit-only?
- **The version/commit** you found it on.

If you don't know which invariant it maps to, that's fine — report it anyway. We'll classify it.

## What counts as a vulnerability

Anything that violates one of the five falsifiable properties in the design doc:

| ID | Property | Example vulnerability |
|---|---|---|
| G1 | Safety | An unauthorized document reaches the model context. |
| G2 | Fidelity | An authorized document is silently dropped by the authorization layer. |
| G3 | Performance | Not a security bug — file it as a regular issue. |
| G4 | Temporal | A revocation does not take effect for an in-flight agent session. |
| G5 | Audit | The audit log can be tampered with without detection. |

Also in scope: **input validation flaws** in the gateway API (once W3 lands), **cryptographic weaknesses** in the audit-log hash chain (once W3.4 lands), and any way to make the pre-filter *less* permissive than the true authorized set (which breaks fidelity silently).

## What is NOT a security vulnerability

- **Performance regressions.** Please file a regular issue.
- **Cache staleness that a downstream gate catches.** That is by design — see the "permissive superset" invariant in the README and design doc.
- **A missing feature.** Please file a regular feature request.
- **Vulnerabilities in a dependency** unless they are exploitable through Warden's API in a way the dependency's own advisory doesn't cover.

If you're not sure, err on the side of reporting privately. We'd rather triage a false alarm than see a real one on a public issue tracker.

---

## What happens next

1. **Acknowledgement** within 3 business days.
2. **Severity + timeline assessment** within 7 business days.
3. **Coordinated disclosure.** We'll agree on a fix window (typically 30-90 days depending on severity), publish a security advisory, and credit you in the release notes unless you prefer to remain anonymous.
4. **A regression test.** Every fix ships with a scenario in the adversarial suite (or a new one) that would have caught the bug. That way it stays fixed.

Warden is early-stage software. We do not yet have a bug bounty program.

---

## Scope

This policy covers:

- The `warden` package under this repository (`core/`, `evals/`, `retrieval/`, `gateway/`, `backends/`, `agent/`).
- The demo app under `demo/` (once shipped in W5).
- The reference Docker Compose stack (once shipped in W5).

It does not cover:

- Bugs in third-party dependencies (report those upstream, but tell us if they're exploitable through us).
- Bugs in your own downstream integrations of Warden.

---

## Design-level clarifications

Two things people occasionally report as bugs that are actually intended behavior. Reading these first may save a round trip:

1. **A stale pre-filter cache lets an unauthorized doc into the top-K candidate set.** This is fine and by design. Gate 2 (the authoritative check) catches it before anything reaches the model. If you can show a case where Gate 2 *doesn't* catch it, that IS a vulnerability — please report it.

2. **A revoked doc remains searchable via the index for a short window.** Also by design, for the same reason. If you can show a revoked doc reaching the model (through any gate, at any time), that's a vulnerability — please report it.

The security boundary is Gate 2, not the index. See the README's "two invariants" section for the full argument.

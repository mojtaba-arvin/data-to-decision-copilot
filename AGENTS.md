# Agent Instructions

These instructions apply to automated agents and contributors working in this repository. Read the relevant architecture, security, and contribution documents before changing behavior. Work only on a clearly scoped GitHub Issue and preserve existing repository policies.

## GitHub Issue title convention

Every Issue title must use `<type>(<scope>): <short, clear description>`. Use one lowercase Conventional Commit type: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `perf`, or `revert`. Scope must be concise lowercase kebab-case and identify the affected domain/component. Keep priority, milestone, feature identifiers, phase, agent identity, dates, and run metadata in labels, milestones, or the Issue body—not the title. Do not use bracketed prefixes, standalone priority codes, `Recover:`, or agent/supervisor identity prefixes. Use `chore(roadmap): ...` for milestone bookkeeping and `chore(supervisor): ...` for run ledgers. Preserve all Issue metadata and relationships when renaming; change only the title. This repository keeps the complete title convention here so contributors do not depend on external/private policy repositories.

## Commit identity and attribution

Read `COMMIT_IDENTITY.md` before creating, amending, or reviewing commits. Maintainer-directed AI-assisted work must use the verified maintainer author identity; preserve real human contributor identities; do not use AI/bot authors or bot co-author trailers. Follow the documented, narrowly approved genuine-automation exception. Do not rewrite historical commits. Repository-specific Git, workflow, review, and merge restrictions in this file remain authoritative.

## Evidence-gated PR review and merge

The implementer may perform and document a complete self-review and merge their own PR when all required status checks pass on the exact head SHA, targeted and relevant regression tests pass, the complete diff and security/data-integrity implications are recorded, material review threads are resolved, and the branch is up to date and mergeable. A formal GitHub approval from the PR author is not possible and is not required by repository policy; never fabricate an approval or describe self-review as independent peer review. Independent specialist review may be requested for high-risk changes but is not a universal merge gate. Never bypass branch protection, rulesets, required checks, or merge queues. Merge does not imply feature acceptance, release readiness, multi-tenant safety, or external deployment acceptance.

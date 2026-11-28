# Evidence-gated PR review and merge

The PR author/implementer may review and merge their own code. Self-review is a recorded engineering activity, not an independent peer approval.

## Merge prerequisites

Record all of the following against the exact PR head SHA:
1. Intended base branch and merge-base freshness.
2. Complete diff review, including dependencies, workflow permissions, security boundaries, migrations, and generated artifacts.
3. Issue acceptance criteria and the exact commands/tests used to verify them.
4. Targeted tests and the full relevant regression suite.
5. Exact-head required GitHub checks all passing.
6. Review threads resolved, or each remaining item explicitly documented as an accepted limitation with rationale.
7. No secrets, production data, local databases, model blobs, or generated artifacts accidentally included.
8. Remaining risks and external acceptance gates.

The implementer may write the review record. A formal GitHub APPROVE review from the PR author is unavailable and must not be simulated. Independent review can add value for high-risk changes, but is not a universal merge prerequisite.

## Merge and acceptance are separate

The implementer may merge a clean, evidence-complete PR if GitHub reports it mergeable. Never bypass required reviews/checks, branch protection, rulesets, or merge queues. If a repository rule still requires a second approver solely because of reviewer identity, update that administrative rule through an authorized owner; do not fabricate an approval.

Merge does not close an Issue, mark a feature accepted, enable autonomous recommendations, establish semantic quality, or prove deployment readiness. Feature acceptance requires its own criteria and evidence. In particular, keep the single-tenant boundary, hostile-catalog sandboxing, semantic-verifier release gate, and customer/production evidence requirements unchanged.

## Release boundary

Release publication and deployment remain separate from PR merge. Preserve the release checklist, artifact provenance, exact merged SHA, security controls, and any owner/external acceptance decisions.

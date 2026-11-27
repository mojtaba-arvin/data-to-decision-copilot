# Commit identity and attribution policy

For maintainer-directed AI-assisted implementation, use Git author `Mojtaba Arvin <m.arvin@live.com>` only after confirming this exact email is verified on the maintainer's GitHub account. Do not use model, assistant, AI-agent, Copilot, Claude, Cursor, Codex, Gemini, generic agent, or bot identities as the author. Do not add bot/AI `Co-authored-by` trailers.

Include `<!-- maintainer-directed-ai-work -->` in the PR body for owner-authorized AI-assisted work. This machine-readable marker requires at least one maintainer-authored commit and validates every commit attributed to `mojtaba-arvin` against the exact maintainer name/email; other human contributor identities remain unchanged. Disclose AI assistance in the PR description; the marker does not claim the maintainer personally typed every line.

Preserve every real human contributor's original author name/email. Never relabel another person as the maintainer and never rewrite historical commits.

Genuine GitHub Actions/Dependabot automation may author a commit only with the explicit `allow-automation-commits` label and matching GitHub-mapped allowlisted bot identities for both author and committer, including their exact GitHub noreply email addresses. The exception does not cover agent-written implementation or a human-authored commit merely committed by a bot. Bot co-author trailers remain prohibited. A `web-flow` committer is distinct from the commit author and is accepted only with GitHub's `GitHub <noreply@github.com>` committer metadata.

Convenience defaults (not enforcement):
```sh
git config --global user.name "Mojtaba Arvin"
git config --global user.email "m.arvin@live.com"
```

`scripts/check_commit_identity.py` checks all PR commits, validates both Git author and committer metadata separately, rejects bot/agent authors, bot/agent committers (except the narrowly defined automation and `web-flow` cases), and bot co-author trailers, and enforces the maintainer identity when the marker is present. External human author fields are retained; the validator does not rewrite commit history. The `Commit identity` workflow uses read-only GitHub permissions and checks out only trusted base-branch code. Its automation allowlist verifies GitHub account mapping and exact bot metadata, but Git metadata and a label cannot cryptographically prove which process created a commit; inspect provenance when approving an automation exception. Configure this check as required in branch protection/rulesets where governance permits; a workflow alone does not block merges.

Git metadata is self-asserted: validation cannot prove who physically typed code or detect every disguised agent identity. Verify the email association and disclose AI assistance transparently.

Never amend, rebase, or cherry-pick a contributor's work under the maintainer identity. Preserve the contributor's original author fields and add a separate maintainer-authored commit for AI-assisted changes.

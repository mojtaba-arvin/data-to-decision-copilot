#!/usr/bin/env python3
"""Validate GitHub pull-request commit author and committer attribution."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

NAME = "Mojtaba Arvin"
EMAIL = "m.arvin@live.com"
LOGIN = "mojtaba-arvin"
MARKER = "<!-- maintainer-directed-ai-work -->"
AUTO_LABEL = "allow-automation-commits"
AUTOMATION = {
    "github-actions[bot]": {
        "name": "github-actions[bot]",
        "email": "41898282+github-actions[bot]@users.noreply.github.com",
    },
    "dependabot[bot]": {
        "name": "dependabot[bot]",
        "email": "49699333+dependabot[bot]@users.noreply.github.com",
    },
}
BOT_MARKERS = (
    "[bot]", "ai agent", "ai-agent", "agent bot", "copilot", "claude",
    "cursor", "windsurf", "codex", "chatgpt", "gemini", "openai", "gpt",
    "ai assistant", "assistant bot", "dependabot", "github-actions",
    "renovate", "aider", "codeium", "tabnine", "devin", "sourcegraph cody",
    "amazon q", "replit agent",
)


def is_bot(name, email, login=None, account_type=None):
    """Recognize GitHub bot accounts and common agent identity markers."""
    if (account_type or "").lower() == "bot":
        return True
    if login and login.lower().endswith("[bot]"):
        return True
    value = f"{name} {email} {login or ''}".lower()
    tokens = value.replace("@", " ").replace("+", " ").replace(".", " ")
    tokens = tokens.replace("_", " ").replace("-", " ").split()
    return bool({"bot", "agent", "assistant"} & set(tokens)) or any(
        marker in value for marker in BOT_MARKERS
    )


def genuine_automation(item, allow_auto):
    """Require the same allowlisted GitHub bot as mapped author and committer."""
    if not allow_auto:
        return False
    author = item.get("author") or {}
    committer = item.get("committer") or {}
    login = author.get("login")
    expected = AUTOMATION.get(login)
    if not expected or committer.get("login") != login:
        return False
    if author.get("type") != "Bot" or committer.get("type") != "Bot":
        return False
    for role in ("author", "committer"):
        metadata = (item.get("commit") or {}).get(role) or {}
        if metadata.get("name") != expected["name"]:
            return False
        if (metadata.get("email") or "").lower() != expected["email"]:
            return False
    return True


def web_flow_committer(item):
    """Allow GitHub's distinct web-flow committer without treating it as author."""
    account = item.get("committer") or {}
    metadata = (item.get("commit") or {}).get("committer") or {}
    return (
        account.get("login") == "web-flow"
        and account.get("type") == "User"
        and metadata.get("name") == "GitHub"
        and (metadata.get("email") or "").lower() == "noreply@github.com"
    )


def validate(pr, commits):
    errors = []
    labels = {label.get("name", "") for label in pr.get("labels", [])}
    allow_auto = AUTO_LABEL in labels
    maintainer_work = MARKER in (pr.get("body") or "")
    maintainer_commit_seen = False

    for item in commits:
        commit = item.get("commit") or {}
        author = item.get("author") or {}
        committer = item.get("committer") or {}
        author_data = commit.get("author") or {}
        committer_data = commit.get("committer") or {}
        author_name = author_data.get("name") or ""
        author_email = author_data.get("email") or ""
        committer_name = committer_data.get("name") or ""
        committer_email = committer_data.get("email") or ""
        author_login = author.get("login")
        committer_login = committer.get("login")
        sha = (item.get("sha") or "unknown")[:12]
        auto_commit = genuine_automation(item, allow_auto)

        if not author_name or not author_email:
            errors.append(f"{sha}: missing commit author name/email")
        if not committer_name or not committer_email:
            errors.append(f"{sha}: missing commit committer name/email")

        if is_bot(author_name, author_email, author_login, author.get("type")) and not auto_commit:
            errors.append(
                f"{sha}: bot/agent author rejected: "
                f"{author_name!r} <{author_email}> login={author_login!r}"
            )
        if is_bot(
            committer_name, committer_email, committer_login, committer.get("type")
        ) and not auto_commit:
            errors.append(
                f"{sha}: bot/agent committer rejected: "
                f"{committer_name!r} <{committer_email}> "
                f"login={committer_login!r}"
            )

        if author_login == "web-flow":
            errors.append(f"{sha}: web-flow is a committer identity, not an author")

        if author_login == LOGIN:
            maintainer_commit_seen = True
            if author_name != NAME or author_email.lower() != EMAIL.lower():
                errors.append(
                    f"{sha}: maintainer author must use {NAME} <{EMAIL}>, "
                    f"got {author_name!r} <{author_email}>"
                )
        elif author_email.lower() == EMAIL.lower():
            errors.append(
                f"{sha}: maintainer email must resolve to {LOGIN}, "
                f"not {author_login!r}"
            )

        if committer_login == LOGIN:
            if committer_name != NAME or committer_email.lower() != EMAIL.lower():
                errors.append(
                    f"{sha}: maintainer committer must use {NAME} <{EMAIL}>, "
                    f"got {committer_name!r} <{committer_email}>"
                )
        elif committer_email.lower() == EMAIL.lower():
            errors.append(
                f"{sha}: maintainer email must resolve to {LOGIN} as committer, "
                f"not {committer_login!r}"
            )

        if committer_login == "web-flow" and not web_flow_committer(item):
            errors.append(f"{sha}: unexpected web-flow committer metadata")

        for line in (commit.get("message") or "").splitlines():
            if line.strip().lower().startswith("co-authored-by:"):
                identity = line.split(":", 1)[1].strip()
                if is_bot(identity, ""):
                    errors.append(
                        f"{sha}: bot/AI Co-authored-by trailer rejected: {identity!r}"
                    )

    if maintainer_work and not maintainer_commit_seen:
        errors.append(
            "maintainer-directed AI work marker requires at least one commit "
            f"authored by {LOGIN}"
        )
    return errors


def get_json(url, token):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "commit-identity-policy-check",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def self_test():
    def item(
        author_name, author_email, author_login, author_type,
        committer_name=None, committer_email=None, committer_login=None,
        committer_type=None, message="feat: test",
    ):
        return {
            "sha": "a" * 40,
            "commit": {
                "author": {"name": author_name, "email": author_email},
                "committer": {
                    "name": committer_name or author_name,
                    "email": committer_email or author_email,
                },
                "message": message,
            },
            "author": {"login": author_login, "type": author_type},
            "committer": {
                "login": committer_login or author_login,
                "type": committer_type or author_type,
            },
        }

    def check(pr, commits, expected):
        actual = validate(pr, commits)
        assert bool(actual) is expected, actual

    empty = {"body": "", "labels": []}
    marked = {"body": MARKER, "labels": []}
    check(empty, [item(NAME, EMAIL, LOGIN, "User")], False)
    check(
        marked,
        [item(
            NAME, EMAIL, LOGIN, "User",
            "GitHub", "noreply@github.com", "web-flow", "User",
        )],
        False,
    )
    check(
        empty,
        [item(
            "External Human", "human@example.org", "external-human", "User",
            "GitHub", "noreply@github.com", "web-flow", "User",
        )],
        False,
    )
    check(empty, [item("Copilot", "bot@example.org", "copilot[bot]", "Bot")], True)
    check(empty, [item("Release Bot", "release@example.org", "release-bot", "User")], True)
    check(empty, [item("GitHub", "noreply@github.com", "web-flow", "User")], True)
    check(
        empty,
        [item(
            "Human", "human@example.org", "human", "User",
            "dependabot[bot]", AUTOMATION["dependabot[bot]"]["email"],
            "dependabot[bot]", "Bot",
        )],
        True,
    )
    check(
        empty,
        [item(
            "github-actions[bot]", AUTOMATION["github-actions[bot]"]["email"],
            "github-actions[bot]", "Bot",
        )],
        True,
    )
    dependabot = item(
        "dependabot[bot]", AUTOMATION["dependabot[bot]"]["email"],
        "dependabot[bot]", "Bot",
    )
    check(empty, [dependabot], True)
    check(
        {"body": "", "labels": [{"name": AUTO_LABEL}]},
        [dependabot],
        False,
    )
    automation = item(
        "github-actions[bot]", AUTOMATION["github-actions[bot]"]["email"],
        "github-actions[bot]", "Bot",
    )
    check(empty, [automation], True)
    check(
        {"body": "", "labels": [{"name": AUTO_LABEL}]},
        [automation],
        False,
    )
    automation["committer"]["login"] = "mojtaba-arvin"
    check(
        {"body": "", "labels": [{"name": AUTO_LABEL}]},
        [automation],
        True,
    )
    automation["committer"]["login"] = "github-actions[bot]"
    check(
        {"body": "", "labels": [{"name": AUTO_LABEL}]},
        [automation],
        False,
    )
    message = (
        "feat: test\n"
        " Co-authored-by: Dependabot <dependabot[bot]@users.noreply.github.com>"
    )
    check(
        empty,
        [item("Human", "human@example.org", "human", "User", message=message)],
        True,
    )
    check(
        marked,
        [item("External Human", "human@example.org", "human", "User")],
        True,
    )
    check(
        empty,
        [item(NAME, "wrong@example.org", LOGIN, "User")],
        True,
    )
    print("PASS: commit identity and attribution self-tests")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument(
        "--pr-number", type=int, default=int(os.getenv("PR_NUMBER", "0"))
    )
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
    if not (args.repository and args.pr_number and args.token):
        if args.self_test and not args.pr_number:
            return 0
        parser.error("--repository, --pr-number and --token are required")

    base = "https://api.github.com/repos/" + args.repository
    pr = get_json(base + "/pulls/" + str(args.pr_number), args.token)
    commits = []
    page = 1
    while True:
        batch = get_json(
            base + f"/pulls/{args.pr_number}/commits?per_page=100&page={page}",
            args.token,
        )
        if not isinstance(batch, list):
            raise RuntimeError("GitHub API returned a non-list commit response")
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    if not commits:
        print("ERROR: empty PR commit list; refusing to pass")
        return 2

    errors = validate(pr, commits)
    print(f"Checked {len(commits)} commits in {args.repository}#{args.pr_number}")
    if errors:
        print("\n".join("ERROR: " + error for error in errors))
        return 1
    print("PASS: no prohibited bot/agent authors, committers, or co-author trailers")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as error:
        print(
            f"ERROR: GitHub API HTTP {error.code}: {error.reason}",
            file=sys.stderr,
        )
        sys.exit(2)
    except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as error:
        print(f"ERROR: identity validation incomplete: {error}", file=sys.stderr)
        sys.exit(2)

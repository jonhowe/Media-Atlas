# Repository Instructions

## GitHub authentication

The connected GitHub app and the repository's SSH remote are valid authentication routes. A stale
or invalid result from `gh auth status` alone must not trigger a request for the user to
re-authenticate. Use the connected GitHub app for supported GitHub API operations, the SSH remote
for Git fetches and pushes, and an authenticated browser session for unsupported workflow or
settings operations. Ask the user about authentication only after an operation required for the
task actually fails across the available authentication routes.

## GitHub publication with Release Automator

After completing a task that changes repository files, offer to publish the completed work through
the repository's Release Automator workflows. Do not offer after read-only work, and do not repeat
the offer for a change set the user has already declined to publish.

Release Automator is installed through these manual GitHub Actions workflows:

- `release-automator-plan.yml` creates a frozen, reviewable publication plan and artifact.
- `release-automator-execute.yml` executes only an explicitly approved 64-character plan ID.
- `release-automator-resume.yml` resumes a failed approved run without repeating completed work.

The workflows pin `jonhowe/Release-Automator` to the immutable commit for Marketplace release
`v0.4.0`. Repository policy is defined in `release-automator.toml`.

### Required repository setup

Before using the workflows, verify all of the following. If any item is missing, stop and report the
prerequisite instead of falling back to a manual release:

- `OPENAI_API_KEY` exists as a repository Actions secret.
- A protected GitHub environment named `release` exists with an appropriate required-reviewer
  policy. Solo maintainers must not enable self-review prevention unless another reviewer can
  approve the deployment.
- `RELEASE_AUTOMATOR_GITHUB_TOKEN` exists only as a secret on the `release` environment. Prefer a
  fine-grained token scoped to this repository with Contents read/write and Pull requests
  read/write. Add Workflows read/write only when the approved changes include `.github/workflows/`.
  The token does not need Checks or Commit statuses permissions; execution and resume workflows
  read those through the restricted built-in `GITHUB_TOKEN`.

Never place either secret in workflow inputs, repository variables, TOML, artifacts, logs, plan
summaries, or command arguments.

### Prepare a source snapshot

The planning workflow runs on GitHub and therefore requires the proposed changes to exist in a
full commit SHA reachable from GitHub. Before creating or switching branches, staging, committing,
or pushing that source snapshot:

1. Inspect the working tree and identify the exact task files to include and every changed file to
   exclude. Never include unrelated changes silently.
2. Run relevant local validation and identify any applicable `docs/RELEASE_CHECKLIST.md` items
   that remain unverified.
3. Present one source-snapshot proposal with the exact included and excluded files, validation
   results, temporary branch `release-source/<concise-kebab-case-task-slug>`, and exact commit
   message. State that this branch only makes the proposed diff available to the planning workflow
   and is not the final pull-request branch.
4. Ask one direct approval question. A general request to push, publish, or release does not replace
   approval of this exact source snapshot.

After approval, create the temporary branch from the current `main` commit, stage only the approved
files, commit, push it without force, and record the full commit SHA. Do not open a pull request from
the temporary branch.

### Plan, approve, and execute

1. Dispatch **Plan a release** with the full source commit SHA, newline-delimited approved paths,
   `release-automator.toml`, and the intended release mode. Releases are enabled by default; use
   `no_release` only when the user explicitly requests a merge without a GitHub Release. Use
   `no_latest` only when the user explicitly wants a stable release that does not replace the
   repository's current latest release; prereleases are never marked latest.
2. Wait for planning to succeed. Read the complete job summary and report the planning run ID, the
   exact 64-character plan ID, included and excluded files, validations, branch and commit, full
   pull-request title and body, required checks, merge and cleanup behavior, release tag/channel,
   release notes, and the GHCR side effect.
3. End with one direct approval question for that exact plan ID. If any material detail changes,
   dispatch a fresh plan and present it in full before asking again.
4. After approval, dispatch **Execute an approved release plan** with the planning run ID and exact
   plan ID. The protected `release` environment is the final credential boundary. Do not bypass its
   reviewers or checks.
5. Let Release Automator create the final `agent/` branch, commit, push, ready-for-review pull
   request, required-check wait, squash merge, branch cleanup, and GitHub Release. Never duplicate
   those operations manually, force a merge, overwrite a branch, or replace an existing tag.
6. After successful execution, delete the temporary `release-source/` branch locally and remotely
   only if it still points to the approved source commit.

If execution fails after approval, inspect the saved state and use **Resume a release plan** with
the planning run ID, failed execution/resume run ID, and the same full plan ID. If the plan is stale,
conflicted, blocked by checks, or otherwise must change, stop and create a new plan rather than
bypassing the safety boundary.

Report the source commit, planning run, plan ID, pull request, final commit, merge commit, release,
validation results, and any available downstream workflow status. Publishing a GitHub Release
triggers `.github/workflows/publish-ghcr.yml`, which publishes the release tag, `latest`, and a
commit-pinned `sha-<commit>` image tag.

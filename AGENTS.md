# Repository Instructions

## GitHub publication approval workflow

After completing any task that changes files in this repository, offer to publish the completed work to GitHub. Do not make this offer after a read-only task, and do not repeat it for a change set the user has already declined to publish.

Before creating or switching branches, staging files, committing, pushing, creating or merging a pull request, or creating a release, inspect the repository and present one complete publication proposal to the user. The proposal must include:

- the files and changes that will be included, plus any working-tree changes that will be excluded;
- validation already completed, its results, and any applicable items from `docs/RELEASE_CHECKLIST.md` that remain unverified;
- the exact new branch name, using `agent/<concise-kebab-case-task-slug>`, and the exact commit message;
- a ready-for-review pull request targeting `main`, including its exact title and complete body;
- the plan to wait for required checks, squash-merge the pull request, and delete the remote branch;
- the latest GitHub release and latest stable release, the exact proposed stable release tag with a short rationale, and the exact release title and complete release notes;
- confirmation that the release will target the pull request's squash-merge commit on `main`, will not be marked as a prerelease, and will be marked as the latest release; and
- the intended execution split: local `git` for branch/commit synchronization, the GitHub app/plugin for supported repository and pull request operations, and approved unsandboxed `gh` commands only for capabilities the plugin does not provide; and
- the repository-specific consequence that publishing a release triggers `.github/workflows/publish-ghcr.yml`, which publishes matching, `latest`, and commit-pinned GHCR image tags.

End the proposal with one direct approval question. One explicit approval authorizes the exact branch, commit, push, pull request, wait-for-checks, squash merge, branch deletion, and release sequence described in the proposal. If the user changes any part of the proposal, present the complete revised proposal before asking again. A general request to push, publish, or release does not replace this exact-proposal approval requirement.

After approval, execute this sequence without additional approval checkpoints:

1. Verify GitHub app/plugin access first. Verify `gh` authentication in an approved unsandboxed command before declaring it unavailable, because the normal sandbox may be unable to read a valid macOS Keychain credential. Confirm that the proposed branch and release tag are still available.
2. Use local `git` to create the proposed branch from the current `main` commit, preserve alignment with the shared working tree, stage only the approved files, and create the approved commit.
3. Run any relevant checks that have not already been run. Use local `git` to push the branch to `origin` with upstream tracking.
4. Prefer the GitHub app/plugin to open and inspect the approved ready-for-review pull request against `main`. Use `gh` only if the plugin lacks the required operation.
5. Prefer the GitHub app/plugin for structured pull request and commit-status inspection. Use approved unsandboxed `gh` commands for GitHub Actions checks or logs and for merge operations the plugin does not support. Wait until required checks finish; when they pass and the pull request is mergeable, squash-merge it and delete the remote branch.
6. Create the approved stable GitHub release from the squash-merge commit on `main`, using an approved unsandboxed `gh` command when the plugin has no release capability. Use the approved title and release notes and mark it as the latest release.
7. Report the branch, commit, pull request, merge commit, release, validation results, and any downstream release-workflow status that is already available.

Do not silently stage unrelated working-tree changes. When the working tree is mixed, include only files changed for the current task and identify all exclusions in the proposal.

Determine release history from live GitHub Releases immediately before presenting the proposal, not from local tags alone. Prefer a stable release. If a prerelease series newer than the latest stable release already exists, propose stabilizing that prerelease base unless the completed change requires a higher semantic-version bump. Otherwise, choose the next semantic version from the completed change: major for breaking compatibility, minor for backward-compatible functionality, and patch for fixes, documentation, CI, or internal maintenance. Verify the proposed tag again immediately before release creation.

Do not treat a failed sandboxed `gh auth status` as proof that GitHub authentication is unavailable. Check GitHub app/plugin access and retry `gh auth status` in an approved unsandboxed command, without displaying or copying the token. If both access paths are unavailable, include that prerequisite in the proposal. If required access remains unavailable after approval, required checks fail, the pull request is blocked or conflicted, the approved tag becomes unavailable, or any material proposal detail must change, stop before merge or release. Explain the blocker and present a complete revised proposal for a new approval before continuing. Never force a merge, bypass required checks, overwrite a remote branch, or replace an existing release tag.

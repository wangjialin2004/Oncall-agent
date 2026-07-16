# Git Metadata Recovery Implementation Plan

> **For agentic workers:** Execute the tasks in order and retain the checkboxes as the implementation record.

**Goal:** Restore usable Git metadata for this workspace without overwriting, deleting, or staging any working-tree files.

**Architecture:** The current `.git` file points to a missing primary worktree. A healthy sibling repository supplies the base commit. The broken pointer is backed up, this directory is initialised as an independent repository, and a mixed reset sets only the index to the sibling's current `main` commit so all current files remain in place.

**Tech Stack:** Git worktree metadata; local sibling repository at `../_oncall-agent-push-worktree`.

---

## Scope and safeguards

- In scope: replace only the invalid `.git` pointer with valid repository metadata and restore the `origin` remote.
- Out of scope: resetting the working tree, deleting files, committing changes, pushing, rebasing, or changing application source.
- Safeguard: copy the invalid pointer to `.git.broken-20260716` before any metadata replacement.
- Safeguard: use `git reset --mixed`, never `--hard`; this writes the index but leaves every workspace file untouched.

## Tasks

### Task 1: Capture recovery base

- [x] Verify the sibling repository is healthy and record its `main` commit: `9884309679e13f7e7e45de2eccd5ba5b3da1be42`.

### Task 2: Rebuild this workspace's metadata

- [x] Back up the invalid `.git` pointer to `.git.broken-20260716`.
- [x] Initialise a fresh local repository in this directory.
- [x] Fetch the sibling repository's `main` commit, configure the origin URL, and use a mixed reset to create a baseline without altering workspace files.

### Task 3: Verify preservation

- [x] Confirm `git status --short` runs successfully; it reports 252 preserved working-tree difference entries.
- [x] Confirm the current branch is `main`, `HEAD` and `origin/main` are `9884309679e13f7e7e45de2eccd5ba5b3da1be42`, the origin URL is `https://github.com/wangjialin2004/Oncall-agent.git`, and both pointer backups remain available for audit.
- [x] Record the outcome and update the plan index.

## Verification

```powershell
git status --short
git branch --show-current
git remote -v
Test-Path .git.broken-20260716
```

Expected: status returns normally, branch is `main`, both origin URLs are present, and the backup pointer exists. A non-empty status is expected because current workspace files are deliberately preserved.

## Rollback

If repository initialisation or baseline setup fails, remove the newly created `.git` directory and rename `.git.broken-20260716` back to `.git`. This restores the prior metadata pointer without touching project files.

## Outcome — completed 2026-07-16

The user removed the explicit deny ACL entries and renamed the invalid pointer without deleting it. A fresh local Git directory was created, then initialised from the healthy sibling repository's shallow `main` history using `git fetch --update-shallow` and `git reset --mixed FETCH_HEAD`. The mixed reset updated only Git metadata and the index; it preserved 252 existing working-tree difference entries. The original pointer backup and renamed pointer remain in place for audit.

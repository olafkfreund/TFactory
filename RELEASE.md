# Release Process

This document describes how releases are created for TFactory.

## Overview

TFactory uses a simplified release process with version bumping and changelog management.

```
┌─────────────────────────────────────────────────────────────────┐
│                        RELEASE FLOW                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   develop branch                    main branch                  │
│   ──────────────                    ───────────                  │
│        │                                 │                       │
│        │  1. bump-version.js             │                       │
│        │     (creates commit)            │                       │
│        │                                 │                       │
│        ▼                                 │                       │
│   ┌─────────┐                           │                       │
│   │ v1.1.0  │  2. Create PR             │                       │
│   │ commit  │ ────────────────────►     │                       │
│   └─────────┘                           │                       │
│                                          │                       │
│                           3. Merge PR    ▼                       │
│                                    ┌──────────┐                  │
│                                    │ v1.1.0   │                  │
│                                    │ on main  │                  │
│                                    └────┬─────┘                  │
│                                         │                        │
│                           4. Create tag & release                │
│                                         ▼                        │
│                                    ┌──────────┐                  │
│                                    │ v1.1.0   │                  │
│                                    │ release  │                  │
│                                    └──────────┘                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Creating a Release

### Step 1: Bump the Version

On your development branch:

```bash
# Navigate to project root
cd TFactory

# Bump version (choose one)
node scripts/bump-version.js patch   # 1.0.0 -> 1.0.1 (bug fixes)
node scripts/bump-version.js minor   # 1.0.0 -> 1.1.0 (new features)
node scripts/bump-version.js major   # 1.0.0 -> 2.0.0 (breaking changes)
node scripts/bump-version.js 1.2.0   # Set specific version
```

This will:
- Update `apps/frontend-web/package.json`
- Update `package.json` (root)
- Update `apps/backend/__init__.py`
- Create a commit with message `chore: bump version to X.Y.Z`

### Step 2: Update CHANGELOG.md

Add release notes to `CHANGELOG.md`:

```markdown
## 1.1.0 - Release Title

### New Features
- Feature description

### Improvements
- Improvement description

### Bug Fixes
- Fix description

---
```

Then amend the version bump commit:

```bash
git add CHANGELOG.md
git commit --amend --no-edit
```

### Step 3: PR to `dev`, then promote to `main`

```bash
git push origin your-branch
gh pr create --base dev --title "Release v1.1.0"
```

Feature work lands on `dev`; `main` only receives promotion merges from `dev`
(see CONTRIBUTING.md).

### Step 4: Merge to `main` (tag and release are automatic)

Do not tag by hand. When the root `package.json` version changes on `main`,
`.github/workflows/release.yml` requires a `## <version>` entry in
`CHANGELOG.md`, an unused tag and non-empty notes, then creates the annotated tag
`v<version>` and the GitHub release, builds and cosign-signs the images and
attaches SBOMs. A push to `main` also runs `deploy.yml` (see the
`tfactory-deploy-runbook` skill in `.claude/skills/` for verify and rollback).

---

## Version Numbering

We follow [Semantic Versioning](https://semver.org/):

| Type | Format | When to Use | Example |
|------|--------|-------------|---------|
| **MAJOR** | X.0.0 | Breaking changes, incompatible API changes | 1.0.0 → 2.0.0 |
| **MINOR** | 0.X.0 | New features, backwards compatible | 1.0.0 → 1.1.0 |
| **PATCH** | 0.0.X | Bug fixes, backwards compatible | 1.0.0 → 1.0.1 |

---

## Changelog Format

Each version entry in `CHANGELOG.md` should follow this format:

```markdown
## X.Y.Z - Release Title

### New Features
- Feature description with context

### Improvements
- Improvement description

### Bug Fixes
- Fix description

### Breaking Changes (if any)
- Description of breaking changes

---
```

### Writing Good Release Notes

- **Be specific**: Instead of "Fixed bug", write "Fixed crash when opening large files"
- **Group by impact**: Features first, then improvements, then fixes
- **Credit contributors**: Mention contributors for significant changes
- **Link issues**: Reference GitHub issues where relevant (e.g., "Fixes #123")

---

## Quick Reference

```bash
node scripts/bump-version.js minor
# Edit CHANGELOG.md
git add CHANGELOG.md
git commit --amend --no-edit
git push origin your-branch
gh pr create --base dev --title "Release v1.1.0"
# After promotion to main, release.yml tags and publishes; nothing else to run.
```

---

## Files Updated by Version Bump

| File | Field |
|------|-------|
| `package.json` | `version` |
| `apps/frontend-web/package.json` | `version` |
| `apps/backend/__init__.py` | `__version__` |

---

## Troubleshooting

### Version mismatch between files

Run the bump script again - it updates all files atomically:

```bash
node scripts/bump-version.js 1.1.0
```

### Tag already exists

`release.yml` fails with "Tag v<version> already exists on origin". Bump the
version again (`node scripts/bump-version.js <new-version>`); only delete the
remote tag (`git push origin :refs/tags/v<version>`) if it was created in error,
and then a new version change on `main` is needed to re-run the release.

### Need to update release notes after publishing

Edit the release on GitHub directly, or:

```bash
gh release edit v1.1.0 --notes "Updated release notes"
```

---

## Resources

- [GitHub Releases](https://github.com/olafkfreund/TFactory/releases)
- [Semantic Versioning](https://semver.org/)
- [GitHub CLI Documentation](https://cli.github.com/manual/)

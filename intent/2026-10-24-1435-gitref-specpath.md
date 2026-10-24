---
status: approved
issue: 1435
author: olafkfreund
---

# Intent: Move the git ref and date validators into _specpath to break the import cycle

## Problem

#1429 made `routes/files.py` import `_require_safe_git_ref` from
`routes/changelog.py`. That starts an import cycle (CodeQL 1911,
`py/cyclic-import`, dismissed as won't-fix so 0.9.41 could ship). There
are also two copies of `_require_safe_git_ref`, in `git.py` and
`changelog.py`, with slightly different regexes.

## Proposed outcome

One copy of `_require_safe_git_ref` and `_require_safe_git_date` in the
leaf module `routes/_specpath.py`. `changelog.py`, `git.py` and
`files.py` import it from there. No cycle, and CodeQL reports no
cyclic-import at `files.py`.

## Affected users and systems

TFactory web-server routes `changelog.py`, `git.py`, `files.py`,
`_specpath.py`. No API change, apart from the regex note in Constraints.

## Constraints

- A pure move. The existing tests pass unchanged.
- `CommandInjectionSanitized.ql` matches by name and needs no logic change.
- No release.

## Open questions

None. The two regexes differ only in `+`. The shared one keeps
`git.py`'s `[\w./@+-]+` (decision in the spec).

# Project conventions

## Language

**Write English in documentation, pull requests, and code comments.**

| Artifact | Language |
|----------|----------|
| Code comments and docstrings | English |
| Documentation (`README.md`, design notes, anything under `docs/`) | English |
| Pull request titles and descriptions | English |
| Identifiers, log messages, error strings | English |

Two deliberate exceptions:

- **User-facing UI copy stays Traditional Chinese.** Labels, buttons, banners,
  and validation messages the end user reads are product content, not
  engineering communication. `密碼至少要 8 個字元` is correct as written.
- **`README.md` is currently Traditional Chinese.** Everything added to it from
  now on should be English; converting the existing body is a separate task
  that has not been scheduled. Do not translate it as a side effect of an
  unrelated change.

The reason for the rule is reach, not preference: comments and PR descriptions
are read by people and tools that do not share a first language, and a mixed
codebase forces every reader to switch context mid-file.

## Comment style

The existing code explains **why**, not what — see `server/app/deps.py` or
`server/app/services/auth.py` for the register. Match it. A comment that
restates the line below it is worse than no comment; a comment that records the
constraint which forced the code into its current shape is what stops the next
person from "simplifying" it back into a bug.

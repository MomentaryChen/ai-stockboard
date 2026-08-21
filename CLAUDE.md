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

- **User-facing UI copy is product content, not engineering communication**, and
  lives in the frontend message catalogue rather than in a component. Add the key
  to `frontend/src/i18n/locales/zh-TW.ts` -- Traditional Chinese is still the
  source of truth -- and `tsc -b` will fail until `en.ts` covers it too. A string
  literal like `密碼至少要 8 個字元` inline in a `.tsx` file is now a bug: it is
  invisible to the English locale. See the i18n section of README.md.

  The same applies to the Chinese product copy the *server* owns on purpose (the
  Best Four Point reasons, the job registry's names and stat labels): those stay
  Chinese in Python and are translated by lookup in `i18n/serverText.ts`.
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

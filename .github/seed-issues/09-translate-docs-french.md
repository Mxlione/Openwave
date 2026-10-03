---
title: "Translate the documentation into French"
labels: ["good first issue", "help wanted", "documentation", "translation"]
milestone: "Backlog"
---

The README exists in French, as [`README.fr.md`](../../README.fr.md). The ten pages under
[`docs/`](../../docs/) do not.

**What to do**

- Translate a page at a time. One page per pull request is better than ten in one: it reviews
  faster and it means a half-finished effort still helps somebody.
- Start with [`installing.md`](../../docs/installing.md) and
  [`using.md`](../../docs/using.md), which are what a newcomer reads.
- Keep technical terms that have no settled French form in English rather than inventing one.
  *Waterfall* and *multiplex* are understood; *cascade* and *bouquet* are guesses.
- Leave the code, the option names and the output exactly as they are. Only the prose is
  translated.

**Where it goes:** this needs a decision before the first page. Either `docs/fr/using.md` with
MkDocs' `i18n` plugin, or `docs/using.fr.md` with no plugin and links between the two. Say which
you propose in the issue before writing much; either is fine, but the project should not end up
with both.

**Done when**

- The page reads as French written by a person, not as English with French words.
- `mkdocs build --strict` passes, which means the navigation and every link still resolve.

Other languages are welcome on the same terms.

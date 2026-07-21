# Codebase Wiki

- Every project keeps an `openwiki/` folder of generated codebase
  documentation, produced by OpenWiki — an LLM-powered tool that writes
  structured Markdown describing the code (module boundaries, architecture,
  workflows). Read the relevant wiki pages for context at the start of a
  task instead of re-deriving how the codebase fits together from scratch.
- `openwiki/` is **generated output — never hand-edit it.** The source of
  truth is always the code. If a wiki page is wrong, the fix is in the code
  (or the code's own comments/docstrings), followed by regenerating the
  wiki — not an edit to the Markdown, which the next regeneration would
  silently overwrite.
- **Regenerate before committing.** When a change alters code that the wiki
  describes, regenerate the wiki with `openwiki code --update` and stage the
  updated `openwiki/` alongside that change, so the wiki lands in the same
  pull request and never drifts from `main`. Treat a stale `openwiki/` the
  same as a stale test — part of the change isn't done until it's updated.
- Regenerating calls a paid LLM provider and needs one-time setup
  (`npm install -g openwiki`, then `openwiki auth <provider>`). Install and
  auth instructions live in the project `README`; `openwiki` is a per-machine
  global CLI, **not** a project dependency, and must never be added to
  `pyproject.toml`.
- Regeneration is also automated in CI: `.github/workflows/openwiki-update.yml`
  runs `openwiki code --update` on a daily schedule (and on `workflow_dispatch`),
  then opens a `docs: update OpenWiki` PR whenever the regenerated `openwiki/`
  differs from `main`. It calls a **paid** LLM provider, so it is deliberately
  not run on every push. This safety net does not replace the rule above:
  regenerate and stage `openwiki/` alongside the code change that touches it, so
  the wiki lands in the same PR rather than drifting until the nightly job
  catches up.

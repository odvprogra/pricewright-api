## What

<!-- One or two sentences: what changes. -->

## Why

<!-- The problem, issue or milestone this serves. Link it: "Closes #123". -->

## How it was tested

<!-- Commands run, tests added at each level (unit / integration / e2e), manual checks. -->

## Screenshots

<!-- UI changes only: before and after. Delete this section otherwise. -->

## Decisions

<!-- ADR added or affected: docs/adr/NNNN-title.md. Delete this section if none. -->

## Definition of Done

- [ ] `just check` passes locally
- [ ] Tests at the right level for the change
- [ ] No new lint or type ignores without an inline justification
- [ ] OpenAPI and the generated client updated (if the API changed)
- [ ] Migrations included and reversible (if the database changed)
- [ ] Logs structured, with no secrets or PII
- [ ] README / ADR updated (if behavior or a decision changed)
- [ ] UI: four states, axe and keyboard checks, Storybook stories (if the UI changed)

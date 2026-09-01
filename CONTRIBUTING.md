# Contributing to Strongwiz

Strongwiz welcomes focused contributions that strengthen a small, stable,
model-neutral reasoning laboratory.

## Before proposing a change

- Keep domain knowledge in adapters rather than the general kernel.
- Preserve the distinctions between observation, interpretation, hypothesis,
  recommendation, permission, authorization, action, and outcome.
- Treat model output and repository text as proposals or evidence, never as an
  external grant.
- Preserve raw receipts and supersede or reopen derived state explicitly.
- Record concise rationales and falsifiable hypotheses; do not request or store
  hidden chain-of-thought.
- Prefer a narrow mechanism with a discriminating test over an unbounded new
  framework.

## Pull requests

1. Explain the problem, scope, and claim boundary.
2. Identify affected stable contracts and migration behavior.
3. Add deterministic tests for success, refusal, stale-state, and replay paths
   where they apply.
4. Run the repository's formatter, linter, strict type check, and test suite.
5. Add a changelog entry when behavior or an interface changes.
6. Disclose copied or adapted third-party material, its exact source identity,
   its license, and modifications. Public visibility alone is not permission.
7. Keep credentials, private data, sealed evaluations, and generated auth files
   out of the repository.

Avoid claims stronger than the submitted evidence. A synthetic acceptance test
is a synthetic result; an advisory review is not permission; and a single
domain success is not proof of general intelligence.

## Authorship and AI tools

Human contributors remain responsible for material they submit. AI systems may
be credited in development notes as tools, but should not be listed as authors,
owners, reviewers of record, grant issuers, or independent authorities. The
person submitting a contribution must verify it and have the right to provide
it.

## Contribution license

Unless a file clearly states otherwise, contributions submitted for inclusion
in Strongwiz are offered under the repository's
[CC BY 4.0 license](LICENSE). Contributors retain any rights the license does
not transfer. Third-party material must remain clearly marked and under terms
compatible with its use here.

Creative Commons recommends software-specific licenses for software; the
project's steward has nevertheless selected CC BY 4.0 for this repository. Do
not change the operative license without explicit steward authorization.

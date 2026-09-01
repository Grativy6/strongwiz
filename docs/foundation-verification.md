# Strongwiz foundation verification

Status: **PASS** for the declared pre-alpha foundation surface.

This receipt verifies the model-neutral Strongwiz laboratory at implementation
commit `d853a5186594e53103b156ac093092f24ba854c0` on branch
`codex/strongwiz-foundation`. It does not report an ARC-AGI-3 run or extend the
claim ceiling in [`claim-boundary.md`](claim-boundary.md).

## Verified surface

- 118/118 tests passed on Python 3.12.14;
- branch coverage passed the configured 85% gate at 85.66%;
- Ruff lint passed;
- Ruff format check passed for 53 files;
- strict configured mypy passed for 24 source files;
- the shadow-route example passed;
- the candidate-file secret scan found zero matching files and zero embedded
  credential patterns;
- `git diff --check` reported no whitespace errors (Git emitted only Windows
  line-ending conversion warnings).

Two read-only adversarial audits then replayed attacks against cross-context
PEA/PECAN/SEED records, forged or reused permits, empty-guard routes, admission
metadata and executor splices, fabricated and cross-route execution results,
post-call unknown effects, mutable runtime declarations, ledger projection
tampering, terminal smuggling, and causal-ablation overclaim. No material blocker
remained. The corresponding regression tests are part of the committed public
test suite.

## Reproducible wheel smoke

Two wheel builds from the implementation commit used
`SOURCE_DATE_EPOCH=1788248095` and produced the same artifact:

- file: `strongwiz-0.1.0.dev0-py3-none-any.whl`;
- SHA-256: `62176ecd7a297126168f767c8bb315838cf51ff8f11837fb9e60fdb02c89e261`;
- exact-match builds: 2/2;
- fresh-environment smoke: Python 3.12.14, Pydantic 2.13.5, import passed,
  schema CLI passed, `pip check` passed;
- wheel contents include the Strongwiz runtime and CC BY 4.0 license text.

The local wheel is a verification byproduct, not a published release. The
source commit and build inputs are the public reproduction surface.

## Claim ceiling

This is a pre-alpha reasoning laboratory and stable-contract candidate. It is
not a packaged autonomous Kaggle entry, an ARC-AGI-3 success receipt, a hostile
code sandbox, a durable distributed exactly-once executor, proof of causal
isolation supplied by a caller, or evidence of general intelligence. PEA Core
v1.1.3, PECAN v1.0.4, and SEED v0.3 are executable control profiles here; they
preserve review distinctions but do not manufacture consent, standing,
authorization, legal authority, or ethical correctness.

The machine-readable companion is
[`foundation-verification.v1.json`](receipts/foundation-verification.v1.json).

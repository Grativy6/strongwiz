# Strongwiz v0.2 release gate

Version `0.2.0` is prepared as a release candidate. This document does not
assert that a tag or GitHub release exists.

## Required evidence

Before publication, the candidate must have:

- a clean source tree on the reviewed commit;
- Python 3.12 tests with the declared coverage floor;
- Ruff formatting and lint success;
- strict mypy success for `src/strongwiz`;
- Linux and Windows CI success;
- successful generic non-ARC reference-lab execution;
- source-registry, lab-genesis, evidence-capsule, and checkpoint-restore tests;
- explicit review of any opaque domain-state bytes before a capsule is made
  public (the built-in acknowledgment alone is not a review);
- a secret scan and diff-whitespace check;
- two local builds whose expected sdist and wheel artifacts are byte-identical
  after the declared sdist timestamp normalization under one `SOURCE_DATE_EPOCH`;
- a draft, unmerged pull request describing the claim ceiling.

After the implementation commit is clean, create the local reproducibility
receipt with:

```console
python scripts/verify_reproducible_build.py --receipt docs/receipts/v0.2.0-build.json
```

The script builds twice beneath `build/reproducibility`, refuses a dirty tree,
requires the exact expected sdist and wheel, normalizes accepted
source-distribution tar-member timestamps to `SOURCE_DATE_EPOCH`, and
canonicalizes the gzip stream by setting its timestamp and removing its
original-filename header,
compares every post-normalization artifact hash, rechecks that source identity
stayed unchanged, and records the source commit/tree and epoch. It accepts only
one safe distribution root containing regular files and directories, portable
noncolliding paths, ordinary permission bits, no links, and no non-temporal PAX
metadata. Backend-supplied ownership IDs and names are preserved without being
interpreted, normalized, or certified. The emitted archive is reopened and
validated before replacement, so ownership values that would synthesize new
PAX metadata are refused without modifying the original artifact.
Paths, modes, payloads, and other accepted member metadata are preserved. The
receipt's claim is deliberately limited to two local builds after this declared
normalization; it is not a general cross-host reproducibility claim. The script
does not tag, upload, or publish anything.

## Publication boundary

Repository instructions reserve tagging and release publication for a later
explicit owner instruction. At that boundary, the owner or authorized tool
should verify the final commit and receipt, create an annotated `v0.2.0` tag,
push that tag, create the GitHub release, attach the two verified artifacts, and
then verify their downloaded hashes. No merge, release, contest submission, or
license change is implied by preparation.

The operative Strongwiz license remains CC BY 4.0. Any MIT-0 or CC0 choice for
a later Hearthline distribution is a separate legal object and does not alter
this repository.

## Pre-alpha checkpoint migration

Version 0.2 persists restart-complete checkpoints as the distinct
`strongwiz.session-checkpoint.v1` schema. Code that previously treated a
checkpoint payload as `strongwiz.session-receipt.v1` must call
`SessionCheckpoint.concise_receipt()` when it needs the concise receipt wire
type. This is an explicit pre-alpha compatibility break, not an implicit
schema substitution.

## Claim ceiling

The release can claim a model-neutral, locally runnable laboratory with typed
provider/domain boundaries, exact control separation, resumable sessions,
empty-state genesis, and portable sealed evidence on its tested surfaces. It
cannot claim AGI, universal model improvement, autonomous ARC Prize readiness,
hidden-task generalization, ethical certification, or authority to act.
Complete capsule integrity also does not establish that opaque domain-state
bytes are private-data-free, credential-free, or redistributable.

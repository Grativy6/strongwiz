# Lab genesis and sealed runs

Strongwiz treats the reusable toolbelt and each experimental run as different
sealed objects.

```text
Strongwiz source + model/domain/executor artifacts
                    |
              LabManifest
                    |
                 RunSpec
                    |
        zero-state genesis assertion
                    |
       immutable ledger + domain state
                    |
                 RunSeal
                    |
       complete portable evidence capsule
                    |
     optional PromotionReceipt (proposal only)
```

## Genesis

`strongwiz lab init` accepts a strict `LabManifest` and `RunSpec`. The target
must be absent or empty. Initialization creates the declared layout, an empty
SQLite ledger, an empty domain-state directory, and a canonical
`LabGenesisSeal` asserting:

- zero ledger receipts;
- zero ledger objects;
- no ledger head;
- zero domain-state entries;
- no inherited run or domain-state references.

The assertion is created before domain work begins. `lab verify
--require-genesis` proves the current lab still matches that starting surface;
after work begins, the immutable genesis receipt remains true of the start but
the current-state check correctly becomes false.

## Run specification

The predeclared `RunSpec` binds the objective, exact success state, terminal
authority source, evaluation class, seed, frozen runtime, model/domain
identities, resource budget, action aperture, policy/input references, and any
externally supplied execution grant. A genesis run refuses prior run or domain
state references.

This prevents a fresh calibration from silently inheriting another run's
learned mechanics, action sequence, or authority.

## Seal and capsule

`lab seal-run` binds a terminal disposition to an exact terminal evidence
object referenced by a sealed receipt; the complete ledger count, head, object
projection, and receipt projection; and the complete domain-state path, type,
size, and hash projection. `success_observed` is valid only when the completion
marker is true and the terminal state matches the predeclared success state.
Other honest dispositions remain available: `partial`, `blocked_external`,
`failed_mechanism`, and `failed_infrastructure`.

`lab pack-evidence` exports canonical copies of:

- the lab manifest, run specification, genesis seal, and run seal;
- every object in the SQLite content store;
- every hash-chained receipt envelope;
- every sealed domain-state file and empty directory;
- a capsule manifest binding exact paths, sizes, hashes, projections, and
  terminal claim.

Capsule verification reconstructs the chain and checks object, payload,
parent, sequence, count, head, domain-state, path, and terminal bindings. It
rejects path escape, symlinks, Windows junctions and reparse points, divergent
overwrite, missing objects, undeclared files or directories, and typed fields
that claim to contain private chain-of-thought. Concise rationale, predictions,
alternatives, and update summaries remain part of the evidence surface.

Domain-state files are intentionally opaque to the generic kernel. Strongwiz
seals and copies their exact bytes but does not sanitize, classify, or review
them for credentials, private reasoning, personal data, or redistribution
rights. The concise-reasoning policy applies only to typed Strongwiz ledger
records. Packing nonempty domain state therefore requires
`--acknowledge-opaque-domain-state`, and every resulting manifest remains
`opaque_unsanitized_not_publication_reviewed`. That acknowledgment permits the
requested local copy; it is not permission or authorization to publish. Review
the capsule separately before committing, uploading, or sharing it.

## Promotion is not inheritance

A `PromotionReceipt` may nominate a mechanism for a later lab. It remains
`proposed_not_adopted`, requires independent review and ablation, and excludes
domain state, action sequences, replay state, learned mechanics, hidden
reasoning, and authority. This is the reopening handle between runs without
turning prior success into a clean-room prior.

## Inspection

The sealed run is not a museum. Inspect the portable capsule or a disposable
clone freely. A clean handoff means audit byproducts are outside the sealed
specimen and `git status`/declared hashes still match; it does not prohibit
reading the evidence. Lab and ledger verification use immutable SQLite reads
and refuse active or uncheckpointed sidecars so inspection itself does not add
repository-local state.

# Strongwiz

**Bring whatever model you have. Strongwiz gives it a laboratory.**

Strongwiz is a model-neutral operating layer for difficult work. It separates
observation, retained mechanics, experiments, planning, control, execution,
verification, and receipts so that a model can change without erasing the
identity of the work.

The project is general-purpose. ARC-AGI-3 is one development adapter and source
of hard-won machinery; it is not the kernel's definition.

> Status: `0.4.0.dev0`, development build. The typed kernel, lab-genesis
> commands, local model adapters, resumable sessions, and sealed evidence
> capsules are runnable. Kevin Speak, the representation-only scribe, and the
> adaptive curriculum are experimental. No `v0.4.0` release has been published.
> This is not an ARC Prize submission or evidence of general intelligence.

## The declared boundary

The public contract candidate is `strongwiz.contract.v1`. Compatibility is
exercised and versioned rather than promised across every future `0.x` release:

| Boundary | What crosses it | What does not |
| --- | --- | --- |
| Observation | Raw-evidence reference, scope, epoch, summary, action aperture | Hidden interpretation presented as fact |
| Action | Goal-bound proposal, meaningful distinction, prediction, costs | Permission, authorization, or execution |
| Memory | Versioned facts, mechanics, residuals, continuation state, reopening handles | Silent mutation or stale-cache reuse |
| Receipt | Canonical payload, account/version, parent link, hash-chain identity | Truth, proof, or authority by assertion |

```text
model driver -> proposals -> Strongwiz guards -> advisory route
                                      |
external control -> grant + lab rules-+
                                      |
                     exact one-use execution coordinator
                                      |
                            single external writer
                                      |
domain adapter <- evidence-bound outcome + terminal authority
```

Models propose. Control state is supplied independently. Strongwiz records an
advisory route with no authority and no external effect. If an integration is
allowed to act, the control-owned coordinator rechecks the exact route, control
snapshot, lab decision, grant, proposal, action, observation content, goal pair,
executor ID/version/artifact, and one-use permit at the writer call. The
coordinator returns an opaque in-process result that assessment accepts only for
the exact decision route and control snapshot. The domain—not the model—then
remains authoritative for success or failure.

The session pins the exact model object, model/domain versions and artifacts,
and router/cadence policy digests from its frozen manifest, and revalidates
their declared identities at each call boundary. With a ledger configured,
state is durably receipted before it becomes actionable, and assessment
requires the exact completed execution evidence.

## What is implemented

- goal-linked meaningful distinctions and falsifiable predictions;
- scan -> decide -> assess lifecycle with stale-action and repeated-failure guards;
- A0BK-style accounts, versions, successors, residual lineage, and eight hard
  route guards;
- immutable canonical JSON receipts with explicit occurrence identities in a
  single-writer SQLite hash chain;
- earned derived facts with lower-bound, exact, exact-negative, transfer, cost,
  and invalidation semantics;
- scoped mechanic versions, consequence-channel residuals, and implicated-only
  local repair;
- fast/deep cadence and deterministic bounded A* planning;
- branch-safe feedback state, structural-horizon audits, and generic 2x2 causal
  splice experiments;
- retained-receipt versus cache versus discard ablations with heterogeneous
  costs and fixed denominators;
- externally rooted, revocable task grants and one-use nonserializable permits;
- PEA Core v1.1.3, PECAN v1.0.4, and SEED v0.3 control-owned lab interfaces;
- an exact-bound execution admission bridge that rechecks the proposal, route,
  control snapshot, lab decision, task grant, executor artifact, and one-use
  permit, then returns an opaque coordinator-issued result;
- frozen runtime manifests binding code, configuration, dependencies, drivers,
  adapters, policies, and model artifacts;
- restart-complete checkpoints for every session phase without repeating a
  model call or environment action;
- reference-normalized durable checkpoints that retain history by exact ledger
  references instead of copying an ever-growing session history into each save;
- bounded-memory ledger verification and sealing whose projection identities
  remain byte-compatible with the existing evidence-capsule contract;
- zero-state `LabManifest`/`RunSpec` genesis with an empty ledger and no prior
  domain state;
- immutable terminal run seals and complete portable evidence capsules that
  carry every ledger object, receipt, and sealed domain-state entry;
- in-process callable-model and bounded binary framed-model adapters, with no
  newline-delimited terminal protocol;
- structural model/domain conformance reports that grant no execution
  authority;
- explicit, replaceable experimental features, with GPPR and Kevin Speak
  disabled by default;
- blank, model-authored Kevin Speak codebooks with a fixed decoder, exact
  round trips, uncompressed residuals, multi-case promotion gates, and
  recommendation -> review/refinement -> scoped adoption -> transfer receipts;
- a dedicated representation-only scribe with a separate driver identity,
  receipt-bound derived inputs, adaptation/held-out validation separation,
  typed failure fallback, and restart-safe semantic cycle identities;
- a targeted PAL v2.3 adapter for role-typed boundaries, explicit state
  projections, immutable grant epochs, checkpoint freeze/thaw, heartbeat
  stutter, and re-entry receipts, without claiming package-wide conformance;
- an event-driven steering heartbeat that suppresses unchanged timer-like pings;
- a 30/60/90/final adaptive curriculum with one separately sealed run per stage
  and explicit learned-stack inheritance;
- an exact fourteen-source registry covering eleven named papers/framework
  faces plus PEA/PECAN/SEED;
- a deliberately narrow ARC-AGI-3 terminal-authority adapter with no game IDs,
  policies, or action scripts.

## Just add a model

For a local Python model, return one or more `ProposalDraft` values from an
ordinary callback and wrap it with `CallableModelDriver`. Strongwiz adds the
exact driver, observation, scope, and goal bindings itself:

```python
from strongwiz.modelkit import CallableModelDriver

driver = CallableModelDriver(
    driver_id="my-local-model",
    driver_version="1",
    driver_artifact_ref="<sha256-of-model-and-configuration>",
    proposal_function=my_proposal_function,
)
```

`FramedModelDriver` provides the same boundary for an offline model process
over canonical, length-prefixed binary JSON with checksums, bounds, timeouts,
partial-I/O handling, and replay guards. Its explicit
`FramedModelRestartState` reserves request identities before I/O and can be
persisted by the caller across a process crash. It does not use a TTY or newline
protocol, and it does not claim to supervise or restart the provider process.

“Just add model” applies to the reasoning-provider boundary. Consequential
work still needs a domain adapter, a single-writer executor, and an externally
supplied grant; Strongwiz deliberately does not invent those authorities. See
[Just-add-model guide](docs/just-add-model.md).

## Install and verify

Python 3.12 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.lock.txt
.venv\Scripts\python -m pip install --no-deps -e .
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy src\strongwiz
.venv\Scripts\python -m pytest --cov=strongwiz --cov-report=term-missing
```

On POSIX systems, replace `.venv\Scripts\python` with `.venv/bin/python`.

Print the model-driver request schema or audit an existing ledger:

```console
strongwiz schema
strongwiz schema --all
strongwiz verify-ledger path/to/run.sqlite3
strongwiz verify-sources docs/source-identities.json
strongwiz kevin schema
strongwiz kevin init path/to/kevin.sqlite3 --workspace-id my-run
strongwiz kevin verify path/to/kevin.sqlite3 --workspace-id my-run
strongwiz kevin table path/to/kevin.sqlite3 --workspace-id my-run
strongwiz pal23 schema
strongwiz scribe schema
```

Create and verify a genuinely empty laboratory from predeclared manifests,
then seal and package its evidence after a run:

```console
strongwiz lab init playground/my-lab --manifest lab-input.json --run-spec run-input.json
strongwiz lab verify playground/my-lab --require-genesis
strongwiz lab seal-run playground/my-lab --disposition partial --terminal-state STOPPED --terminal-evidence-ref <sha256> --summary "bounded run stopped"
strongwiz lab pack-evidence playground/my-lab artifacts/local/my-capsule --acknowledge-opaque-domain-state
strongwiz lab verify-capsule artifacts/local/my-capsule
```

The opaque-domain flag acknowledges only the requested local copy. Strongwiz
does not inspect domain-state bytes for credentials, private reasoning,
personal data, or redistribution rights; review them independently before any
commit, upload, or publication.

Run [`examples/shadow_route.py`](examples/shadow_route.py) for a complete
nonexecuting route through the declared proposal/control boundary. Run
[`examples/reference_counter_lab.py`](examples/reference_counter_lab.py) for a
generic non-ARC lab that starts from genesis, uses a local model, crosses the
exact grant/single-writer boundary, observes domain success, resumes from a
complete checkpoint surface, and packs a verified evidence capsule.
Run [`examples/kevin_speak_campaign.py`](examples/kevin_speak_campaign.py) for
a synthetic non-ARC source -> sealed bundle -> stronger-model review -> scoped
adoption -> successor shorthand demonstration. It tests representation
mechanics only and does not evaluate model-facing behavior.
The audited foundation commands, results, reproducible-wheel hash, and claim
ceiling are recorded in
[`docs/foundation-verification.md`](docs/foundation-verification.md).

## Laboratory rules

Strongwiz does not treat fluent output as control authority. The laboratory is
governed through three independently supplied profiles:

- **PEA Core v1.1.3** reviews consent, standing, privacy, reversibility, remedy,
  contestability, refusal, and human responsibility without deciding or acting.
- **PECAN v1.0.4** preserves
  `description != recommendation != permission != authorization`; authorization
  must come from outside the model.
- **SEED v0.3** reviews human-facing release for agency, uncertainty, privacy,
  correction, reopening, and a natural stop.

These are bounded software interfaces, not legal or ethical authorities. See
[`docs/lab-rules.md`](docs/lab-rules.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Reasoning loop](docs/reasoning-loop.md)
- [Adapters and packaging](docs/adapters.md)
- [Lab genesis and sealed runs](docs/lab-genesis.md)
- [Just-add-model guide](docs/just-add-model.md)
- [Kevin Speak](docs/kevin-speak.md)
- [Representation scribe](docs/scribe.md)
- [Targeted PAL v2.3 profile](docs/pal-v2.3-profile.md)
- [Adaptive calibration campaigns](docs/adaptive-calibration.md)
- [v0.3 development verification](docs/v0.3-development-verification.md)
- [Retention ablations](docs/retention-ablation.md)
- [Claim boundary](docs/claim-boundary.md)
- [Provenance](docs/provenance.md)
- [v0.2 release gate](docs/release-v0.2.md)
- [v0.2 verification and evidence](docs/v0.2-verification.md)
- [Foundation verification receipt](docs/foundation-verification.md)
- [ARC-AGI-3 Calibration 001 result](docs/calibrations/001-result.md) — bounded
  Codex-operated `PARTIAL`; official `GameState.WIN` was not observed
- [ARC-AGI-3 Calibration 002 result](docs/calibrations/002-result.md) — adaptive
  Strongwiz v2 campaign paused `PARTIAL`; Kevin Speak remained lossless but blank
- [ARC-AGI-3 Calibration 003 preparation](docs/calibrations/003-strongwiz-v3-pal23-scribe.md)
  — matched v3 design prepared; no ARC environment interaction yet

## Stewardship, provenance, and license

Christopher D. Pang is the author and steward. AI systems are development and
review tools, not co-authors, owners, grant issuers, or independent authorities.

The implementation is a fresh, modular synthesis informed by prior ARC3,
Model Scientist, Wise Scientist, A0BK, FBT, and Prime experiments. Their measured
results and licenses are not silently inherited as Strongwiz claims. Exact
source identities and adaptation boundaries are recorded in
[`docs/provenance.md`](docs/provenance.md) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

First-party Strongwiz material is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). See [LICENSE](LICENSE).

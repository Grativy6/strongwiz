# Strongwiz

**Bring whatever model you have. Strongwiz gives it a laboratory.**

Strongwiz is a model-neutral operating layer for difficult work. It separates
observation, retained mechanics, experiments, planning, control, execution,
verification, and receipts so that a model can change without erasing the
identity of the work.

The project is general-purpose. ARC-AGI-3 is one development adapter and source
of hard-won machinery; it is not the kernel's definition.

> Status: `0.1.0.dev0`, pre-alpha. The typed kernel and deterministic fixtures are
> runnable. This is not yet an autonomous product, an ARC Prize submission, or
> evidence of general intelligence.

## The declared boundary

The public contract candidate is `strongwiz.contract.v1`. Strongwiz is still
pre-alpha, so compatibility is exercised and versioned rather than promised
across every future `0.x` release:

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
- a deliberately narrow ARC-AGI-3 terminal-authority adapter with no game IDs,
  policies, or action scripts.

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
```

Run [`examples/shadow_route.py`](examples/shadow_route.py) for a complete
nonexecuting route through the declared proposal/control boundary.

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
- [Retention ablations](docs/retention-ablation.md)
- [Claim boundary](docs/claim-boundary.md)
- [Provenance](docs/provenance.md)

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

# Calibration 001 integration

This directory is an additive, run-local bridge around the unchanged Strongwiz
v0.2 kernel. It implements the frozen clean-room protocol in
`docs/calibrations/001-strongwiz-arc3-clean-room.md`; it does not change that
protocol or the kernel.

## Safe order of operations

All paths below stay in the repository's ignored `artifacts/local/` and
`playground/` trees.

```powershell
# 1. Networked acquisition only. This performs three targeted official GETs,
#    never imports game source, and never constructs an environment.
python -m calibration acquire artifacts/local/calibration-001/assets

# 2. Freeze the preregistration, exact asset, dependencies, toolbelt, policies,
#    integration, model interface, grant, and budgets; then prove empty genesis.
python -m calibration prepare `
  playground/calibration-001/run `
  artifacts/local/calibration-001/assets `
  --run-id calibration-001

# 3. In a dedicated process, verify the prepared-only state, construct the exact
#    versioned game offline, count Arcade.make's implicit reset, and serve loopback.
python -m calibration serve `
  playground/calibration-001/run `
  artifacts/local/calibration-001/assets `
  --endpoint playground/calibration-001/control-endpoint.json
```

The acquisition command is the only networked command. Its anonymous key is
kept only in a local variable and is never printed, logged, hashed, or written.
The live process forces `OPERATION_MODE=offline`, clears `ARC_API_KEY`, sets
`ONLY_RESET_LEVELS=false`, and uses a fail-closed loopback base URL before
importing `arc_agi`.

## Isolated operator commands

The persistent server is the only holder of the environment wrapper. A
cross-process exclusive lock prevents a second live owner, and the wall-clock
budget starts immediately before `Arcade.make`. A context-isolated operator
uses one-shot clients:

```powershell
python -m calibration status playground/calibration-001/control-endpoint.json
python -m calibration act playground/calibration-001/control-endpoint.json `
  --input playground/calibration-001/proposal-00001.json
python -m calibration assess playground/calibration-001/control-endpoint.json `
  --input playground/calibration-001/assessment-00001.json
```

Every command and response uses Strongwiz's `SWZJ` version-1 length-prefixed,
SHA-256-protected canonical JSON framing over loopback TCP. Message identities
are replay-guarded. The endpoint contains a fresh per-server capability copied
into each request; it is never logged, returned, or written to the ledger. This
deters accidental or unrelated local writers, but same-OS-identity context
isolation remains procedural rather than cryptographically enforceable.
`status` supplies the exact `ReasoningRequest`, current raw
JSON path, pinned 16-color ARC-AGI-3 PNG paths, legal actions, phase, checkpoint,
and budget counters. It
does not supply game source, internal objects, private tags, baseline actions,
prior-run hints, or a parent-authored recommendation.

A proposal input is a `strongwiz.arc-agi3-proposal-draft.v1`. The operator owns
only concise interpretation fields and an action candidate; the bridge adds the
exact driver, observation, scope, goal, and cost bindings. An assessment input
is a `strongwiz.arc-agi3-assessment-draft.v1`. Private chain-of-thought fields
are rejected, and input files outside ignored `playground/` are refused.

Proposal attempts are monotone within one observation. The first draft uses
`proposal_attempt: 1` and no predecessor. If Strongwiz holds or otherwise does
not select it, the response and subsequent `status` provide the exact next
attempt number and `supersedes_proposal_ref`. A revision must bind both values;
the held proposal remains in evidence. Attempts cannot be skipped or replayed,
and an admitted proposal can never be replaced.

After `GAME_OVER`, the next observation aperture contains only `RESET`. The
failed assessment remains in the session ledger; an assessed reset can continue
the same run. An infrastructure exception after an uncertain official call is
`UNKNOWN_EFFECT` and is never retried automatically.

Before `Arcade.make`, the harness reserves and durably records the implicit
initial reset. It then writes the raw frame, trace entry, PNGs, completion
contract, and ledger receipt immediately after success. Every later action has
the same append-only admission/completion/assessment closure. An unclosed
admission blocks another server from making or stepping the game. If the live
process ends in that window, `seal` records `FAILED_INFRASTRUCTURE` with
`UNKNOWN_EFFECT`; it does not repeat the call. Known wall/action/reset/total
budget denials occur before Strongwiz admission and stop as `PARTIAL`, with no
environment effect charged.

To stop without completion:

```powershell
python -m calibration stop playground/calibration-001/control-endpoint.json `
  --summary "preregistered budget reached without official WIN"
```

An exact post-action `arcengine.GameState.WIN`, normalized by the frozen adapter
and assessed by Strongwiz as `TerminalAuthority.SUCCESS`, is the only success
path. A scorecard value, level change, terminal-looking image, or unassessed
frame cannot trigger success.

## Seal and capsule

After the server closes:

```powershell
python -m calibration seal playground/calibration-001/run
python -m calibration capsule `
  playground/calibration-001/run `
  artifacts/local/calibration-001/capsule `
  --receipt artifacts/local/calibration-001/run.receipt.json
```

The immutable terminal record is bound into the Strongwiz ledger before the run
seal. The capsule command verifies the complete ledger and opaque domain-state
projection. The final delivery receipt remains outside the capsule because
embedding a receipt that contains the capsule's own hash would be circular.

## Verification

```powershell
.venv\Scripts\pytest.exe -q
.venv\Scripts\ruff.exe check .
.venv\Scripts\mypy.exe
```

The integration tests use injected HTTP and environment fixtures. They do not
access the ARC API, import an official game artifact, construct an official
environment, or perform an official action.

# Calibration 001 — Codex-operated Strongwiz on ARC-AGI-3

Status: preregistered before the first game artifact, observation, or action.

## Purpose and claim ceiling

This is a calibration of Strongwiz as a model-neutral laboratory. It is not a
competition entry, Kaggle rehearsal, autonomous-offline-agent claim, verified
score, private evaluation, or general ARC-AGI-3 performance claim.

The governing objective is to play one preregistered official public game until
the pinned official environment itself reports `GameState.WIN`. A level
transition, scorecard field, high score, inferred completion, synthetic test,
or complete-looking model is not completion. `NOT_FINISHED` requires continued
work. `GAME_OVER` is preserved failure evidence and permits only recovery or
reset behavior supplied by the official environment.

One observed public-game `WIN` earns only this statement:

> A context-isolated Codex operator, using the frozen Strongwiz toolbelt and the
> declared run-local integration, observed an official public ARC-AGI-3
> environment report `GameState.WIN` for this run.

## Frozen identities

- Toolbelt repository: `https://github.com/Grativy6/strongwiz`
- Toolbelt commit: `a85508dc11cc6ac30336f5c42344b62afdc86b24`
- Toolbelt tree: `9e58cb361919fca3638b1f76a00379740c4e4aa4`
- Calibration branch: `codex/strongwiz-arc3-calibration`
- Python: 3.12
- Official toolkit: `arc-agi==0.9.9`
- Official engine: `arcengine==0.9.3`
- Seed: `0`
- Evaluation class: `local-public`
- Preregistered game name: `ls20`
- Exact versioned game and artifact hashes: unresolved until authorized
  dependency acquisition; they must be frozen before environment construction.

`ls20` was selected because it is the official toolkit's published QuickStart
example. It was selected before a frame or game source was observed.

## Authority boundary

Christopher D. Pang explicitly authorized this non-contest calibration to use
the official public ARC API despite the noted conflict between the general site
terms and the published agent/testing surfaces. The authorization forbids
account creation, owner credentials, submissions, and competition entry.

The SDK's anonymous key may exist transiently in process memory. It must not be
printed, logged, hashed into evidence, written to disk, committed, or disclosed.
No `COMPETITION` operation mode, Kaggle path, submission path, private game, or
registered-key game is in scope.

## Clean-room boundary

The action-selecting process must not read or receive:

- the ARC3 development repository;
- Hearthline repositories or context;
- Little, Model, or Wise Scientist traces, receipts, mechanics, action
  sequences, summaries, or derived hints;
- Codex memory or prior rollout summaries;
- official game source, private tags, baseline actions, internal game objects,
  or hidden evaluator state;
- any action recommendation from the orchestration parent.

The official SDK may load the sealed game artifact to execute it. The operator
receives only the official `FrameDataRaw` projection, current legal actions,
its own run-local Strongwiz records, and generic public interface semantics.

Before game access, the orchestration parent was involuntarily shown a stale
completed Wise Scientist subagent message containing prior gameplay details.
No environment had been acquired or opened and no action had occurred. The
parent is therefore disqualified from gameplay decisions. A newly spawned
Codex process with no conversation fork will make every proposal. The parent
may operate infrastructure and verify evidence but may not recommend an action,
interpret a frame for the operator, or transmit the stale content.

Before game access, the same collaboration-status behavior also exposed the
generic harness implementer and an independent code-audit process to unrelated
prior gameplay text. Neither process requested, used, or relayed that content,
and neither had accessed an environment or selected an action. Both are
non-clean-room infrastructure processes and are disqualified from gameplay.
These incidents do not contaminate the uncreated no-fork operator, but they must
remain in the handoff and terminal incident record. The operator must not call
collaboration-status or agent-management tools during play.

## Toolbelt and run separation

Strongwiz source is the frozen toolbelt object. The lab genesis, raw frames,
proposals, actions, consequences, checkpoints, official recording, terminal
evidence, run seal, and evidence capsule form a distinct run object.

Genesis must prove:

- zero ledger objects and receipts;
- no ledger head;
- no domain-state entries;
- no prior run references;
- no prior domain-state references;
- no inherited mechanics or action sequences.

Dependency acquisition and its hashes precede genesis. Environment construction
and its implicit initial reset occur only after genesis and are recorded as the
initial observation boundary.

## Model identity and reasoning record

The model is an external context-isolated Codex runtime. The frozen artifact
binds the integration source, model-interface declaration, configuration, and
every recorded proposal; it cannot bind inaccessible hosted weights or service
runtime. The run must not be relabeled autonomous, offline-reproducible, or
fully model-frozen.

For each action, retain only concise decision-relevant records:

- current goal or subgoal;
- meaningful distinction and competing resolutions;
- falsifiable prediction and alternatives;
- action candidate and declared costs;
- concise rationale and evidence references;
- observed consequence;
- matched prediction components;
- localized residuals;
- preserved and revised hypotheses.

Do not request or store private chain-of-thought.

## Learning and action policy

Learning serves progress toward `WIN`. Prefer the smallest reversible action
that distinguishes consequential alternatives when uncertainty blocks a plan.
When a supported plan exists, prefer the shortest credible progress path.
Retain reliable mechanics across levels. A new surface closes the previous
surface attentionally, not permanently; reopen only the smallest implicated
model when later evidence conflicts.

Do not exhaustively enumerate interactions. Prioritize distinctions that could
change movement, access, resources, hazards, progress, candidate choice, or the
next experiment.

## Preregistered budgets

- Non-reset environment actions: at most `2048`.
- Resets, including the SDK's initial reset: at most `64`.
- Total official environment calls: at most `2112`.
- Wall-clock run allowance: `8 hours` from initial observation.
- Coordinate actions: only current legal `ACTION6`, with `x,y` in `0..63`.
- After `GAME_OVER`: preserve the terminal frame; only `RESET` may follow.

Reaching a budget without `WIN` is `PARTIAL`, not completion. Infrastructure
loss after an uncertain external call is recorded as `UNKNOWN_EFFECT`; it is
not silently retried.

## Required terminal receipt

The final receipt must contain:

- exact game ID and official artifact identity;
- final environment state;
- `levels_completed` and `win_levels`;
- non-reset action count, reset count, and total SDK call count;
- elapsed wall time;
- Strongwiz toolbelt, integration, dependency, model-interface, domain, and
  executor identities;
- lab genesis, latest checkpoint, run seal, official recording, raw trace, and
  evidence-capsule paths and hashes;
- whether `GameState.WIN` was genuinely observed;
- explicit claim class and exclusions;
- any failure, recovery, contamination incident, or unresolved burden.

No artifact may claim success unless the terminal raw observation bound into the
ledger contains the pinned Python enum state `GameState.WIN` and Strongwiz's ARC
domain authority independently maps it to success.

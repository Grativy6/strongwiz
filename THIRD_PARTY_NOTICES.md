# Third-party notices and source lineages

This file records material that informed Strongwiz and the boundary under which
it was used. It is not a claim that every listed source contributed copied code,
nor does it relicense any source.

## Implementation provenance

The initial Strongwiz foundation is an original, clean conceptual
implementation. No source code was copied from the FBT, A0BK, Prime Axiom, or
ARC experimental repositories listed below. Their experiments and designs were
used to identify mechanisms worth expressing through new, model-neutral
interfaces. Similarity of an idea or interface purpose does not transfer the
source repository's copyright or license.

| Source lineage | Inspected source identity | Licensing treatment | Use in Strongwiz |
| --- | --- | --- | --- |
| ARC3 Build 003 / Little Scientist | [`10e53c2150ea40da87eec5566ba7af1cfc3a591e`](https://github.com/Grativy6/ARC3/commit/10e53c2150ea40da87eec5566ba7af1cfc3a591e) | Source identified as MIT-0. | Conceptual evidence for scoped mechanics, residual localization, bounded probes, and authoritative terminal-state receipts. |
| Model Scientist | [`41d73427468afa7a8d797d93a87efd6e2a7e9403`](https://github.com/Grativy6/ARC3/commit/41d73427468afa7a8d797d93a87efd6e2a7e9403) | Source identified as MIT-0. | Conceptual evidence for decision-relevant distinctions, subgoal lifecycles, prediction assessment, and reopening. |
| Wise Scientist v2 | implementation `19c6be5b51d72b8dfdf8b0531316bf7a52c050d9`; frozen run `7fd6252bae08f5aa76bb683502461fd17a22daf6` | Source identified as MIT-0. | Conceptual evidence for single-writer traces, raw/derived evidence separation, hash chaining, and explicit runtime claim ceilings. |
| FBT experimental lineage | [`1427434f821d0d54b06f4027c09a78312745c658`](https://github.com/Grativy6/FBT/commit/1427434f821d0d54b06f4027c09a78312745c658) | No license was present in the inspected source snapshot. Public or private visibility is not a reuse grant. The common owner expressly authorized a new clean conceptual implementation in Strongwiz; that instruction does not retroactively license or relicense FBT. | Version-bound continuation, component interaction contrasts, retain/discard ablations, and structural-horizon diagnostics. No FBT model code or weights are included. |
| A0 Boundary-Layer Kernel / `a0-zsa-kernel` | [`e0e64ede7b87c05aefe8aee063dc26a5e658d335`](https://github.com/Grativy6/a0-zsa-kernel/commit/e0e64ede7b87c05aefe8aee063dc26a5e658d335) | No operative source license was observed at the inspected commit. The common owner authorized clean conceptual implementation only; Strongwiz does not purport to relicense A0BK. | Proposal/control separation, account lineage, explicit witnesses, guards, residuals, successor/version transitions, and route dispositions. |
| Prime Axiom Software Build 005 | [`b640e3aa44adddc6d9b560142d028d8f2092a546`](https://github.com/Grativy6/Prime_Axiom_Software/commit/b640e3aa44adddc6d9b560142d028d8f2092a546) | Source license is not asserted by this notice. It is treated as conceptual lineage; no source code was copied. | Earned derived-fact receipts, exact/negative/lower-bound states, invalidation and transfer rules, and explicit acquisition/validation/transport costs. Prime-specific scouting is not part of the general kernel. |

Commit identifiers are the evidence boundary. Later changes in those
repositories do not silently alter this notice.

## Calibration 001 execution boundary

Calibration 001 distinguishes code actually imported or executed from
conceptual influence and structural correspondence.

| Component | Bound identity | Treatment in Calibration 001 |
| --- | --- | --- |
| Frozen Strongwiz toolbelt | commit `a85508dc11cc6ac30336f5c42344b62afdc86b24`; tree `9e58cb361919fca3638b1f76a00379740c4e4aa4` | First-party Strongwiz code actually executed. The two run-local integrations were separately bound by content refs; they did not mutate the frozen toolbelt. |
| Python interpreter | Python 3.12 | Executed runtime. Only the major/minor identity was frozen; no interpreter source or binary is redistributed here. Upstream terms remain applicable. |
| ARC-AGI toolkit | `arc-agi==0.9.9` | Third-party package imported and executed to acquire the authorized local-public artifact and construct the official environment. No package source, wheel, game source, or artifact is vendored in this repository. |
| ARC engine | `arcengine==0.9.3` | Third-party package imported and executed; its `GameState` enum, projected through the frozen adapter, supplied terminal-state authority. No package source or binary is vendored here. |
| Context-isolated Codex selector | external process; hosted weights not bound | Operated the run-local proposal and assessment interface. It is not a bundled dependency or a reproduced model artifact, and no autonomous-offline claim is made. |
| Streaming post-run verifier | source SHA-256 `4c00f2ea221c6ff63ddd288d31389878f93b889052310dec261ca8c0a717bc0f` | New first-party Strongwiz code added and executed only after gameplay closed. It finalized and verified the evidence package; it did not select actions or contribute to the run outcome. |

The exact run-specific bindings and receipts are in
[`docs/calibrations/001-result.md`](docs/calibrations/001-result.md) and
[`docs/calibrations/receipts/001/`](docs/calibrations/receipts/001/).
The upstream runtime packages were obtained separately and retain their own
terms. This notice does not assert a license beyond what the corresponding
upstream distribution supplies.

No FBT, A0BK, Prime Axiom, Little Scientist, Model Scientist, or Wise Scientist
repository code or weights were imported or executed in Calibration 001.
Their entries below remain conceptual provenance only. Similar structure is not
evidence of code transfer, independent corroboration, or causation, and the
calibration does not support a claim that FBT caused any observed ARC behavior
or efficiency.

## Owner-supplied formal source stack

The coordinated
[PAL v2.3 — Primitive Axiom Layers](https://doi.org/10.5281/zenodo.22240134)
author release supplies five distinct authority faces. Each face identifies
Christopher D. Pang as author and steward, `2026-09-02` as its publication
date, and CC BY 4.0 as its license. Strongwiz records the following exact DOCX
identities without redistributing their text, figures, or equations.

| PAL v2.3 face | Version | Frozen DOCX SHA-256 | Bounded use in Strongwiz |
| --- | --- | --- | --- |
| Mechanical Structural Spine | `2.3` | `e9517b17278b72995f22469d825a62ad9a47d3a151089684f3d4c3ef96e4e9a2` | Prospective mechanical semantics and invariant vocabulary; no external authority or automatic Strongwiz conformance. |
| Mathematical Realization Atlas | `2.3-M` | `c053292376363edd6fc743f0f2e31e3bb3850edc78ade3a289bbb07e7e8452c5` | Prospective scoped realization maps, boundary adapters, and fixtures; no spine amendment or universal result. |
| Obligation and Decision Ledger | `2.3-L` | `694449304139c642c5112f9e41f3b41848a652fc7d12b9a60450bf5e776f704b` | Prospective decision, obligation, residual, and reopening evidence; no manufactured closure or authority. |
| Conformance Tests | `2.3-T` | `bf79d3a76ef71d6946704be551488bdfe2c062231f4a0c1a85bb51b203fe4b89` | Prospective specifications, fixtures, and falsifiers; bounded passing checks are not independent validation. |
| Compatibility Note | `2.3-C` | `57bd5432c6a0a4474c781f918e60e1fcf3f1b80119bfdc317bf6c17bbbc80f07` | Prospective migration and non-retrofit guidance; it cannot redefine another authority face. |

PAL v1 and v2 provenance remains attached to its original versions and
receipts. Strongwiz registers PAL v2.3 only as a prospective `SUCCESSOR` source
for new records that bind the updated registry; it asserts neither a
PAL-native `VERSION` transition nor full PAL v2.3 conformance. The source text
is evidence, not repository authorization, and the common authorship does not
make the five faces independent corroborations.

The following earlier works by Christopher D. Pang were inspected as
conceptual design sources. Their exact local artifact hashes and persistent
identifiers are in
[`docs/source-identities.json`](docs/source-identities.json). No paper text,
figures, or equations are redistributed by Strongwiz, and a shared
author/steward does not make these independent corroborations.

| Work | Persistent identity | Bounded use |
| --- | --- | --- |
| Golden Phase Prime Ribbons v0.1 | [Zenodo 22225414](https://doi.org/10.5281/zenodo.22225414) | Optional geometry-aware path experiment, disabled by default. |
| A0 Software Boundary-Layer Kernel v0.10.0 | [Zenodo 22168887](https://doi.org/10.5281/zenodo.22168887) | Accounts, guards, residuals, proposal/control separation, and reopening. |
| The Context Sets a Rhythm v0.1 | [Zenodo 22214952](https://doi.org/10.5281/zenodo.22214952) | Replaceable cadence and refresh policy design. |
| The Context Draws a Map v1.0 | [Zenodo 21831000](https://doi.org/10.5281/zenodo.21831000) | Local context maps, route distinctions, and reopening handles. |
| The Context Is the Model | [Zenodo 21713134](https://doi.org/10.5281/zenodo.21713134) | Context identity and model/runtime/work separation. |
| PAL Single-Cut Transport Lemma v0.1 | [Zenodo 21882601](https://doi.org/10.5281/zenodo.21882601) | Exact boundary transport accounting. |

PEA Core v1.1.3, PECAN v1.0.4, and SEED v0.3 are separately identified
control-policy inputs. Their software profiles remain non-authorizing and are
not represented as legal, ethical, or institutional authority.

## Research context, not imported code

Strongwiz's treatment of feedback and retained continuation was informed by:

> Xi Wang, Ziyang Cai, Zheng Zhan, Harry Dong, Ying Fan, Gustavo de Rosa,
> Tim Pearce, and John Langford. “Full-bandwidth transformer.”
> [arXiv:2608.08888v1](https://arxiv.org/abs/2608.08888), 2026.

The paper is cited as technical context. Strongwiz does not include its text,
equations, training recipe, model implementation, or weights, and this citation
does not claim that Strongwiz reproduced the paper's results.

## Package dependencies

Strongwiz depends on third-party Python packages declared in `pyproject.toml`.
Those packages are obtained separately and remain under their own licenses.
Their presence as dependencies does not place their source code under CC BY
4.0. Distributions should preserve notices required by the exact dependency
versions they redistribute.

## CC BY 4.0 software caveat

The owner deliberately selected CC BY 4.0 for Strongwiz first-party material.
Creative Commons [recommends software-specific licenses for
software](https://creativecommons.org/faq/#can-i-apply-a-creative-commons-license-to-software)
because CC licenses do not contain source-distribution or software-patent terms
and can be difficult to combine with common software licenses. This disclosure
does not replace, narrow, or change the operative Strongwiz license.

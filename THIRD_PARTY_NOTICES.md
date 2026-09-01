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

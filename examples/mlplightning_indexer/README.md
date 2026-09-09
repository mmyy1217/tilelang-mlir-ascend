# MlpLightningIndexer Prefill V1

TileLang Expert single mixed Cube/Vector kernel: growing TopK history plus
merge4. Includes the final cross-core merge; not a model replacement.

## Contract

- Contiguous Q[Tq,16,128], K[Tk,1,128], BF16/FP16; Weight[Tq,16] FP32.
- Cumulative query/key lengths [B+1] INT64, starting at zero; TND only.
- Prepared immutable lengths with 0 < Lq <= Lk; TopK2048, block lengths 1.
- Causal/noncausal; 0 <= init <= 512, local >= 0.
- INT32 indices [Tq,1,2048]; optional values of the same shape and Q dtype.
- When return_value=False, the second returned buffer is unspecified: do not read it.
- bind validates inputs before capture; do not mutate cumulative lengths afterwards.
- Prepared objects own scratch/output buffers and are not concurrent-stream safe.

## Data flow

Q8/K512 tiles feed QK matmul. FP32 ReLU scores are multiplied by head weights,
then reduced across 16 heads. Each sorted512 seeds or joins a growing history;
up to three new lists merge with the retained history using merge4. After all
key blocks, contributing cores merge their partial TopK lists in the same kernel.
The rectangular V1 task partition is retained; no V2/V3 selector is embedded.

## Dependencies and execution

Requires CANN9.0.0, Ascend910B, Expert mode and an existing TileLang/AscendNPU-IR
toolchain with vsort32, vmrgsort, vextract_pairs and mixed-kernel support.
The clean base commit alone does not include all measured toolchain patches;
this operator commit does not change or install a compiler backend.

```bash
export TILELANG_ASCEND_MODE=Expert
python test_prefill.py --device 0 --q 183 --k 4096 --out results
```

The test additionally requires the flash_ops wrapper and frozen original
MlpLightningIndexer private OPP package. The operator itself does not call AscendC.
Configure the private OPP/library paths outside this repository.

## AscendC optimized counterpart

[Growing plus merge4 source](https://gitcode.com/qq_51358522/ops-transformer/tree/d004a7f7cbfaff10c0e4a1dcb0781b76d81a6749/docs/experiments/mlp_lightning_indexer_ascendc)
is on branch `fzm/mlp-lightning-indexer-growing-merge4`, fixed commit
`d004a7f7cbfaff10c0e4a1dcb0781b76d81a6749`. It is an independent optimized
comparison, not the frozen original used as the performance denominator and
not a TileLang runtime dependency. Its standalone build contract is documented
there; it is not integrated into the ops-transformer top-level build.

## Validation and performance scope

The measured V1 snapshot passed the 48-shape index-only comparisons and prior
strict value/boundary regressions. On the same device, 44/48 shapes were faster
than frozen AscendC; arithmetic mean of per-shape latency reductions was 12.13%,
maximum 24.53%, worst regression 5.80%. Negative results are retained.

Delivery formatting preserves make_main AST. Host zip now explicitly checks equal
lengths; the diagnostic override was removed from the test. Historical performance
belongs to the measured snapshot, not a new 48-shape run of formatted source.
The complete table and exact toolchain fingerprints are in tilelang-ascend-docs,
`Work/performance/longcat/2026-09-09-mlp-lightning-indexer-tilelang-v1.md`.

Partial workspace remains large (about 681MiB at Tq=Tk=1812, plus 12MiB scores).
Explicit UB budgets are not compiler peak measurements. No whole-model resource
safety or end-to-end benefit is claimed. Host preparation is outside kernel timing.
Keep cross-key UB initialization and the guarded final-merge loop: they preserve
correctness with the measured compiler allocation and signed-division behavior.

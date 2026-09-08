# LightningIndexer QFit V7

This experimental Expert implementation performs the complete LightningIndexer
operation in one mixed Cube/Vector kernel, including final cross-core merging.
It is not registered as a replacement for a model or a global operator package.
Only the QFit V7 single-kernel implementation is included.

## Toolchain dependency

Use the companion AscendNPU-IR branch `fzm/native-packed-sort-qfit-v7`, based on
CANN 9.0.0 revision `428ab8fdab46a879c7349caba4cde705bc8c9bb6`.
The tested companion snapshot is commit
`8a5d8fefca0e4b6a087019245c7fcabb6e0a8f43` in
`https://gitcode.com/qq_51358522/AscendNPU-IR.git`.
It adds native `hivm.hir.vsort32` and `hivm.hir.vmrgsort`, including verifiers,
memory effects, late lowering and the `bishengir_mrgsort.aiv.bc` library.
Build with `BISHENGIR_PUBLISH=ON` and `BISHENGIR_BUILD_MRGSORT_TEMPLATE=ON`.
See that branch's `bishengir/test/Integration/HIVM/MrgSort/README.md`.
Build TileLang against this separately built IR installation; the existing
submodule pin is not changed to an unmerged fork commit. The system hivmc
backend is not modified. An older IR without native packed-sort registration
cannot compile this example and produces an explicit diagnostic.

## Data and computation

- Query: BF16 `[T,16,128]`; weights: BF16 `[T,16]`.
- Paged key: BF16 `[physical_pages,128,1,128]`.
- Metadata: int32 block table `[B,width]`, cumulative query lengths `[B]`,
  key lengths `[B]`, initialization lengths `[B]`, local lengths `[T]`.
- Outputs: int32 indices and BF16 values, both `[T,1,2048]`.

For each query and valid key, compute FP32 dot products, apply ReLU, multiply
each of the 16 heads by its weight, then reduce heads in the exact association
16 → 8 → 4 → 2 → 1. Apply the initialization/local forced-selection rules and
the causal or noncausal validity mask before selecting the largest 2048 scores.
Invalid output entries are `-1`/negative infinity. Equal scores need not have
a unique index order.

```text
one mixed kernel:
  distribute [batch, query group, key block] over available Cube cores
  Cube:
    retain query tile in L1
    load four key pages per 512-key block
    Q × Kᵀ → FP32 L0C → Fixpipe with ReLU → two GM score slots
    exchange ready/free credits with both Vector subcores
  Vector:
    retain per-query TopK and three sorted 512-record cache slots
    for each key block and assigned query:
      load score[16,512]; multiply weight[16,1]; tree-reduce heads
      mask; Sort32 × 16 → merge into 128-record lists → one 512-record list
      every three lists: merge(history2048,512,512,512), retain first2048
      handle one/two remaining lists with two/three-way merging
    write partial TopK to GM; synchronize all launched Vector cores
    merge each query's partial TopK lists, at most four inputs each time
    extract value/index records, cast values, write final outputs
```

`record` is an FP32 score plus an opaque int32 index (eight bytes). Index bits
are not converted to floating-point scores. The vectorization axis remains
the contiguous 512-key dimension; the score layout is `[head,key]`.

## Query-fit dispatch and resources

One/two/five queries per request select query tiles
1/2/5 respectively. Otherwise use tile8 when the query count is divisible by8,
and tile4 otherwise. Explicit tiles1/2/4/5/8 are accepted. Each Vector core
allocates `ceil(query_tile/2)` rows; inactive subcores still exchange credits.

The maximum query tile is8. L0C uses two `[query_tile*16,128]` FP32 buffers,
not a single `[128,512]` buffer. Sorting scratch is reused between stages.
Persistent cache initialization outside the key loop is intentional: it
prevents allocation placement from treating cross-key state as a temporary.
Automatic multi-buffer expansion is disabled in the example.

On the tested Ascend910B2C/CANN9.0.0 toolchain, representative static UB end
addresses for tiles1/2/5 are192576/192832/194848 bytes respectively. Tile5 has
only1760 bytes remaining out of192KiB. These are compiler static-planning
results, not runtime peaks or a guarantee of whole-model resource safety.
This V7 snapshot does not include explicit scale8 rewrite experiments or
experimental TopK-buffer rotation. The existing broadcast-multiply template
already processes eight 64-key chunks with 16 head repeats per chunk.

## Correctness and measurement

The specialization supports uniform queries per request and key lengths up to
200320, with the wrapper's shape/dtype/contiguity checks. Ragged query counts,
NaN/Inf and overflowing arithmetic are not claimed as supported domains.

`bench.py` uses an independent dense CPU oracle for index validity, uniqueness,
TopK threshold, sorted scores and BF16 value tolerances. It also poisons outputs
before graph replay to check that capture executes the complete operator.
Use an available device; the following commands do not choose an idle device
or coordinate device ownership for you.

```sh
export TILELANG_ASCEND_MODE=Expert
python examples/lightning_indexer/bench.py --backend qfit --device 0 \
  --q-per-request 12 --key-length 131072 --samples 200 --rounds 4 \
  --output artifacts/lightning-indexer/t48
python examples/lightning_indexer/bench.py --backend qfit --device 0 \
  --q-per-request 5 --key-length 4097 --actual-key-lengths 0 129 2049 4097 \
  --signed-weights --samples 5 --output artifacts/lightning-indexer/ragged-k
pytest -q testing/npuir/sort_ops/test_packed_sort_exp.py \
  testing/npuir/scalar_ops/test_if_then_else_exp.py
```

For a 12-point B=4 sweep, query counts per request are
1/2/4/5/8/9/12/16/24/32/48/64 (total T=4 through256). Record four rounds of200
samples per point and compare arithmetic means of full single-kernel Event
durations. Preserve source/toolchain versions and raw results; performance
against measurements from a different round/device is only historical context.

Runtime changes accompanying this example preserve asynchronous argument
lifetimes, allocate stream-aware workspaces and obtain the current launch
stream for graph capture. These affect more than this example and require
upstream runtime review; complete operator graph checks do not substitute for
all-platform regression coverage.

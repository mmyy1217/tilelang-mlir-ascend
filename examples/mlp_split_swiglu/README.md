# MlpSplitSwiglu — TileLang Developer

Grouped SwiGLU for contiguous NPU input `x[M, 2H]` and int32 expert token
counts `group_list[E]`. The output is `SiLU(x[:, :H]) * x[:, H:]`, evaluated
in float32 and cast back to BF16/FP16. Only the rows belonging to the selected
expert interval are written at their original offsets; other rows are undefined.
Counts must be nonnegative and their sum must not exceed M. `end=-1` selects
all experts (and resets start to zero), matching the original operator.

```python
from mlp_split_swiglu import mlp_split_swiglu
out = mlp_split_swiglu(x, group_list, local_exp_start=0, local_exp_end=-1)
```

Set `TILELANG_ASCEND_MODE=Developer` before importing/compiling. This is the
NPUIR backend, not the separate TileLang AscendC backend. H must be a positive
multiple of 16, at most 7680. The default kernel uses 40 vector cores, four
rows per tile and a column block no larger than 1024 that divides H. It uses
Developer vector operations without hand-written synchronization. Explicit UB
allocation at block=1024 is 73,728 bytes; this is not the compiler peak.

## Validation and benchmark

`benchmark.py` preserves the 73-configuration synthetic comparison matrix
(decode/prefill capacities, effective rows, widths/dtypes, expert counts and
subsets). It requires torch, torch_npu and the independently installed original
`flash_ops` package registering `torch.ops.custom.mlp_split_swiglu`; that
customer baseline is not distributed here. Run on an idle Ascend 910B device:

```bash
export TILELANG_ASCEND_MODE=Developer
python benchmark.py --output-dir ./measurement
# One case, or resume an interrupted sweep:
python benchmark.py --only 0 --output-dir ./smoke
python benchmark.py --resume --output-dir ./measurement
```

Select the physical device with the environment's supported visibility setting;
the script uses visible device 0. It checks the effective interval against both
FP32 reference and AscendC, and checks untouched rows with a sentinel. Each graph
contains 100 calls, with five warmup calls and three warmup replays; eight rounds
alternate baseline/candidate order. Timings are arithmetic means of graph event
latency per call, not Python dispatch or whole-network latency. Outputs contain
raw timing samples and generated-kernel SHA256. Compile time is recorded
separately. The baseline captures its allocation; the candidate uses a
preallocated output.

## Measured version and dependencies

The 2026-09-09 measurement used Ascend 910B2C, CANN 9.0.0, torch 2.9.0+cpu and
torch_npu 2.9.0.post2+git912882d. The measured kernel source SHA256 was
`eebea16f4a2c80e7e6537b0c5188ec43da6933f3187bfc2fce5c8e5b4f16406f`.
This publication only reformats that implementation. The measurement used a
private modified TileLang tree based on `90be209cdcf1e59eba56fc81359c8dc84e773f56`
and a modified AscendNPU-IR tree based on `428ab8fdab46a879c7349caba4cde705bc8c9bb6`.
Those dirty toolchain trees are not an exact reproducible release pin.

This branch inherits published runtime work from
`fzm/lightning-indexer-qfit-v7` (`a5608dce1351a50582d14817fa3ba112b2827147`).
Its companion published IR lineage is
[AscendNPU-IR fzm/native-packed-sort-qfit-v7](https://gitcode.com/qq_51358522/AscendNPU-IR/tree/fzm/native-packed-sort-qfit-v7),
commit `8a5d8fefca0e4b6a087019245c7fcabb6e0a8f43`.
A clean build of these published dependencies has not been revalidated for this
example. The historical timings must not be labeled measurements of this new
publication commit.

All 73 configurations passed; effective outputs matched AscendC exactly.
Decode cases reduced latency by 33.41–51.07%. Large effective prefill workloads
regressed (worst 27.98%); this is not a universal faster replacement. No
whole-network replacement or benchmark has been performed.

The Chinese optimization history, complete table and integration status live in
[tilelang-ascend-docs](https://github.com/mmyy1217/tilelang-ascend-docs/tree/main/Work/longcat/mlp_split_swiglu).

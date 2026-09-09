import argparse
import hashlib
import json
import os
import statistics
import time
from pathlib import Path
import torch
import torch_npu  # noqa: F401 -- registers NPU support
import flash_ops  # noqa: F401 -- registers the original custom operator
from mlp_split_swiglu import build_kernel

p = argparse.ArgumentParser()
p.add_argument("--resume", action="store_true")
p.add_argument("--only", type=int)
p.add_argument("--output-dir", type=Path, default=Path("mlp_split_swiglu_results"))
args = p.parse_args()
torch.manual_seed(20260909)
torch.set_num_threads(4)
torch.npu.set_device(0)
assert os.environ["TILELANG_ASCEND_MODE"] == "Developer"
root = args.output_dir
(root / "results").mkdir(parents=True, exist_ok=True)
results = []


def save():
    (
        root
        / "results"
        / ("sweep.json" if args.only is None else f"recheck_{args.only}.json")
    ).write_text(json.dumps(results, indent=2))


def run_case(m, h, e, valid, start, end, dtype, block, pattern="random", prefix=3):
    counts = [0] * e
    # Prefix occupies 3 rows for nonzero expert start. Selected tokens are skewed.
    first = prefix if start else 0
    if start:
        counts[0] = first
    counts[start] = valid // 3
    counts[end - 1] += valid - valid // 3
    gc = torch.tensor(counts, dtype=torch.int32)
    g = gc.npu()
    xc = torch.randn((m, 2 * h), dtype=dtype)
    if pattern == "extremes":
        values = torch.tensor(
            [-100.0, -30.0, -10.0, -1.0, -0.0, 0.0, 1.0, 10.0, 30.0, 100.0], dtype=dtype
        )
        xc = (
            values.repeat((m * 2 * h + 9) // 10)[: m * 2 * h]
            .reshape(m, 2 * h)
            .contiguous()
        )
    x = xc.npu()
    y = torch.full((m, h), -777.0, dtype=dtype, device="npu")
    t = time.time()
    k = build_kernel(m, h, e, start, end, str(dtype).split(".")[-1], block)
    compile_s = time.time() - t
    k(x, g, y)
    torch.npu.synchronize()

    def baseline():
        return torch.ops.custom.mlp_split_swiglu(
            x, g, local_exp_start=start, local_exp_end=end
        )

    a = baseline()
    torch.npu.synchronize()
    ref = (
        xc[:, :h].float() / (1 + torch.exp(-xc[:, :h].float())) * xc[:, h:].float()
    ).to(dtype)
    ys = y.cpu()
    ac = a.cpu()
    sl = slice(first, first + valid)
    tol = 0.008 if dtype == torch.bfloat16 else 0.001
    torch.testing.assert_close(ys[sl], ref[sl], rtol=tol, atol=1e-4)
    torch.testing.assert_close(ys[sl], ac[sl], rtol=tol, atol=1e-4)
    assert (ys[:first] == -777).all() and (ys[first + valid :] == -777).all()
    diff = (ys[sl].float() - ac[sl].float()).abs()
    record = dict(
        m=m,
        h=h,
        experts=e,
        valid=valid,
        start=start,
        end=end,
        dtype=str(dtype),
        block=block,
        block_rows=4,
        cores=40,
        pattern=pattern,
        compile_s=compile_s,
        correct=True,
        max_abs_vs_ascendc=float(diff.max()) if valid else 0.0,
        kernel_sha256=hashlib.sha256(k.get_kernel_source()).hexdigest(),
    )
    return record, k, x, g, y, baseline


def capture(fn, repeat=100):
    for _ in range(5):
        fn()
    torch.npu.synchronize()
    graph = torch.npu.NPUGraph()
    with torch.npu.graph(graph):
        for _ in range(repeat):
            fn()
    torch.npu.synchronize()
    for _ in range(3):
        graph.replay()
    torch.npu.synchronize()
    return graph


def measure(graph, repeat=100):
    start = torch.npu.Event(enable_timing=True)
    end = torch.npu.Event(enable_timing=True)
    start.record()
    graph.replay()
    end.record()
    end.synchronize()
    return start.elapsed_time(end) * 1000 / repeat


import gc
from functools import partial

cases = []


def add(family, m, h, e, v, dtype="bfloat16", start=0, end=None, prefix=3):
    cases.append(
        dict(
            family=family,
            m=m,
            h=h,
            e=e,
            v=v,
            dtype=dtype,
            start=start,
            end=e if end is None else end,
            prefix=prefix,
        )
    )


# Real decode capacity, includes 4-row and 160-row grid boundaries.
for v in [
    0,
    1,
    3,
    4,
    5,
    8,
    16,
    17,
    32,
    64,
    127,
    128,
    129,
    159,
    160,
    161,
    256,
    383,
    384,
]:
    add("decode_tokens", 384, 1024, 16, v)
# Real prefill capacities, including saturation and tails.
for m in [24576, 49152]:
    for v in [64, 512, 1024, 2048, 4096, 8192, 16384, m - 1, m]:
        add("prefill_tokens", m, 1024, 32, v)
# Width / dtype portability, wider shapes are synthetic coverage.
for dtype in ["bfloat16", "float16"]:
    for h in [16, 128, 512, 768, 1024, 1536, 2048, 3072, 4096, 7680]:
        add("width_dtype", 512, h, 16, 257, dtype)
# Expert metadata overhead while work remains constant.
for e in [1, 4, 8, 16, 32, 64, 128]:
    add("expert_count", 384, 1024, e, 128)
# Offset, skew, non-full expert interval, and tail.
for start, end, prefix, v in [
    (1, 15, 3, 129),
    (4, 12, 127, 129),
    (15, 16, 3, 17),
    (2, 15, 7, 0),
]:
    add("expert_subset", 384, 1024, 16, v, start=start, end=end, prefix=prefix)
# Isolate capacity from computation; same valid token count.
for m in [128, 384, 2048, 24576, 49152]:
    add("capacity_only", m, 1024, 16, 128)
(root / "results" / "matrix.json").write_text(json.dumps(cases, indent=2))
if (
    args.resume
    and (
        root
        / "results"
        / ("sweep.json" if args.only is None else f"recheck_{args.only}.json")
    ).exists()
):
    results = json.loads(
        (
            root
            / "results"
            / ("sweep.json" if args.only is None else f"recheck_{args.only}.json")
        ).read_text()
    )
done = {r["case_id"] for r in results}
for index, c in enumerate(cases):
    if index in done or (args.only is not None and index != args.only):
        continue
    dtype = getattr(torch, c["dtype"])
    block = min(1024, c["h"])
    while c["h"] % block:
        block -= 16
    rec, k, x, g, y, base = run_case(
        c["m"],
        c["h"],
        c["e"],
        c["v"],
        c["start"],
        c["end"],
        dtype,
        block,
        prefix=c["prefix"],
    )
    rec.update(
        case_id=index,
        family=c["family"],
        prefix=c["prefix"] if c["start"] else 0,
        group_counts=g.cpu().tolist(),
    )
    ga = capture(base)
    gt = capture(partial(k, x, g, y))
    a = []
    t = []
    for rnd in range(8):
        if rnd % 2:
            t.append(measure(gt))
            a.append(measure(ga))
        else:
            a.append(measure(ga))
            t.append(measure(gt))
    av = statistics.mean(a)
    tv = statistics.mean(t)
    rec.update(
        ascendc_us_samples=a,
        tilelang_us_samples=t,
        ascendc_us=av,
        tilelang_us=tv,
        latency_reduction_percent=100 * (1 - tv / av),
        ascendc_cv_percent=100 * statistics.stdev(a) / av,
        tilelang_cv_percent=100 * statistics.stdev(t) / tv,
        repeat=100,
        rounds=8,
    )
    results.append(rec)
    save()
    print(json.dumps(rec), flush=True)
    del ga, gt, base, k, x, g, y
    gc.collect()
    torch.npu.empty_cache()
print("ALL PASS", len(results), flush=True)

"""Strict frozen AscendC comparison. Benchmark only after exact parity."""

import argparse
import json
import pathlib
import time
import torch
import torch_npu  # noqa: F401 - register NPU runtime
import flash_ops  # noqa: F401 - register frozen AscendC comparison operator
from mlp_prefill_expert import MlpPrefillExpert

p = argparse.ArgumentParser()
p.add_argument("--device", type=int, default=0)
p.add_argument("--q", type=int, default=4)
p.add_argument("--k", type=int, default=2048)
p.add_argument("--out", required=True)
p.add_argument("--init", type=int, default=4)
p.add_argument("--local", type=int, default=1024)
p.add_argument("--bench", action="store_true")
p.add_argument("--qlens")
p.add_argument("--klens")
p.add_argument(
    "--kind", choices=["random", "signed", "zero", "ties", "fp16"], default="random"
)
p.add_argument("--noncausal", action="store_true")
a = p.parse_args()
qlens = list(map(int, a.qlens.split(","))) if a.qlens else [a.q]
klens = list(map(int, a.klens.split(","))) if a.klens else [a.k]
a.q, a.k = sum(qlens), sum(klens)
torch.set_num_threads(8)
torch.npu.set_device(a.device)
out = pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=False)
torch.manual_seed(20260909)
q = torch.randn(a.q, 16, 128).bfloat16().npu()
k = torch.randn(a.k, 1, 128).bfloat16().npu()
w = torch.rand(a.q, 16).npu()
if a.kind == "signed":
    w -= 0.5
if a.kind == "zero":
    w.zero_()
if a.kind == "ties":
    q.fill_(1)
    k.fill_(1)
    w.fill_(1)
if a.kind == "fp16":
    q = q.half()
    k = k.half()
qb = torch.tensor([0] + qlens, dtype=torch.int64).cumsum(0).npu()
kb = torch.tensor([0] + klens, dtype=torch.int64).cumsum(0).npu()


def baseline(values=True):
    return torch.ops.custom.mlp_lightning_indexer(
        q,
        k,
        w,
        cur_seq_lengths_query=qb,
        cur_seq_lengths_key=kb,
        layout_query="TND",
        layout_key="TND",
        sparse_count=2048,
        kv_block_len=1,
        q_block_len=1,
        init_num=a.init,
        local_num=a.local,
        sparse_mode=0 if a.noncausal else 3,
        return_value=values,
    )


ref = baseline()
torch.npu.synchronize()
print("BASELINE_READY", flush=True)
t = time.time()
op = MlpPrefillExpert(
    qlens,
    klens,
    q.device,
    init_num=a.init,
    local_num=a.local,
    causal=not a.noncausal,
    dtype=q.dtype,
).bind(q, k, w, qb, kb)
print("COMPILED", time.time() - t, flush=True)
got = op()
torch.npu.synchronize()
report = dict(
    q=a.q,
    k=a.k,
    qlens=qlens,
    klens=klens,
    kind=a.kind,
    device=a.device,
    compile_seconds=time.time() - t,
    indices_exact=torch.equal(got[0], ref[0]),
    values_exact=torch.equal(got[1], ref[1]),
    index_mismatches=int((got[0] != ref[0]).sum()),
    value_mismatches=int((got[1] != ref[1]).sum()),
)
torch.save(
    dict(reference=[x.cpu() for x in ref], actual=[x.cpu() for x in got]),
    out / "outputs.pt",
)
(out / "results.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
if not report["indices_exact"] or not report["values_exact"]:
    raise RuntimeError("strict correctness failed; performance not measured")
if a.bench:
    op = MlpPrefillExpert(
        qlens,
        klens,
        q.device,
        init_num=a.init,
        local_num=a.local,
        causal=not a.noncausal,
        dtype=q.dtype,
        return_value=False,
    ).bind(q, k, w, qb, kb)
    idx_only = op()[0]
    torch.npu.synchronize()
    assert torch.equal(idx_only, ref[0]), "return_value=False index mismatch"
    report["performance_return_value"] = False
    graphs = {}
    for name, fn in (("baseline", lambda: baseline(False)), ("tilelang", op)):
        for _ in range(20):
            fn()
        torch.npu.synchronize()
        graph = torch.npu.NPUGraph()
        with torch.npu.graph(graph):
            fn()
        for _ in range(20):
            graph.replay()
        graphs[name] = graph
    report["samples_us"] = {name: [] for name in graphs}
    for round_id in range(4):
        for name in (
            ("baseline", "tilelang") if round_id % 2 == 0 else ("tilelang", "baseline")
        ):
            events = []
            for _ in range(200):
                s = torch.npu.Event(enable_timing=True)
                e = torch.npu.Event(enable_timing=True)
                s.record()
                graphs[name].replay()
                e.record()
                events.append((s, e))
            torch.npu.synchronize()
            report["samples_us"][name].extend(
                s.elapsed_time(e) * 1000 for s, e in events
            )
    report["mean_us"] = {n: sum(v) / len(v) for n, v in report["samples_us"].items()}
    (out / "results.json").write_text(json.dumps(report, indent=2))
    print(report["mean_us"], flush=True)

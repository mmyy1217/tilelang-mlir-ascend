# Copyright (c) Huawei Technologies Co., Ltd. 2026.
"""Same-input correctness and full-operator Event timing; external msprof ready."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time

import torch
import torch_npu  # noqa: F401


def check_result(q, k, w, table, actual_k, init, local, qp, causal, indices, values):
    """Independent dense CPU oracle, not the implementation's merge algorithm."""
    worst_gap, worst_error = 0.0, 0.0
    for b in range(len(actual_k)):
        length = int(actual_k[b])
        dense = (
            k[table[b, : (length + 127) // 128].long()]
            .reshape(-1, 128)[:length]
            .float()
        )
        dots = torch.matmul(q[b * qp : (b + 1) * qp].float(), dense.T).clamp_min(0)
        scaled = dots * w[b * qp : (b + 1) * qp].float().unsqueeze(-1)
        # Same declared arithmetic association; QK itself is independently CPU computed.
        for n in (8, 4, 2, 1):
            scaled = scaled[:, :n] + scaled[:, n : 2 * n]
        scores = scaled[:, 0]
        for row in range(qp):
            end = max(0, length - qp + row + 1) if causal else length
            score = scores[row].clone()
            score[end:] = -float("inf")
            ini, loc = int(init[b]), int(local[b * qp + row])
            if ini + loc < end:
                score[:ini] = torch.finfo(torch.float32).max / 2
                if loc:
                    score[end - loc : end] = torch.finfo(torch.float32).max / 2
            ids = indices[b * qp + row, 0].long()
            val = values[b * qp + row, 0].float()
            selected = ids[ids >= 0]
            count = min(end, 2048)
            assert len(selected) == count, (b, row, "wrong valid output count")
            assert len(selected.unique()) == count, (b, row, "duplicate index")
            assert bool((selected < end).all()), (b, row, "out of range index")
            assert bool((ids[ids < 0] == -1).all()), (b, row, "bad padding index")
            assert bool(torch.isneginf(val[ids < 0]).all()), (
                b,
                row,
                "bad padding value",
            )
            if not count:
                continue
            chosen = score[selected]
            unselected = score.clone()
            unselected[selected] = -float("inf")
            gap = max(0.0, float(unselected.max() - chosen.min())) if length else 0.0
            worst_gap = max(worst_gap, gap)
            assert gap <= 0.02, (b, row, "missed larger score", gap)
            expected = chosen.to(torch.bfloat16).float()
            got = val[ids >= 0]
            error = float((got - expected).abs().max())
            worst_error = max(worst_error, error)
            assert torch.allclose(got, expected, rtol=0.008, atol=0.02), (b, row, error)
            assert bool((val[:-1] >= val[1:]).all()), (b, row, "scores not descending")
    return {
        "passed": True,
        "max_topk_gap": worst_gap,
        "max_bf16_value_error": worst_error,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=("ascendc", "qfit"), required=True)
    p.add_argument("--q-per-request", type=int, default=12)
    p.add_argument("--query-tile", type=int, choices=(1, 2, 4, 5, 8), default=None)
    p.add_argument("--key-length", type=int, default=131072)
    p.add_argument(
        "--actual-key-lengths",
        type=int,
        nargs=4,
        help="Four runtime lengths, each in [0, --key-length]",
    )
    p.add_argument("--mode", type=int, choices=(0, 3), default=3)
    p.add_argument("--init", type=int, default=4)
    p.add_argument("--local", type=int, default=1024)
    p.add_argument("--signed-weights", action="store_true")
    p.add_argument(
        "--zero-query", action="store_true", help="Exercise exact score ties"
    )
    p.add_argument("--seed", type=int, default=20260907)
    p.add_argument("--device", type=int, default=0)
    p.add_argument("--samples", type=int, default=200)
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--profile-only", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if (
        a.q_per_request <= 0
        or not 0 <= a.key_length <= 1565 * 128
        or a.samples <= 0
        or a.rounds <= 0
    ):
        p.error("require positive query/sample counts and key length in [0, 200320]")
    if a.init < 0 or a.local < 0:
        p.error("init and local must be nonnegative")
    if a.actual_key_lengths is not None and any(
        not 0 <= n <= a.key_length for n in a.actual_key_lengths
    ):
        p.error("actual key lengths must be within the specialization bound")
    a.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    torch.npu.set_device(a.device)
    torch.manual_seed(a.seed)
    qp, total = a.q_per_request, a.q_per_request * 4
    q = torch.randn(total, 16, 128).to(torch.bfloat16)
    k = torch.randn(5408, 128, 1, 128).to(torch.bfloat16)
    w = torch.rand(total, 16).to(torch.bfloat16)
    if a.zero_query:
        q.zero_()
    if a.signed_weights:
        w = (w.float() - 0.5).to(torch.bfloat16)
    bt = (torch.arange(4 * 1565).reshape(4, 1565) % 5408).int()
    aq = (torch.arange(1, 5) * qp).int()
    ak = torch.full((4,), a.key_length, dtype=torch.int32)
    if a.actual_key_lengths is not None:
        ak = torch.tensor(a.actual_key_lengths, dtype=torch.int32)
    init = torch.full((4,), a.init, dtype=torch.int32)
    local = torch.full((total,), a.local, dtype=torch.int32)
    tensors = [x.npu() for x in (q, k, w, aq, ak, bt, init, local)]
    input_hashes = [
        hashlib.sha256(x.contiguous().view(torch.uint8).numpy().tobytes()).hexdigest()
        for x in (q, k, w, aq, ak, bt, init, local)
    ]
    if a.backend == "ascendc":
        import flash_ops  # noqa: F401

        qn, kn, wn, aqn, akn, btn, it, lt = tensors

        def call():
            return torch.ops.custom.npu_lightning_indexer(
                qn,
                kn,
                wn,
                actual_seq_lengths_query=aqn,
                actual_seq_lengths_key=akn,
                block_table=btn,
                init_tensor=it,
                local_tensor=lt,
                layout_query="TND",
                layout_key="PA_BSND",
                sparse_count=2048,
                sparse_mode=a.mode,
                return_value=True,
            )
    else:
        from lightning_indexer_expert import LightningIndexerExpert

        op = LightningIndexerExpert(
            4,
            qp,
            max(1, a.key_length),
            device=f"npu:{a.device}",
            causal=a.mode == 3,
            query_tile=a.query_tile,
        )
        op.validate_inputs(*tensors)
        for name, kernel in [("main", op.main)]:
            if kernel is None:
                continue
            source = kernel.get_kernel_source()
            if isinstance(source, bytes):
                (a.output / (name + ".bin")).write_bytes(source)
            else:
                (a.output / (name + ".npuir")).write_text(source)

        def call():
            return op(*tensors)

    started = time.time()
    result = call()
    torch.npu.synchronize()
    report = {
        "backend": a.backend,
        "T": total,
        "query_tile": op.query_tile if a.backend != "ascendc" else None,
        "key_length": a.key_length,
        "actual_key_lengths": ak.tolist(),
        "device": a.device,
        "torch": torch.__version__,
        "seed": a.seed,
        "mode": a.mode,
        "signed_weights": a.signed_weights,
        "zero_query": a.zero_query,
        "init": a.init,
        "local": a.local,
        "input_sha256": input_hashes,
        "opp": os.environ.get("ASCEND_CUSTOM_OPP_PATH"),
        "started_unix": started,
        "complete_operator": True,
        "kernel_count": 1,
        "sampling": {
            "rounds": a.rounds,
            "samples_per_round": a.samples,
            "eager_warmups": 20,
            "replay_warmups_per_round": 20,
        },
    }
    if not a.profile_only:
        indices, values = [x.cpu() for x in result]
        torch.save({"indices": indices, "values": values}, a.output / "outputs.pt")
        report["oracle"] = check_result(
            q, k, w, bt, ak, init, local, qp, a.mode == 3, indices, values
        )
    for _ in range(20):
        call()
    torch.npu.synchronize()
    if not a.profile_only:
        graph = torch.npu.NPUGraph()
        with torch.npu.graph(graph):
            result = call()
        for _ in range(20):
            graph.replay()
        torch.npu.synchronize()
        # Prove replay executes the complete operator, not an empty capture
        # whose output happened to remain from eager warmup.
        if a.backend != "ascendc":
            result[0].fill_(-123)
            result[1].fill_(float("nan"))
            graph.replay()
            torch.npu.synchronize()
            replay_indices, replay_values = [x.cpu() for x in result]
            report["graph_oracle"] = check_result(
                q,
                k,
                w,
                bt,
                ak,
                init,
                local,
                qp,
                a.mode == 3,
                replay_indices,
                replay_values,
            )
        rounds = []
        for round_index in range(a.rounds):
            if round_index:
                for _ in range(20):
                    graph.replay()
                torch.npu.synchronize()
            events = []
            for _ in range(a.samples):
                start, end = (
                    torch.npu.Event(enable_timing=True),
                    torch.npu.Event(enable_timing=True),
                )
                start.record()
                graph.replay()
                end.record()
                events.append((start, end))
            torch.npu.synchronize()
            round_samples = [s.elapsed_time(e) * 1000 for s, e in events]
            rounds.append(
                {"mean": statistics.mean(round_samples), "samples": round_samples}
            )
        samples = [
            sample for round_result in rounds for sample in round_result["samples"]
        ]
        ordered = sorted(samples)
        report["event_us"] = {
            "median": statistics.median(samples),
            "mean": statistics.mean(samples),
            "p90": ordered[int(0.9 * (len(ordered) - 1))],
            "samples": samples,
            "rounds": rounds,
        }
    report["completed_unix"] = time.time()
    report["loaded_libraries"] = sorted(
        {
            line.split()[-1]
            for line in Path("/proc/self/maps").read_text().splitlines()
            if any(
                name in line
                for name in ("libcust_opapi", "liboptiling", "libtilelang", "libkernel")
            )
        }
    )
    (a.output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "event_us"}), flush=True)
    if "event_us" in report:
        print("FULL_OPERATOR_EVENT_MEAN_US", report["event_us"]["mean"], flush=True)


if __name__ == "__main__":
    main()

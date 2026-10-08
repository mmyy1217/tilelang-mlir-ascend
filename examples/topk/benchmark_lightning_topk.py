# Copyright (c) Tile-AI Corporation.
# Licensed under the MIT License.

"""
Phase 0: Benchmark & Correctness Harness for Lightning Indexer TopK.
Evaluates TileLang TopK kernels (VSort, Chunked) against Golden PyTorch reference
and prepares baseline logs for AscendC comparison.
"""

import argparse
import csv
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("TILELANG_ASCEND_MODE", "Developer")

import torch

try:
    import torch_npu
except ImportError:
    torch_npu = None

import tilelang
from lightning_topk_vsort import lightning_topk_vsort
from lightning_topk_chunked import lightning_topk_chunked
from lightning_topk_hierarchical import HierarchicalTopK


def synchronize_device():
    """Synchronizes host with NPU/GPU execution."""
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elif torch_npu is not None and hasattr(torch.npu, "synchronize"):
        torch.npu.synchronize()


def check_correctness(
    scores: torch.Tensor,
    out_indices: torch.Tensor,
    K: int,
    atol: float = 1e-3,
    rtol: float = 1e-3,
) -> Tuple[bool, str]:
    """
    Verifies that out_indices selects the true top-K values from scores for each row.
    Allows arbitrary ordering within the selected K indices and handles duplicate values.
    """
    M, N = scores.shape
    if out_indices.shape != (M, K):
        return False, f"Shape mismatch: expected {(M, K)}, got {out_indices.shape}"

    scores_cpu = scores.detach().cpu().float()
    indices_cpu = out_indices.detach().cpu().long()

    for r in range(M):
        row_indices = indices_cpu[r]
        # Check indices are within bounds
        if (row_indices < 0).any() or (row_indices >= N).any():
            return False, f"Row {r}: index out of bounds [0, {N})"

        # Check indices are unique
        if len(torch.unique(row_indices)) != K:
            return False, f"Row {r}: duplicate indices found"

        # Gather actual selected values
        actual_vals = scores_cpu[r].gather(0, row_indices)
        actual_sorted, _ = torch.sort(actual_vals, descending=True)

        # Golden top-K values
        golden_vals, _ = torch.topk(scores_cpu[r], k=K, largest=True)
        golden_sorted, _ = torch.sort(golden_vals, descending=True)

        if not torch.allclose(actual_sorted, golden_sorted, atol=atol, rtol=rtol):
            diff = (actual_sorted - golden_sorted).abs().max().item()
            return False, f"Row {r}: value mismatch, max diff = {diff}"

    return True, "PASSED"


def measure_kernel_latency_us(
    compiled_func,
    args: Tuple,
    warmup: int = 10,
    iters: int = 50,
) -> float:
    """Measures steady-state kernel latency in microseconds."""
    synchronize_device()
    for _ in range(warmup):
        compiled_func(*args)
    synchronize_device()

    t_start = time.perf_counter()
    for _ in range(iters):
        compiled_func(*args)
    synchronize_device()
    t_end = time.perf_counter()

    avg_latency_us = ((t_end - t_start) / iters) * 1e6
    return avg_latency_us


def run_benchmark(
    M_list: List[int],
    N_list: List[int],
    K: int,
    dtypes: List[str],
    methods: List[str],
    chunk_size: int = 4096,
    warmup: int = 10,
    iters: int = 50,
    device: str = "npu",
    csv_file: Optional[str] = None,
):
    results = []

    print("=" * 90)
    print(f"Lightning Indexer TopK Benchmark (K={K}, chunk_size={chunk_size})")
    print("=" * 90)
    print(
        f"{'Method':<16} | {'M':<4} | {'N':<8} | {'K':<5} | {'dtype':<8} | "
        f"{'Latency (us)':<12} | {'GB/s':<10} | {'Status':<8}"
    )
    print("-" * 90)

    for dtype_str in dtypes:
        torch_dtype = getattr(torch, dtype_str)
        bytes_per_elem = 2 if dtype_str == "float16" else 4

        for M in M_list:
            for N in N_list:
                if N < K:
                    continue

                scores = torch.randn((M, N), dtype=torch_dtype, device=device)
                out_indices = torch.zeros((M, K), dtype=torch.int32, device=device)

                total_bytes = M * N * bytes_per_elem + M * K * 4

                for method in methods:
                    if method == "vsort":
                        # Full VSort baseline
                        try:
                            func = lightning_topk_vsort(M, N, K, dtype=dtype_str)
                            compiled = tilelang.compile(func, target="npuir")
                            compiled(scores, out_indices)

                            ok, msg = check_correctness(scores, out_indices, K)
                            if not ok:
                                status = f"FAIL: {msg}"
                                lat_us = -1.0
                                bw = 0.0
                            else:
                                lat_us = measure_kernel_latency_us(
                                    compiled, (scores, out_indices), warmup, iters
                                )
                                bw = (total_bytes / 1e9) / (lat_us / 1e6)
                                status = "PASS"
                        except Exception as e:
                            status = f"ERR ({type(e).__name__})"
                            lat_us = -1.0
                            bw = 0.0

                    elif method == "chunked":
                        # Hierarchical / Chunked Streaming TopK
                        if N % chunk_size != 0:
                            continue
                        try:
                            func = lightning_topk_chunked(
                                M, N, K, CHUNK_SIZE=chunk_size, dtype=dtype_str
                            )
                            compiled = tilelang.compile(func, target="npuir")
                            compiled(scores, out_indices)

                            ok, msg = check_correctness(scores, out_indices, K)
                            if not ok:
                                status = f"FAIL: {msg}"
                                lat_us = -1.0
                                bw = 0.0
                            else:
                                lat_us = measure_kernel_latency_us(
                                    compiled, (scores, out_indices), warmup, iters
                                )
                                bw = (total_bytes / 1e9) / (lat_us / 1e6)
                                status = "PASS"
                        except Exception as e:
                            status = f"ERR ({type(e).__name__})"
                            lat_us = -1.0
                            bw = 0.0

                    elif method == "hierarchical":
                        # Multi-Core Binary Tree Hierarchical TopK
                        if N % 8192 != 0 or N < 8192:
                            continue
                        try:
                            engine = HierarchicalTopK(
                                N, K, B=8192, dtype=dtype_str, device=scores.device
                            )
                            val, idx = engine(scores)
                            synchronize_device()

                            ok, msg = check_correctness(scores, idx, K)
                            if not ok:
                                status = f"FAIL: {msg}"
                                lat_us = -1.0
                                bw = 0.0
                            else:
                                synchronize_device()
                                for _ in range(warmup):
                                    val, idx = engine(scores)
                                synchronize_device()

                                t_start = time.perf_counter()
                                for _ in range(iters):
                                    val, idx = engine(scores)
                                synchronize_device()
                                t_end = time.perf_counter()

                                lat_us = ((t_end - t_start) / iters) * 1e6
                                bw = (total_bytes / 1e9) / (lat_us / 1e6)
                                status = "PASS"
                        except Exception as e:
                            status = f"ERR ({type(e).__name__})"
                            lat_us = -1.0
                            bw = 0.0

                    elif method == "torch":
                        # PyTorch baseline
                        try:
                            synchronize_device()
                            for _ in range(warmup):
                                _, idx = torch.topk(scores, k=K, dim=-1, largest=True, sorted=False)
                            synchronize_device()

                            t_start = time.perf_counter()
                            for _ in range(iters):
                                _, idx = torch.topk(scores, k=K, dim=-1, largest=True, sorted=False)
                            synchronize_device()
                            t_end = time.perf_counter()

                            lat_us = ((t_end - t_start) / iters) * 1e6
                            bw = (total_bytes / 1e9) / (lat_us / 1e6)
                            status = "PASS"
                        except Exception as e:
                            status = f"ERR ({type(e).__name__})"
                            lat_us = -1.0
                            bw = 0.0
                    else:
                        continue

                    print(
                        f"{method:<16} | {M:<4} | {N:<8} | {K:<5} | {dtype_str:<8} | "
                        f"{lat_us:<12.2f} | {bw:<10.2f} | {status:<8}"
                    )
                    results.append({
                        "method": method,
                        "M": M,
                        "N": N,
                        "K": K,
                        "dtype": dtype_str,
                        "latency_us": lat_us,
                        "gb_per_sec": bw,
                        "status": status,
                    })

    if csv_file:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults written to {csv_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Lightning Indexer TopK Benchmark")
    parser.add_argument("--m", type=int, nargs="+", default=[1], help="Query row counts (M)")
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        default=[8192, 16384, 32768, 65536, 131072],
        help="Sequence lengths (N)",
    )
    parser.add_argument("--k", type=int, default=2048, help="Top-K size (default 2048)")
    parser.add_argument(
        "--dtypes",
        type=str,
        nargs="+",
        default=["float16"],
        choices=["float16", "float32"],
        help="Data types to test",
    )
    parser.add_argument(
        "--methods",
        type=str,
        nargs="+",
        default=["vsort", "chunked", "hierarchical", "torch"],
        help="Methods to benchmark (vsort, chunked, hierarchical, torch)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=4096,
        help="Chunk size for chunked topk (default 4096)",
    )
    parser.add_argument("--warmup", type=int, default=10, help="Warmup iterations")
    parser.add_argument("--iters", type=int, default=50, help="Benchmark iterations")
    parser.add_argument("--device", type=str, default="npu", help="Target device (npu or cuda)")
    parser.add_argument("--csv-out", type=str, default="benchmark_topk_results.csv", help="CSV output path")
    args = parser.parse_args()

    run_benchmark(
        M_list=args.m,
        N_list=args.n,
        K=args.k,
        dtypes=args.dtypes,
        methods=args.methods,
        chunk_size=args.chunk_size,
        warmup=args.warmup,
        iters=args.iters,
        device=args.device,
        csv_file=args.csv_out,
    )


if __name__ == "__main__":
    main()

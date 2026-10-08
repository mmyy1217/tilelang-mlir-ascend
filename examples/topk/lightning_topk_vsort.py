# Copyright (c) Tile-AI Corporation.
# Licensed under the MIT License.

"""
Phase 1: VSort Baseline for Lightning Indexer TopK.
Takes scores of shape [M, N] and outputs top-K indices along the last dimension.
"""

from typing import Optional, Tuple
import os
import torch

os.environ.setdefault("TILELANG_ASCEND_MODE", "Developer")

import tilelang
import tilelang.language as T


def lightning_topk_vsort(
    M: int,
    N: int,
    K: int = 2048,
    dtype: str = "float16",
    return_values: bool = False,
):
    """
    Constructs a TileLang prim_func for Lightning Indexer TopK using full VSort.

    Args:
        M: Number of query rows (tokens).
        N: KV sequence length.
        K: Top-K elements to select along last dimension (default 2048).
        dtype: Data type of scores ('float16' or 'float32').
        return_values: Whether to also output top-K values.
    """
    assert N >= K, f"N ({N}) must be >= K ({K})"

    if return_values:
        @T.prim_func
        def topk_vsort_kernel(
            scores: T.Tensor((M, N), dtype),
            out_indices: T.Tensor((M, K), "int32"),
            out_values: T.Tensor((M, K), dtype),
        ):
            with T.Kernel(M, is_npu=True) as (cid, _):
                src_ub = T.alloc_shared((1, N), dtype)
                val_ub = T.alloc_shared((1, N), dtype)
                idx_ub = T.alloc_shared((1, N), "int32")

                # Copy 1 row of scores from GM to UB
                T.copy(scores[cid : cid + 1, :], src_ub)

                # Sort along tail axis descending
                T.vsort(src_ub, val_ub, idx_ub, descending=True, sort_axis=-1)

                # Copy top K results back to GM
                T.copy(idx_ub[0:1, 0:K], out_indices[cid : cid + 1, 0:K])
                T.copy(val_ub[0:1, 0:K], out_values[cid : cid + 1, 0:K])

        return topk_vsort_kernel
    else:
        @T.prim_func
        def topk_vsort_kernel(
            scores: T.Tensor((M, N), dtype),
            out_indices: T.Tensor((M, K), "int32"),
        ):
            with T.Kernel(M, is_npu=True) as (cid, _):
                src_ub = T.alloc_shared((1, N), dtype)
                val_ub = T.alloc_shared((1, N), dtype)
                idx_ub = T.alloc_shared((1, N), "int32")

                # Copy 1 row of scores from GM to UB
                T.copy(scores[cid : cid + 1, :], src_ub)

                # Sort along tail axis descending
                T.vsort(src_ub, val_ub, idx_ub, descending=True, sort_axis=-1)

                # Copy top K indices back to GM
                T.copy(idx_ub[0:1, 0:K], out_indices[cid : cid + 1, 0:K])

        return topk_vsort_kernel


def compile_vsort_topk(
    M: int,
    N: int,
    K: int = 2048,
    dtype: str = "float16",
    return_values: bool = False,
):
    """Compiles the VSort TopK kernel for NPU."""
    func = lightning_topk_vsort(M, N, K, dtype, return_values)
    return tilelang.compile(func, target="npuir")

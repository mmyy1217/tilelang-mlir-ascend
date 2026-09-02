# Copyright (c) Tile-AI Corporation.
# Licensed under the MIT License.

"""
Phase 2: Hierarchical / Chunked Streaming TopK for Lightning Indexer.
Takes scores of shape [M, N] and outputs top-K indices along the last dimension.
Breaks large N into chunks of size CHUNK_SIZE, performs local VSort, and maintains
running top-K candidates in UB.
"""

from typing import Optional
import os
import torch

os.environ.setdefault("TILELANG_ASCEND_MODE", "Developer")

import tilelang
import tilelang.language as T


def lightning_topk_chunked(
    M: int,
    N: int,
    K: int = 2048,
    CHUNK_SIZE: int = 4096,
    dtype: str = "float16",
    return_values: bool = False,
):
    """
    Constructs a TileLang prim_func for Chunked Streaming TopK.

    Args:
        M: Number of query rows (tokens).
        N: KV sequence length (must be multiple of CHUNK_SIZE for fixed tiling).
        K: Top-K elements to select along last dimension (default 2048).
        CHUNK_SIZE: Chunk size for local sorting in UB (default 4096, >= K).
        dtype: Data type of scores ('float16' or 'float32').
        return_values: Whether to also output top-K values.
    """
    assert CHUNK_SIZE >= K, f"CHUNK_SIZE ({CHUNK_SIZE}) must be >= K ({K})"
    assert N % CHUNK_SIZE == 0, f"N ({N}) must be divisible by CHUNK_SIZE ({CHUNK_SIZE})"
    num_chunks = N // CHUNK_SIZE
    MERGE_SIZE = 2 * K

    if num_chunks == 1:
        # Single chunk degenerates to direct VSort
        if return_values:
            @T.prim_func
            def topk_chunked_single(
                scores: T.Tensor((M, N), dtype),
                out_indices: T.Tensor((M, K), "int32"),
                out_values: T.Tensor((M, K), dtype),
            ):
                with T.Kernel(M, is_npu=True) as (cid, _):
                    src_ub = T.alloc_shared((1, N), dtype)
                    val_ub = T.alloc_shared((1, N), dtype)
                    idx_ub = T.alloc_shared((1, N), "int32")

                    T.copy(scores[cid : cid + 1, :], src_ub)
                    T.vsort(src_ub, val_ub, idx_ub, descending=True, sort_axis=-1)
                    T.copy(idx_ub[0:1, 0:K], out_indices[cid : cid + 1, 0:K])
                    T.copy(val_ub[0:1, 0:K], out_values[cid : cid + 1, 0:K])

            return topk_chunked_single
        else:
            @T.prim_func
            def topk_chunked_single(
                scores: T.Tensor((M, N), dtype),
                out_indices: T.Tensor((M, K), "int32"),
            ):
                with T.Kernel(M, is_npu=True) as (cid, _):
                    src_ub = T.alloc_shared((1, N), dtype)
                    val_ub = T.alloc_shared((1, N), dtype)
                    idx_ub = T.alloc_shared((1, N), "int32")

                    T.copy(scores[cid : cid + 1, :], src_ub)
                    T.vsort(src_ub, val_ub, idx_ub, descending=True, sort_axis=-1)
                    T.copy(idx_ub[0:1, 0:K], out_indices[cid : cid + 1, 0:K])

            return topk_chunked_single

    # Multi-chunk streaming top-K
    if return_values:
        @T.prim_func
        def topk_chunked_multi(
            scores: T.Tensor((M, N), dtype),
            out_indices: T.Tensor((M, K), "int32"),
            out_values: T.Tensor((M, K), dtype),
        ):
            with T.Kernel(M, is_npu=True) as (cid, _):
                # Working buffers for chunk processing
                chunk_ub = T.alloc_shared((1, CHUNK_SIZE), dtype)
                chunk_val = T.alloc_shared((1, CHUNK_SIZE), dtype)
                chunk_idx = T.alloc_shared((1, CHUNK_SIZE), "int32")

                # Running top-K candidates
                cur_topk_val = T.alloc_shared((1, K), dtype)
                cur_topk_idx = T.alloc_shared((1, K), "int32")

                # Buffers for merging candidates: size 2*K
                merged_val = T.alloc_shared((1, MERGE_SIZE), dtype)
                merged_idx = T.alloc_shared((1, MERGE_SIZE), "int32")
                sorted_merged_val = T.alloc_shared((1, MERGE_SIZE), dtype)
                merged_perm = T.alloc_shared((1, MERGE_SIZE), "int32")

                # --- Process Chunk 0 ---
                T.copy(scores[cid : cid + 1, 0 : CHUNK_SIZE], chunk_ub)
                T.vsort(chunk_ub, chunk_val, chunk_idx, descending=True, sort_axis=-1)
                for k in T.Parallel(K):
                    cur_topk_val[0, k] = chunk_val[0, k]
                    cur_topk_idx[0, k] = chunk_idx[0, k]

                # --- Process Subsequent Chunks ---
                for p in T.serial(1, num_chunks):
                    T.copy(
                        scores[cid : cid + 1, p * CHUNK_SIZE : (p + 1) * CHUNK_SIZE],
                        chunk_ub,
                    )
                    T.vsort(chunk_ub, chunk_val, chunk_idx, descending=True, sort_axis=-1)

                    # Pack running top-K and new chunk top-K into merge buffer
                    for k in T.Parallel(K):
                        merged_val[0, k] = cur_topk_val[0, k]
                        merged_idx[0, k] = cur_topk_idx[0, k]
                        merged_val[0, K + k] = chunk_val[0, k]
                        merged_idx[0, K + k] = chunk_idx[0, k] + p * CHUNK_SIZE

                    # Sort the 2*K candidates
                    T.vsort(
                        merged_val,
                        sorted_merged_val,
                        merged_perm,
                        descending=True,
                        sort_axis=-1,
                    )

                    # Select new top-K
                    for k in T.Parallel(K):
                        cur_topk_val[0, k] = sorted_merged_val[0, k]
                        cur_topk_idx[0, k] = merged_idx[0, merged_perm[0, k]]

                # Write final top-K back to GM
                T.copy(cur_topk_idx[0:1, 0:K], out_indices[cid : cid + 1, 0:K])
                T.copy(cur_topk_val[0:1, 0:K], out_values[cid : cid + 1, 0:K])

        return topk_chunked_multi
    else:
        @T.prim_func
        def topk_chunked_multi(
            scores: T.Tensor((M, N), dtype),
            out_indices: T.Tensor((M, K), "int32"),
        ):
            with T.Kernel(M, is_npu=True) as (cid, _):
                chunk_ub = T.alloc_shared((1, CHUNK_SIZE), dtype)
                chunk_val = T.alloc_shared((1, CHUNK_SIZE), dtype)
                chunk_idx = T.alloc_shared((1, CHUNK_SIZE), "int32")

                cur_topk_val = T.alloc_shared((1, K), dtype)
                cur_topk_idx = T.alloc_shared((1, K), "int32")

                merged_val = T.alloc_shared((1, MERGE_SIZE), dtype)
                merged_idx = T.alloc_shared((1, MERGE_SIZE), "int32")
                sorted_merged_val = T.alloc_shared((1, MERGE_SIZE), dtype)
                merged_perm = T.alloc_shared((1, MERGE_SIZE), "int32")

                # --- Process Chunk 0 ---
                T.copy(scores[cid : cid + 1, 0 : CHUNK_SIZE], chunk_ub)
                T.vsort(chunk_ub, chunk_val, chunk_idx, descending=True, sort_axis=-1)
                for k in T.Parallel(K):
                    cur_topk_val[0, k] = chunk_val[0, k]
                    cur_topk_idx[0, k] = chunk_idx[0, k]

                # --- Process Subsequent Chunks ---
                for p in T.serial(1, num_chunks):
                    T.copy(
                        scores[cid : cid + 1, p * CHUNK_SIZE : (p + 1) * CHUNK_SIZE],
                        chunk_ub,
                    )
                    T.vsort(chunk_ub, chunk_val, chunk_idx, descending=True, sort_axis=-1)

                    for k in T.Parallel(K):
                        merged_val[0, k] = cur_topk_val[0, k]
                        merged_idx[0, k] = cur_topk_idx[0, k]
                        merged_val[0, K + k] = chunk_val[0, k]
                        merged_idx[0, K + k] = chunk_idx[0, k] + p * CHUNK_SIZE

                    T.vsort(
                        merged_val,
                        sorted_merged_val,
                        merged_perm,
                        descending=True,
                        sort_axis=-1,
                    )

                    for k in T.Parallel(K):
                        cur_topk_val[0, k] = sorted_merged_val[0, k]
                        cur_topk_idx[0, k] = merged_idx[0, merged_perm[0, k]]

                # Write final top-K back to GM
                T.copy(cur_topk_idx[0:1, 0:K], out_indices[cid : cid + 1, 0:K])

        return topk_chunked_multi


def compile_chunked_topk(
    M: int,
    N: int,
    K: int = 2048,
    CHUNK_SIZE: int = 4096,
    dtype: str = "float16",
    return_values: bool = False,
):
    """Compiles the Chunked Streaming TopK kernel for NPU."""
    func = lightning_topk_chunked(M, N, K, CHUNK_SIZE, dtype, return_values)
    return tilelang.compile(func, target="npuir")

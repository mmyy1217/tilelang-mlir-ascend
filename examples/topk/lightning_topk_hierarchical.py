# Copyright (c) Tile-AI Corporation.
# Licensed under the MIT License.

"""
Phase 4: Hierarchical Multi-Core Parallel TopK for Lightning Indexer.
Partitions sequence across AI Cores in parallel for local Top-K extraction,
followed by a binary tree reduction to produce exact global Top-K.
"""

from typing import Optional, Tuple
import os
import time
import torch

os.environ.setdefault("TILELANG_ASCEND_MODE", "Developer")

import tilelang
import tilelang.language as T


def make_stage1_kernel(P: int, B: int, K: int, dtype: str = "float16"):
    """
    Stage 1: P cores in parallel extract local top-K from blocks of size B.
    """
    @T.prim_func
    def stage1_kernel(
        scores_blocks: T.Tensor((P, B), dtype),
        out_val: T.Tensor((P, K), dtype),
        out_idx: T.Tensor((P, K), "int32"),
    ):
        with T.Kernel(P, is_npu=True) as (bid, _):
            src_ub = T.alloc_shared((1, B), dtype)
            val_ub = T.alloc_shared((1, B), dtype)
            idx_ub = T.alloc_shared((1, B), "int32")

            T.copy(scores_blocks[bid : bid + 1, :], src_ub)
            T.vsort(src_ub, val_ub, idx_ub, descending=True, sort_axis=-1)
            T.copy(val_ub[0:1, 0:K], out_val[bid : bid + 1, 0:K])
            T.copy(idx_ub[0:1, 0:K], out_idx[bid : bid + 1, 0:K])

    return stage1_kernel


def make_merge_kernel(num_pairs: int, K: int, dtype: str = "float16"):
    """
    Binary Merge: num_pairs cores in parallel merge 2 candidate blocks (2 * K -> K).
    """
    MERGE_IN = 2 * K

    @T.prim_func
    def merge_kernel(
        cand_val: T.Tensor((num_pairs, MERGE_IN), dtype),
        cand_idx: T.Tensor((num_pairs, MERGE_IN), "int32"),
        out_val: T.Tensor((num_pairs, K), dtype),
        out_idx: T.Tensor((num_pairs, K), "int32"),
    ):
        with T.Kernel(num_pairs, is_npu=True) as (bid, _):
            val_ub = T.alloc_shared((1, MERGE_IN), dtype)
            sorted_val_ub = T.alloc_shared((1, MERGE_IN), dtype)
            perm_ub = T.alloc_shared((1, MERGE_IN), "int32")

            idx_ub = T.alloc_shared((1, MERGE_IN), "int32")
            res_idx_ub = T.alloc_shared((1, K), "int32")

            T.copy(cand_val[bid : bid + 1, :], val_ub)
            T.copy(cand_idx[bid : bid + 1, :], idx_ub)

            T.vsort(val_ub, sorted_val_ub, perm_ub, descending=True, sort_axis=-1)
            for k in T.Parallel(K):
                res_idx_ub[0, k] = idx_ub[0, perm_ub[0, k]]

            T.copy(sorted_val_ub[0:1, 0:K], out_val[bid : bid + 1, 0:K])
            T.copy(res_idx_ub[0:1, 0:K], out_idx[bid : bid + 1, 0:K])

    return merge_kernel


class HierarchicalTopK:
    """
    Hierarchical Multi-Core Top-K Engine on Ascend NPU.
    Manages precompiled kernels and execution for arbitrary N and K.
    """
    def __init__(self, N: int, K: int = 2048, B: int = 8192, dtype: str = "float16", device: str = "npu:0"):
        self.N = N
        self.K = K
        self.B = B
        self.dtype = dtype
        self.device = device

        assert N % B == 0, f"N ({N}) must be divisible by B ({B})"
        self.P = N // B
        assert (self.P & (self.P - 1)) == 0, f"P ({self.P}) must be a power of 2"

        # Compile Stage 1
        s1_func = make_stage1_kernel(self.P, self.B, self.K, dtype=dtype)
        self.compiled_s1 = tilelang.compile(s1_func, target="npuir")

        # Compile reduction tree levels
        self.merge_kernels = []
        curr_p = self.P
        while curr_p > 1:
            num_pairs = curr_p // 2
            m_func = make_merge_kernel(num_pairs, self.K, dtype=dtype)
            compiled_m = tilelang.compile(m_func, target="npuir")
            self.merge_kernels.append((num_pairs, compiled_m))
            curr_p = num_pairs

        # Preallocate buffers
        self.offsets = (torch.arange(self.P, dtype=torch.int32, device=device) * self.B).unsqueeze(1)
        torch_dtype = getattr(torch, dtype)
        self.s1_val = torch.zeros((self.P, self.K), dtype=torch_dtype, device=device)
        self.s1_idx = torch.zeros((self.P, self.K), dtype=torch.int32, device=device)

        self.tree_bufs = []
        for num_pairs, _ in self.merge_kernels:
            v = torch.zeros((num_pairs, self.K), dtype=torch_dtype, device=device)
            i = torch.zeros((num_pairs, self.K), dtype=torch.int32, device=device)
            self.tree_bufs.append((v, i))

    def __call__(self, scores: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Executes hierarchical TopK on input scores tensor (1, N).
        Returns (values, indices) of shape (1, K).
        """
        scores_blocks = scores.view(self.P, self.B)
        self.compiled_s1(scores_blocks, self.s1_val, self.s1_idx)
        s1_idx_global = self.s1_idx + self.offsets

        prev_val = self.s1_val
        prev_idx = s1_idx_global

        MERGE_IN = 2 * self.K
        for idx, (num_pairs, kernel) in enumerate(self.merge_kernels):
            out_v, out_i = self.tree_bufs[idx]
            cand_v = prev_val.view(num_pairs, MERGE_IN)
            cand_i = prev_idx.view(num_pairs, MERGE_IN)
            kernel(cand_v, cand_i, out_v, out_i)
            prev_val = out_v
            prev_idx = out_i

        if not self.merge_kernels:
            return self.s1_val.view(1, self.K), s1_idx_global.view(1, self.K)

        final_val, final_idx = self.tree_bufs[-1]
        return final_val.view(1, self.K), final_idx.view(1, self.K)

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
            T.gather(idx_ub, res_idx_ub, perm_ub[0:1, 0:K])

            T.copy(sorted_val_ub[0:1, 0:K], out_val[bid : bid + 1, 0:K])
            T.copy(res_idx_ub[0:1, 0:K], out_idx[bid : bid + 1, 0:K])

    return merge_kernel


class HierarchicalTopK:
    """
    Hierarchical Multi-Core Top-K Engine on Ascend NPU.
    Manages precompiled kernels and execution for arbitrary N and K.
    """
    def __init__(self, N: int, K: int = 2048, B: int = 8192, dtype: str = "float16", device: str = "npu:0", strategy: str = "auto"):
        self.N = N
        self.K = K
        self.B = B
        self.dtype = dtype
        self.device = device
        self.strategy = strategy

        assert N % B == 0, f"N ({N}) must be divisible by B ({B})"
        self.P = N // B
        assert (self.P & (self.P - 1)) == 0, f"P ({self.P}) must be a power of 2"

        # Decide local candidate extraction size
        if strategy == "tree":
            self.k_local = K
        else: # "auto" or "twostage"
            if self.P > 1 and (self.P * K) > 4096:
                self.k_local = min(K, max(128, 4096 // self.P))
            else:
                self.k_local = K

        # Compile Stage 1
        s1_func = make_stage1_kernel(self.P, self.B, self.k_local, dtype=dtype)
        self.compiled_s1 = tilelang.compile(s1_func, target="npuir")

        # Compile reduction tree levels
        self.merge_kernels = []
        curr_p = self.P
        total_cand = curr_p * self.k_local
        torch_dtype = getattr(torch, dtype)

        if curr_p > 1 and total_cand <= 4096:
            # Fast-path: single-stage reduction when total candidate pool fits in UB (<= 4096 elements)
            @T.prim_func
            def single_merge_kernel(
                cand_val: T.Tensor((1, total_cand), dtype),
                cand_idx: T.Tensor((1, total_cand), "int32"),
                out_val: T.Tensor((1, self.K), dtype),
                out_idx: T.Tensor((1, self.K), "int32"),
            ):
                with T.Kernel(1, is_npu=True) as (cid, _):
                    val_ub = T.alloc_shared((1, total_cand), dtype)
                    sorted_val_ub = T.alloc_shared((1, total_cand), dtype)
                    perm_ub = T.alloc_shared((1, total_cand), "int32")
                    idx_ub = T.alloc_shared((1, total_cand), "int32")
                    res_idx_ub = T.alloc_shared((1, self.K), "int32")

                    T.copy(cand_val[0:1, :], val_ub)
                    T.copy(cand_idx[0:1, :], idx_ub)
                    T.vsort(val_ub, sorted_val_ub, perm_ub, descending=True, sort_axis=-1)
                    T.gather(idx_ub, res_idx_ub, perm_ub[0:1, 0 : self.K])
                    T.copy(sorted_val_ub[0:1, 0 : self.K], out_val[0:1, 0 : self.K])
                    T.copy(res_idx_ub[0:1, 0 : self.K], out_idx[0:1, 0 : self.K])

            compiled_single = tilelang.compile(single_merge_kernel, target="npuir")
            self.merge_kernels.append(("single", total_cand, compiled_single))
            self.tree_bufs = [(
                torch.zeros((1, self.K), dtype=torch_dtype, device=device),
                torch.zeros((1, self.K), dtype=torch.int32, device=device),
            )]
        else:
            self.tree_bufs = []
            while curr_p > 1:
                num_pairs = curr_p // 2
                m_func = make_merge_kernel(num_pairs, self.K, dtype=dtype)
                compiled_m = tilelang.compile(m_func, target="npuir")
                self.merge_kernels.append(("binary", num_pairs, compiled_m))
                v = torch.zeros((num_pairs, self.K), dtype=torch_dtype, device=device)
                i = torch.zeros((num_pairs, self.K), dtype=torch.int32, device=device)
                self.tree_bufs.append((v, i))
                curr_p = num_pairs

        # Preallocate buffers
        self.offsets = (torch.arange(self.P, dtype=torch.int32, device=device) * self.B).unsqueeze(1)
        self.s1_val = torch.zeros((self.P, self.k_local), dtype=torch_dtype, device=device)
        self.s1_idx = torch.zeros((self.P, self.k_local), dtype=torch.int32, device=device)
        self.s1_idx_global = torch.zeros((self.P, self.k_local), dtype=torch.int32, device=device)

        if self.merge_kernels and self.merge_kernels[0][0] == "single":
            _, total_cand, _ = self.merge_kernels[0]
            self.single_cand_v = self.s1_val.view(1, total_cand)
            self.single_cand_i = self.s1_idx_global.view(1, total_cand)

    def __call__(self, scores: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Executes hierarchical TopK on input scores tensor (1, N).
        Returns (values, indices) of shape (1, K).
        """
        scores_blocks = scores.view(self.P, self.B)
        self.compiled_s1(scores_blocks, self.s1_val, self.s1_idx)
        torch.add(self.s1_idx, self.offsets, out=self.s1_idx_global)

        if not self.merge_kernels:
            return self.s1_val.view(1, self.K), self.s1_idx_global.view(1, self.K)

        if self.merge_kernels[0][0] == "single":
            _, _, kernel = self.merge_kernels[0]
            out_v, out_i = self.tree_bufs[0]
            kernel(self.single_cand_v, self.single_cand_i, out_v, out_i)
            return out_v.view(1, self.K), out_i.view(1, self.K)

        prev_val = self.s1_val
        prev_idx = self.s1_idx_global

        MERGE_IN = 2 * self.K
        for idx, (_, num_pairs, kernel) in enumerate(self.merge_kernels):
            out_v, out_i = self.tree_bufs[idx]
            cand_v = prev_val.view(num_pairs, MERGE_IN)
            cand_i = prev_idx.view(num_pairs, MERGE_IN)
            kernel(cand_v, cand_i, out_v, out_i)
            prev_val = out_v
            prev_idx = out_i

        final_val, final_idx = self.tree_bufs[-1]
        return final_val.view(1, self.K), final_idx.view(1, self.K)

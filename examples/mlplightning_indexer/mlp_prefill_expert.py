# Copyright (c) Huawei Technologies Co., Ltd. 2026.
"""Prefill TND Expert growing+merge4. One mixed kernel; no baseline dispatch."""

import os

import torch

import tilelang
import tilelang.language as T
from tilelang.utils import NPUUtils


def make_main(
    query_lengths,
    max_q,
    max_k,
    total_key,
    cores,
    causal=True,
    query_tile=8,
    init_num=4,
    local_num=1024,
    return_value=True,
    dtype="bfloat16",
):
    batch = len(query_lengths)
    total_q = sum(query_lengths)
    qgroups = (max_q + query_tile - 1) // query_tile
    vector_rows = (query_tile + 1) // 2
    groups = batch * qgroups
    ktiles = (max_k + 511) // 512
    tasks = groups * ktiles
    cache_slots = 3
    scratch_size = 7168

    @T.prim_func
    def main(
        Query: T.Tensor((total_q * 16, 128), dtype),
        Key: T.Tensor((total_key, 128), dtype),
        Weight: T.Tensor((total_q * 16, 1), "float32"),
        ActualQ: T.Tensor((batch + 1,), "int64"),
        ActualK: T.Tensor((batch + 1,), "int64"),
        Scores: T.Tensor((cores, 2, 128, 512), "float32"),
        Partial: T.Tensor((groups, cores, query_tile, 4096), "float32"),
        IndicesOut: T.Tensor((total_q, 1, 2048), "int32"),
        ValuesOut: T.Tensor((total_q, 1, 2048), dtype),
    ):
        with T.Kernel(cores, is_npu=True) as (cid, vid):
            task_begin = tasks * cid // cores
            task_end = tasks * (cid + 1) // cores
            group_begin = task_begin // ktiles
            group_end = (task_end + ktiles - 1) // ktiles

            with T.Scope("Cube"):
                q_l1 = T.alloc_L1((query_tile * 16, 128), dtype)
                k_l1 = T.alloc_L1((2, 128, 128), dtype)
                c_l0 = T.alloc_L0C((2, query_tile * 16, 128), "float32")
                for group in T.serial(group_begin, group_end):
                    b = group // qgroups
                    qstart = group % qgroups * query_tile
                    qprefix = T.cast(ActualQ[b], "int32")
                    kprefix = T.cast(ActualK[b], "int32")
                    klen = T.cast(ActualK[b + 1] - ActualK[b], "int32")
                    qlen = T.cast(ActualQ[b + 1], "int32") - qprefix
                    nq = T.min(query_tile, qlen - qstart)
                    begin = T.max(task_begin - group * ktiles, 0)
                    end = T.min(task_end - group * ktiles, ktiles)
                    if nq > 0:
                        T.copy(
                            Query[
                                (qprefix + qstart) * 16 : (qprefix + qstart + nq) * 16,
                                :,
                            ],
                            q_l1[: nq * 16, :],
                        )
                        for kb in T.serial(begin, end):
                            if kb * 512 < klen:
                                with T.rs("PIPE_FIX"):
                                    T.sync_block_wait(2 + kb % 2)
                                for page in T.serial(4):
                                    logical_page = kb * 4 + page
                                    nk = T.max(0, T.min(128, klen - logical_page * 128))
                                    if nk > 0:
                                        T.copy(
                                            Key[
                                                kprefix + logical_page * 128 : kprefix
                                                + logical_page * 128
                                                + nk,
                                                :,
                                            ],
                                            k_l1[page % 2, :nk, :],
                                        )
                                    T.gemm(
                                        q_l1,
                                        k_l1[page % 2, :, :],
                                        c_l0[page % 2, :, :],
                                        initC=True,
                                        b_transpose=True,
                                        size=[nq * 16, 128, 128],
                                    )
                                    with T.rs("PIPE_FIX"):
                                        T.store_fixpipe(
                                            c_l0[page % 2, 0, 0],
                                            Scores[cid, kb % 2, 0, page * 128],
                                            size=[nq * 16, 128],
                                            enable_nz2nd=True,
                                            pre_relu_mode="relu",
                                        )
                                with T.rs("PIPE_FIX"):
                                    T.sync_block_set(kb % 2)
                # Both slots have a free credit, including an unused slot.
                with T.rs("PIPE_FIX"):
                    T.sync_block_wait(2)
                    T.sync_block_wait(3)

            with T.Scope("Vector"):
                score = T.alloc_ub((16, 512), "float32")
                weights = T.alloc_ub((vector_rows * 16, 1), "float32")
                history = T.alloc_ub((vector_rows, 4096), "float32")
                cache = T.alloc_ub((vector_rows, cache_slots, 1024), "float32")
                scratch = T.alloc_ub((scratch_size,), "float32")
                indices = T.alloc_ub((1, 512), "int32")
                base_indices = T.alloc_ub((1, 512), "int32")
                threshold = T.alloc_ub((1, 512), "float32")
                minus_one = T.alloc_ub((1, 512), "int32")
                mask = T.alloc_ub((1, 512), "bool")
                with T.rs("PIPE_MTE2"):
                    T.sync_block_set(2)
                    T.sync_block_set(3)
                T.vbrc(T.int32(-1), minus_one)
                T.arange(base_indices, [0, 1], offset=0)
                for group in T.serial(group_begin, group_end):
                    b = group // qgroups
                    qstart = group % qgroups * query_tile
                    qprefix = T.cast(ActualQ[b], "int32")
                    kprefix = T.cast(ActualK[b], "int32")
                    klen = T.cast(ActualK[b + 1] - ActualK[b], "int32")
                    qlen = T.cast(ActualQ[b + 1], "int32") - qprefix
                    nq = T.min(query_tile, qlen - qstart)
                    av_begin = vid * ((nq + 1) // 2)
                    av_count = (nq + 1) // 2 - vid * (nq % 2)
                    begin = T.max(task_begin - group * ktiles, 0)
                    end = T.min(task_end - group * ktiles, ktiles)
                    if nq > 0:
                        T.vbrc(-T.infinity("float32"), history)
                        if True:
                            # This state is carried across key-block iterations.
                            # An outer-loop write prevents allocation placement
                            # from treating each cache slice as a row temporary.
                            for cache_row in T.serial(vector_rows):
                                T.vbrc(-T.infinity("float32"), cache[cache_row, :, :])
                        if av_count > 0:
                            T.copy(
                                Weight[
                                    (qprefix + qstart + av_begin) * 16 : (
                                        qprefix + qstart + av_begin + av_count
                                    )
                                    * 16,
                                    :,
                                ],
                                weights[: av_count * 16, :],
                            )
                        for kb in T.serial(begin, end):
                            if kb * 512 < klen:
                                with T.rs("PIPE_MTE2"):
                                    T.sync_block_wait(kb % 2)
                                for row in T.serial(av_count):
                                    qrow = qstart + av_begin + row
                                    qtoken = qprefix + qrow  # noqa: F841 - preserve measured kernel AST
                                    with T.rs("PIPE_MTE2"):
                                        T.copy(
                                            Scores[
                                                cid,
                                                kb % 2,
                                                (av_begin + row) * 16 : (
                                                    av_begin + row + 1
                                                )
                                                * 16,
                                                :,
                                            ],
                                            score,
                                        )
                                    # ReLU is fused into Cube Fixpipe, before weighting.
                                    T.vmul(
                                        score,
                                        weights[row * 16 : (row + 1) * 16, :],
                                        score,
                                    )
                                    T.vadd(score[0:8, :], score[8:16, :], score[0:8, :])
                                    T.vadd(score[0:4, :], score[4:8, :], score[0:4, :])
                                    T.vadd(score[0:2, :], score[2:4, :], score[0:2, :])
                                    T.vadd(score[0, :], score[1, :], score[0, :])
                                    # Dead reduction rows are reused for mask constants.
                                    T.vbrc(kb * 512, indices)
                                    T.vadd(base_indices, indices, indices)
                                    valid_end = (
                                        klen - qlen + qrow + 1 if causal else klen
                                    )
                                    T.vbrc(
                                        T.float32(8.5070586659632215e37), score[1, :]
                                    )
                                    T.vmin(score[0, :], score[1, :], score[0, :])
                                    if True:
                                        if kb * 512 < init_num:
                                            T.vbrc(
                                                T.float32(1.7014118346046923e38),
                                                score[1, :],
                                            )
                                            T.vbrc(T.float32(init_num), threshold)
                                            T.vcast(
                                                indices, score[2, :], round_mode="rint"
                                            )
                                            T.vcmp(score[2, :], threshold, mask, "lt")
                                            T.vselect(
                                                mask,
                                                score[1, :],
                                                score[0, :],
                                                score[0, :],
                                            )
                                        if (
                                            local_num > 0
                                            and (kb + 1) * 512 > valid_end - local_num
                                        ):
                                            T.vbrc(
                                                T.float32(1.7014118346046923e38),
                                                score[1, :],
                                            )
                                            T.vbrc(valid_end - local_num, threshold)
                                            T.vcast(
                                                indices, score[2, :], round_mode="rint"
                                            )
                                            T.vcmp(score[2, :], threshold, mask, "ge")
                                            T.vselect(
                                                mask,
                                                score[1, :],
                                                score[0, :],
                                                score[0, :],
                                            )
                                    if (kb + 1) * 512 > valid_end:
                                        T.vbrc(valid_end, threshold)
                                        T.vcast(indices, score[2, :], round_mode="rint")
                                        T.vcmp(score[2, :], threshold, mask, "lt")
                                        T.vbrc(-T.infinity("float32"), score[1, :])
                                        T.vselect(
                                            mask, score[0, :], score[1, :], score[0, :]
                                        )
                                        T.vselect(mask, indices, minus_one, indices)
                                    slot = T.max(0, (kb - begin - 1) % 3)
                                    T.vsort32(
                                        score[0, :],
                                        indices,
                                        scratch[:1024],
                                        repeat_times=16,
                                    )
                                    T.vmrgsort(
                                        scratch[:1024],
                                        scratch[64:1024],
                                        scratch[128:1024],
                                        scratch[192:1024],
                                        scratch[1024:2048],
                                        (32, 32, 32, 32),
                                        repeat_times=4,
                                    )
                                    T.vmrgsort(
                                        scratch[1024:1280],
                                        scratch[1280:1536],
                                        scratch[1536:1792],
                                        scratch[1792:2048],
                                        cache[row, slot, :],
                                        (128, 128, 128, 128),
                                    )
                                    if kb == begin:
                                        T.copy(cache[row, slot, :], history[row, :1024])
                                    elif (
                                        slot == 2
                                        or kb + 1 == end
                                        or (kb + 1) * 512 >= klen
                                    ):
                                        if kb - begin <= 3:
                                            if slot == 2:
                                                T.vmrgsort(
                                                    history[row, :],
                                                    cache[row, 0, :],
                                                    cache[row, 1, :],
                                                    cache[row, 2, :],
                                                    scratch,
                                                    (512, 512, 512, 512),
                                                    valid_bit=15,
                                                )
                                                T.copy(
                                                    scratch[:4096], history[row, :4096]
                                                )
                                            elif slot == 1:
                                                T.vmrgsort(
                                                    history[row, :],
                                                    cache[row, 0, :],
                                                    cache[row, 1, :],
                                                    cache[row, 2, :],
                                                    scratch,
                                                    (512, 512, 512, 0),
                                                    valid_bit=7,
                                                )
                                                T.copy(
                                                    scratch[:3072], history[row, :3072]
                                                )
                                            else:
                                                T.vmrgsort(
                                                    history[row, :],
                                                    cache[row, 0, :],
                                                    cache[row, 1, :],
                                                    cache[row, 2, :],
                                                    scratch,
                                                    (512, 512, 0, 0),
                                                    valid_bit=3,
                                                )
                                                T.copy(
                                                    scratch[:2048], history[row, :2048]
                                                )
                                        else:
                                            if slot == 2:
                                                T.vmrgsort(
                                                    history[row, :],
                                                    cache[row, 0, :],
                                                    cache[row, 1, :],
                                                    cache[row, 2, :],
                                                    scratch,
                                                    (2048, 512, 512, 512),
                                                    valid_bit=15,
                                                )
                                                T.copy(
                                                    scratch[:4096], history[row, :4096]
                                                )
                                            elif slot == 1:
                                                T.vmrgsort(
                                                    history[row, :],
                                                    cache[row, 0, :],
                                                    cache[row, 1, :],
                                                    cache[row, 2, :],
                                                    scratch,
                                                    (2048, 512, 512, 0),
                                                    valid_bit=7,
                                                )
                                                T.copy(
                                                    scratch[:4096], history[row, :4096]
                                                )
                                            else:
                                                T.vmrgsort(
                                                    history[row, :],
                                                    cache[row, 0, :],
                                                    cache[row, 1, :],
                                                    cache[row, 2, :],
                                                    scratch,
                                                    (2048, 512, 0, 0),
                                                    valid_bit=3,
                                                )
                                                T.copy(
                                                    scratch[:4096], history[row, :4096]
                                                )
                                with T.rs("PIPE_MTE2"):
                                    T.sync_block_set(2 + kb % 2)
                        for row in T.serial(av_count):
                            T.copy(
                                history[row, :], Partial[group, cid, av_begin + row, :]
                            )

            if True:
                with T.Scope("Vector"):
                    # All launched Vector cores participate after publishing
                    # partial lists. Flags 0..3 belong to the Cube/Vector ring.
                    T.pipe_barrier("PIPE_ALL")
                    with T.rs("PIPE_MTE3"):
                        T.block_barrier(14)
                    T.pipe_barrier("PIPE_ALL")
                    final_history = T.alloc_ub((4096,), "float32")
                    incoming = T.alloc_ub((3, 4096), "float32")
                    final_scratch = T.alloc_ub((16384,), "float32")
                    final_values = T.alloc_ub((2048,), "float32")
                    final_indices = T.alloc_ub((2048,), "int32")
                    final_minus_one = T.alloc_ub((2048,), "int32")
                    final_mask = T.alloc_ub((2048,), "bool")
                    final_converted = T.alloc_ub((2048,), dtype)
                    for step in T.serial(
                        (groups * query_tile + cores * 2 - 1) // (cores * 2)
                    ):
                        flat = cid * 2 + vid + step * cores * 2
                        group = flat // query_tile
                        row = flat % query_tile
                        b = group // qgroups
                        qlocal = group % qgroups * query_tile + row
                        if group < groups:
                            q = T.cast(ActualQ[b], "int32") + qlocal
                            if qlocal < T.cast(ActualQ[b + 1] - ActualQ[b], "int32"):
                                first = ((group * ktiles + 1) * cores - 1) // tasks
                                last = ((group + 1) * ktiles * cores - 1) // tasks
                                T.copy(Partial[group, first, row, :], final_history)
                                # Bound the number of chunks statically. Only
                                # valid sources execute; no signed division.
                                for chunk in T.serial((cores + 2) // 3):
                                    source = first + 1 + chunk * 3
                                    if source <= last:
                                        count = T.min(3, last - source + 1)
                                        for lane in T.serial(count):
                                            T.copy(
                                                Partial[group, source + lane, row, :],
                                                incoming[lane, :],
                                            )
                                        if count == 3:
                                            T.vmrgsort(
                                                final_history,
                                                incoming[0, :],
                                                incoming[1, :],
                                                incoming[2, :],
                                                final_scratch,
                                                (2048, 2048, 2048, 2048),
                                                valid_bit=15,
                                                exhausted_suspension=True,
                                            )
                                        elif count == 2:
                                            T.vmrgsort(
                                                final_history,
                                                incoming[0, :],
                                                incoming[1, :],
                                                incoming[0, :],
                                                final_scratch,
                                                (2048, 2048, 2048, 0),
                                                valid_bit=7,
                                                exhausted_suspension=True,
                                            )
                                        else:
                                            T.vmrgsort(
                                                final_history,
                                                incoming[0, :],
                                                incoming[0, :],
                                                incoming[0, :],
                                                final_scratch,
                                                (2048, 2048, 0, 0),
                                                valid_bit=3,
                                                exhausted_suspension=True,
                                            )
                                        T.copy(final_scratch[:4096], final_history)
                                T.vextract_pairs(
                                    final_history, final_values, final_indices
                                )
                                T.vbrc(T.int32(-1), final_minus_one)
                                T.vcast(
                                    final_indices,
                                    final_scratch[:2048],
                                    round_mode="rint",
                                )
                                T.vbrc(T.float32(-1), final_scratch[2048:4096])
                                T.vcmp(
                                    final_scratch[:2048],
                                    final_scratch[2048:4096],
                                    final_mask,
                                    "gt",
                                )
                                T.vselect(
                                    final_mask,
                                    final_indices,
                                    final_minus_one,
                                    final_indices,
                                )
                                T.vbrc(T.float32(65504), final_scratch[:2048])
                                T.vmin(final_values, final_scratch[:2048], final_values)
                                T.vcast(
                                    final_values, final_converted, round_mode="round"
                                )
                                T.copy(final_indices, IndicesOut[q, 0, :])
                                if return_value:
                                    T.copy(final_converted, ValuesOut[q, 0, :])

    return main


class MlpPrefillExpert:
    """Prepared TND shape specialization, with no CPU work inside replay."""

    def __init__(
        self,
        qlens,
        klens,
        device,
        init_num=4,
        local_num=1024,
        causal=True,
        return_value=True,
        dtype=torch.bfloat16,
    ):
        if os.environ.get("TILELANG_ASCEND_MODE") != "Expert":
            raise ValueError("Expert mode required")
        if len(qlens) != len(klens) or not qlens:
            raise ValueError("invalid sequence lengths")
        if any(q <= 0 or k < q for q, k in zip(qlens, klens, strict=True)):
            raise ValueError("require 0 < Lq <= Lk")
        if dtype not in (torch.bfloat16, torch.float16):
            raise ValueError("BF16/FP16 only")
        if init_num < 0 or init_num > 512 or local_num < 0:
            raise ValueError("require 0 <= init <= 512 and local >= 0")
        self.qlens, self.klens = tuple(qlens), tuple(klens)
        self.total_q, self.total_k = sum(qlens), sum(klens)
        groups = len(qlens) * ((max(qlens) + 7) // 8)
        cores = min(
            NPUUtils.get().get_aicore_num(), groups * ((max(klens) + 511) // 512)
        )
        self.scores = torch.empty(
            (cores, 2, 128, 512), dtype=torch.float32, device=device
        )
        self.partial = torch.empty(
            (groups, cores, 8, 4096), dtype=torch.float32, device=device
        )
        self.indices = torch.empty(
            (self.total_q, 1, 2048), dtype=torch.int32, device=device
        )
        self.values = torch.empty_like(self.indices, dtype=dtype)
        self.dtype = dtype
        self.main = tilelang.compile(
            make_main(
                qlens,
                max(qlens),
                max(klens),
                self.total_k,
                cores,
                causal=causal,
                init_num=init_num,
                local_num=local_num,
                return_value=return_value,
                dtype="bfloat16" if dtype == torch.bfloat16 else "float16",
            ),
            target="npuir",
            pass_configs={tilelang.PassConfigKey.NPUIR_ENABLE_AUTO_MULTI_BUFFER: False},
        )
        self._bound = False

    def bind(self, q, k, w, qb, kb):
        for tensor, shape, dtype in (
            (q, (self.total_q, 16, 128), self.dtype),
            (k, (self.total_k, 1, 128), self.dtype),
            (w, (self.total_q, 16), torch.float32),
            (qb, (len(self.qlens) + 1,), torch.int64),
            (kb, (len(self.klens) + 1,), torch.int64),
        ):
            if (
                tuple(tensor.shape) != shape
                or tensor.dtype != dtype
                or not tensor.is_contiguous()
                or tensor.device != self.indices.device
            ):
                raise ValueError("input shape/dtype/device/contiguity mismatch")
        for t, lengths in ((qb, self.qlens), (kb, self.klens)):
            expected = torch.tensor((0,) + lengths, dtype=torch.int64).cumsum(0)
            if not torch.equal(t.cpu(), expected):
                raise ValueError("bound lengths differ")
        self.args = (
            q.view(-1, 128),
            k.view(-1, 128),
            w.view(-1, 1),
            qb,
            kb,
            self.scores,
            self.partial,
            self.indices,
            self.values,
        )
        self._bound = True
        return self

    def __call__(self):
        if not self._bound:
            raise ValueError("bind inputs before execution")
        self.main(*self.args)
        return self.indices, self.values

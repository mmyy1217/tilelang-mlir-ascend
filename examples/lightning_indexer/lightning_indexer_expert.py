# Copyright (c) Huawei Technologies Co., Ltd. 2026.
"""QFit V7: a single Expert MixCV LightningIndexer kernel.

See DESIGN.md for the uniform-query input contract and validation.
"""

import os

import torch

import tilelang
import tilelang.language as T
from tilelang.utils import NPUUtils


def make_main(
    batch,
    max_q,
    max_k,
    physical_pages,
    table_width,
    cores,
    causal=True,
    query_tile=8,
):
    total_q = batch * max_q
    qgroups = (max_q + query_tile - 1) // query_tile
    vector_rows = (query_tile + 1) // 2
    groups = batch * qgroups
    ktiles = (max_k + 511) // 512
    tasks = groups * ktiles
    cache_slots = 3
    scratch_size = 7168

    @T.prim_func
    def main(
        Query: T.Tensor((total_q * 16, 128), "bfloat16"),
        Key: T.Tensor((physical_pages, 128, 1, 128), "bfloat16"),
        Weight: T.Tensor((total_q * 16, 1), "bfloat16"),
        Table: T.Tensor((batch, table_width), "int32"),
        ActualQ: T.Tensor((batch,), "int32"),
        ActualK: T.Tensor((batch,), "int32"),
        Init: T.Tensor((batch,), "int32"),
        Local: T.Tensor((total_q,), "int32"),
        Scores: T.Tensor((cores, 2, 128, 512), "float32"),
        Partial: T.Tensor((groups, cores, query_tile, 4096), "float32"),
        IndicesOut: T.Tensor((total_q, 1, 2048), "int32"),
        ValuesOut: T.Tensor((total_q, 1, 2048), "bfloat16"),
    ):
        with T.Kernel(cores, is_npu=True) as (cid, vid):
            task_begin = tasks * cid // cores
            task_end = tasks * (cid + 1) // cores
            group_begin = task_begin // ktiles
            group_end = (task_end + ktiles - 1) // ktiles

            with T.Scope("Cube"):
                q_l1 = T.alloc_L1((query_tile * 16, 128), "bfloat16")
                k_l1 = T.alloc_L1((2, 128, 128), "bfloat16")
                c_l0 = T.alloc_L0C((2, query_tile * 16, 128), "float32")
                for group in T.serial(group_begin, group_end):
                    b = group // qgroups
                    qstart = group % qgroups * query_tile
                    qprefix = T.if_then_else(b == 0, 0, ActualQ[T.max(b - 1, 0)])
                    qlen = ActualQ[b] - qprefix
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
                            if kb * 512 < ActualK[b]:
                                with T.rs("PIPE_FIX"):
                                    T.sync_block_wait(2 + kb % 2)
                                for page in T.serial(4):
                                    logical_page = kb * 4 + page
                                    physical_page = T.if_then_else(
                                        logical_page * 128 < ActualK[b],
                                        Table[b, T.min(logical_page, table_width - 1)],
                                        0,
                                    )
                                    T.copy(
                                        Key[physical_page, :, 0, :],
                                        k_l1[page % 2, :, :],
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
                weights_bf = T.alloc_ub((vector_rows * 16, 1), "bfloat16")
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
                    qprefix = T.if_then_else(b == 0, 0, ActualQ[T.max(b - 1, 0)])
                    qlen = ActualQ[b] - qprefix
                    nq = T.min(query_tile, qlen - qstart)
                    av_begin = vid * ((nq + 1) // 2)
                    av_count = (nq + 1) // 2 - vid * (nq % 2)
                    begin = T.max(task_begin - group * ktiles, 0)
                    end = T.min(task_end - group * ktiles, ktiles)
                    if nq > 0:
                        T.vbrc(-T.infinity("float32"), history)
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
                                weights_bf[: av_count * 16, :],
                            )
                            T.vcast(
                                weights_bf[0, 0],
                                weights[0, 0],
                                size=[av_count * 16, 1],
                                round_mode="rint",
                            )
                        for kb in T.serial(begin, end):
                            if kb * 512 < ActualK[b]:
                                with T.rs("PIPE_MTE2"):
                                    T.sync_block_wait(kb % 2)
                                for row in T.serial(av_count):
                                    qrow = qstart + av_begin + row
                                    qtoken = qprefix + qrow
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
                                        ActualK[b] - qlen + qrow + 1
                                        if causal
                                        else ActualK[b]
                                    )
                                    if Init[b] + Local[qtoken] < valid_end:
                                        if kb * 512 < Init[b]:
                                            T.vbrc(
                                                T.float32(1.7014118346046923e38),
                                                score[1, :],
                                            )
                                            T.vbrc(Init[b], threshold)
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
                                            Local[qtoken] > 0
                                            and (kb + 1) * 512
                                            > valid_end - Local[qtoken]
                                        ):
                                            T.vbrc(
                                                T.float32(1.7014118346046923e38),
                                                score[1, :],
                                            )
                                            T.vbrc(valid_end - Local[qtoken], threshold)
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
                                    slot = (kb - begin) % 3
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
                                        T.copy(scratch[:4096], history[row, :])
                                    elif kb + 1 == end or (kb + 1) * 512 >= ActualK[b]:
                                        if slot == 1:
                                            T.vmrgsort(
                                                history[row, :],
                                                cache[row, 0, :],
                                                cache[row, 1, :],
                                                cache[row, 0, :],
                                                scratch,
                                                (2048, 512, 512, 0),
                                                valid_bit=7,
                                            )
                                        else:
                                            T.vmrgsort(
                                                history[row, :],
                                                cache[row, 0, :],
                                                cache[row, 0, :],
                                                cache[row, 0, :],
                                                scratch,
                                                (2048, 512, 0, 0),
                                                valid_bit=3,
                                            )
                                        T.copy(scratch[:4096], history[row, :])
                                with T.rs("PIPE_MTE2"):
                                    T.sync_block_set(2 + kb % 2)
                        for row in T.serial(av_count):
                            T.copy(
                                history[row, :], Partial[group, cid, av_begin + row, :]
                            )

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
                final_converted = T.alloc_ub((2048,), "bfloat16")
                for step in T.serial((total_q + cores * 2 - 1) // (cores * 2)):
                    q = cid * 2 + vid + step * cores * 2
                    if q < total_q:
                        group = q // max_q * qgroups + q % max_q // query_tile
                        row = q % max_q % query_tile
                        first = ((group * ktiles + 1) * cores - 1) // tasks
                        last = ((group + 1) * ktiles * cores - 1) // tasks
                        T.copy(Partial[group, first, row, :], final_history)
                        for chunk in T.serial((last - first + 2) // 3):
                            source = first + 1 + chunk * 3
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
                        T.vextract_pairs(final_history, final_values, final_indices)
                        T.vbrc(T.int32(-1), final_minus_one)
                        T.vcast(final_indices, final_scratch[:2048], round_mode="rint")
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
                        T.vcast(final_values, final_converted, round_mode="rint")
                        T.copy(final_indices, IndicesOut[q, 0, :])
                        T.copy(final_converted, ValuesOut[q, 0, :])

    return main


class LightningIndexerExpert:
    """Prepared uniform-Q specialization; construction/validation is untimed.

    Bind equal per-request Q lengths and max K before graph capture. Calls do
    no CPU tensor copies and no torch.topk. Prefix lengths must match the bound
    uniform shape; ragged-Q dispatch is deliberately not claimed here.
    """

    def __init__(
        self,
        batch,
        query_per_request,
        max_key_length,
        physical_pages=5408,
        table_width=1565,
        device="npu:0",
        causal=True,
        query_tile=None,
    ):
        if os.environ.get("TILELANG_ASCEND_MODE") != "Expert":
            raise ValueError("set TILELANG_ASCEND_MODE=Expert before compilation")
        if batch <= 0 or query_per_request <= 0 or max_key_length <= 0:
            raise ValueError("batch, query length and max key length must be positive")
        if physical_pages <= 0 or table_width <= 0:
            raise ValueError("page storage and table width must be positive")
        if table_width * 128 < max_key_length:
            raise ValueError("block table is too short")
        if query_tile is None:
            if query_per_request in (1, 2, 5):
                query_tile = query_per_request
            else:
                query_tile = 8 if query_per_request % 8 == 0 else 4
        if query_tile not in (1, 2, 4, 5, 8):
            raise ValueError("QFit V7 query_tile must be 1, 2, 4, 5 or 8")
        self.batch, self.qp, self.max_k = batch, query_per_request, max_key_length
        self.total_q = batch * query_per_request
        self.physical_pages, self.table_width = physical_pages, table_width
        self._validated = False
        self.query_tile = query_tile
        groups = batch * ((query_per_request + query_tile - 1) // query_tile)
        cores = min(
            NPUUtils.get().get_aicore_num(), groups * ((max_key_length + 511) // 512)
        )
        self.scores = torch.empty(
            (cores, 2, 128, 512), dtype=torch.float32, device=device
        )
        self.partial = torch.empty(
            (groups, cores, query_tile, 4096), dtype=torch.float32, device=device
        )
        self.indices = torch.empty(
            (self.total_q, 1, 2048), dtype=torch.int32, device=device
        )
        self.values = torch.empty(
            (self.total_q, 1, 2048), dtype=torch.bfloat16, device=device
        )
        config = {tilelang.PassConfigKey.NPUIR_ENABLE_AUTO_MULTI_BUFFER: False}
        self.main = tilelang.compile(
            make_main(
                batch,
                query_per_request,
                max_key_length,
                physical_pages,
                table_width,
                cores,
                causal,
                query_tile,
            ),
            target="npuir",
            pass_configs=config,
        )

    def validate_metadata(self, actual_q, actual_k):
        """Call once at binding time, not inside the timed/graph-captured loop."""
        expected = torch.arange(1, self.batch + 1, dtype=torch.int32) * self.qp
        if not torch.equal(actual_q.cpu(), expected):
            raise ValueError(
                "this experimental specialization requires uniform query lengths"
            )
        lengths = actual_k.cpu()
        if bool(((lengths < 0) | (lengths > self.max_k)).any()):
            raise ValueError("actual key length outside the bound specialization")

    def validate_inputs(self, q, k, w, actual_q, actual_k, block_table, init, local):
        """Untimed binding gate. Revalidate whenever input metadata changes.

        The prepared experimental interface requires immutable validated shape,
        lengths and page metadata during capture/replay. It is not a generic
        dispatcher. Numeric validation is the independent benchmark oracle's
        responsibility; finite random tests do not establish overflow behavior.
        """
        self._validated = False
        specs = (
            ("query", q, (self.total_q, 16, 128), torch.bfloat16),
            ("key", k, (self.physical_pages, 128, 1, 128), torch.bfloat16),
            ("weight", w, (self.total_q, 16), torch.bfloat16),
            ("actual_q", actual_q, (self.batch,), torch.int32),
            ("actual_k", actual_k, (self.batch,), torch.int32),
            ("block_table", block_table, (self.batch, self.table_width), torch.int32),
            ("init", init, (self.batch,), torch.int32),
            ("local", local, (self.total_q,), torch.int32),
        )
        for name, tensor, shape, dtype in specs:
            if (
                tuple(tensor.shape) != shape
                or tensor.dtype != dtype
                or not tensor.is_contiguous()
                or tensor.device != self.indices.device
            ):
                raise ValueError(
                    f"{name} must be contiguous {dtype} {shape} on {self.indices.device}"
                )
        self.validate_metadata(actual_q, actual_k)
        if bool((init.cpu() < 0).any()) or bool((local.cpu() < 0).any()):
            raise ValueError("init and local lengths must be nonnegative")
        lengths, table = actual_k.cpu(), block_table.cpu()
        for b in range(self.batch):
            used = table[b, : (int(lengths[b]) + 127) // 128]
            if bool(((used < 0) | (used >= self.physical_pages)).any()):
                raise ValueError(f"request {b} references an unallocated physical page")
        self._validated = True

    def __call__(self, q, k, w, actual_q, actual_k, block_table, init, local):
        if not self._validated:
            raise ValueError("call validate_inputs once before capture or execution")
        self.main(
            q.view(self.total_q * 16, 128),
            k,
            w.view(self.total_q * 16, 1),
            block_table,
            actual_q,
            actual_k,
            init,
            local,
            self.scores,
            self.partial,
            self.indices,
            self.values,
        )
        return self.indices, self.values

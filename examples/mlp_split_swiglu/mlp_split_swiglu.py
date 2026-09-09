"""Grouped SwiGLU, adapted from vec_add_2d and the Developer cast example.

Only rows belonging to [local_exp_start, local_exp_end) are written, at their
original row offsets. Other output rows are unspecified, matching AscendC.
"""

import functools
import os

import tilelang
import tilelang.language as T


def _group_sum(groups, begin, end):
    return sum((groups[i] for i in range(begin, end)), T.int32(0))


@functools.lru_cache(None)
def build_kernel(
    rows,
    hidden,
    experts,
    start=0,
    end=-1,
    dtype="bfloat16",
    block=1024,
    cores=40,
    block_rows=4,
):
    if os.environ.get("TILELANG_ASCEND_MODE") != "Developer":
        raise RuntimeError("Set TILELANG_ASCEND_MODE=Developer before compiling")
    if end == -1:
        start, end = 0, experts
    assert 0 <= start < end <= experts
    assert hidden > 0 and hidden % 16 == 0 and hidden <= 7680
    assert hidden % block == 0
    columns = hidden // block

    @T.prim_func
    def main(
        X: T.Tensor((rows, hidden * 2), dtype),
        G: T.Tensor((experts,), "int32"),
        Y: T.Tensor((rows, hidden), dtype),
    ):
        with T.Kernel(cores, is_npu=True) as (cid, _):
            a = T.alloc_ub((block_rows, block), dtype)
            b = T.alloc_ub((block_rows, block), dtype)
            af = T.alloc_ub((block_rows, block), "float32")
            bf = T.alloc_ub((block_rows, block), "float32")
            z = T.alloc_ub((block_rows, block), "float32")
            out = T.alloc_ub((block_rows, block), dtype)
            first = _group_sum(G, 0, start)
            count = _group_sum(G, start, end)
            for iteration in T.serial(
                T.ceildiv(T.ceildiv(count, block_rows) * columns, cores)
            ):
                tile = iteration * cores + cid
                if tile < T.ceildiv(count, block_rows) * columns:
                    row = first + tile // columns * block_rows
                    active = T.min(block_rows, first + count - row)
                    col = tile % columns * block
                    T.copy(
                        X[row : row + active, col : col + block], a[0:active, 0:block]
                    )
                    T.copy(
                        X[row : row + active, hidden + col : hidden + col + block],
                        b[0:active, 0:block],
                    )
                    T.vcast(a, af, round_mode="rint")
                    T.vcast(b, bf, round_mode="rint")
                    T.vmul(af, -1.0, z)
                    T.vexp(z, z)
                    T.vadd(z, 1.0, z)
                    T.vdiv(af, z, z)
                    T.vmul(z, bf, z)
                    T.vcast(z, out, round_mode="rint")
                    T.copy(
                        out[0:active, 0:block], Y[row : row + active, col : col + block]
                    )

    return tilelang.compile(main, target="npuir")


def mlp_split_swiglu(x, group_list, local_exp_start=0, local_exp_end=-1):
    import torch

    if x.ndim != 2 or not x.is_contiguous() or x.shape[1] % 32:
        raise ValueError("x must be contiguous [M, 2H], H divisible by 16")
    if x.dtype not in (torch.float16, torch.bfloat16):
        raise TypeError("x must be float16 or bfloat16")
    if (
        group_list.ndim != 1
        or group_list.dtype != torch.int32
        or not group_list.is_contiguous()
    ):
        raise ValueError("group_list must be contiguous int32 counts")
    if x.device.type != "npu" or group_list.device != x.device:
        raise ValueError("inputs must be on the same NPU")
    h = x.shape[1] // 2
    block = min(1024, h)
    while h % block:
        block -= 16
    kernel = build_kernel(
        x.shape[0],
        h,
        group_list.numel(),
        local_exp_start,
        local_exp_end,
        str(x.dtype).split(".")[-1],
        block,
    )
    y = torch.empty((x.shape[0], h), dtype=x.dtype, device=x.device)
    kernel(x, group_list, y)
    return y

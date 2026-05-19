# Copyright (c) Huawei Technologies Co., Ltd. 2025.
import argparse
import os

os.environ.setdefault("TILELANG_ASCEND_MODE", "Developer")
os.environ.setdefault("TILELANG_ENABLE_SIMT", "1")

import torch
import torch_npu  # noqa: F401

import tilelang
import tilelang.language as T


def env_int(name, default):
    value = os.environ.get(name)
    return default if value is None else int(value)


# Shape is the total gathered element count; block is T.Parallel lanes per program.
# Launch grid is ceildiv(shape, block); larger block means fewer programs.

# Tail-mask cases should include shape values that are not multiples of block.
# Suggested pairs: 4097/128, 16385/256, 65537/512, 262145/512.

# Also sweep --block 128|256|512 with the same shape to compare lane width.
def indirect_load_vadd(n, block):
    @T.prim_func
    def main(
        X: T.Tensor((n * 2,), "float32"),
        Y: T.Tensor((n,), "float32"),
        IDX_GM: T.Tensor((n,), "int32"),
        OUT_GM: T.Tensor((n,), "float32"),
    ):
        with T.Kernel(T.ceildiv(n, block), is_npu=True) as (pid, _):
            start = pid * block
            valid = T.min(block, n - start)
            IDX_UB = T.alloc_ub((block,), "int32")
            O_UB = T.alloc_ub((block,), "float32")
            Y_UB = T.alloc_ub((block,), "float32")
            SUM_UB = T.alloc_ub((block,), "float32")

            value_zero = 0
            T.npuir_brc(value_zero, O_UB)
            T.npuir_brc(value_zero, Y_UB)
            T.copy(IDX_GM[start:start + valid], IDX_UB[0:valid])
            T.copy(Y[start:start + valid], Y_UB[0:valid])

            for i in T.Parallel(block):
                if i < valid:
                    O_UB[i] = X[IDX_UB[i]]

            T.vadd(O_UB, Y_UB, SUM_UB)
            T.copy(SUM_UB[0:valid], OUT_GM[start:start + valid])

    return main


def main(n, block):
    if n <= 0 or block <= 0:
        raise ValueError("n and block must be positive")

    torch.manual_seed(0)
    torch.npu.set_device(0)

    x = torch.randn(n * 2, device="npu", dtype=torch.float32)
    y = torch.randn(n, device="npu", dtype=torch.float32)
    idx = torch.randint(0, n * 2, (n,), device="npu", dtype=torch.int32)
    out = torch.empty(n, device="npu", dtype=torch.float32)

    kernel = tilelang.compile(indirect_load_vadd(n, block), target="npuir")
    kernel(x, y, idx, out)

    torch.testing.assert_close(out, x[idx.long()] + y, rtol=1e-3, atol=1e-3)
    print("PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", "--shape", dest="n", type=int,
                        default=env_int("TILELANG_SIMT_N", 1000))
    parser.add_argument("--block", type=int,
                        default=env_int("TILELANG_SIMT_BLOCK", 256))
    args = parser.parse_args()
    main(args.n, args.block)

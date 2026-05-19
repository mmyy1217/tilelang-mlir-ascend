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

# Full-block requires shape % block == 0.
# Suggested sweep: --shape 4096|16384|65536|262144|1048576

# Combine with --block 128|256|512 when comparing SIMD and SIMT.
def indirect_load_full_block(n, block):
    @T.prim_func
    def main(
        X: T.Tensor((n * 2,), "float32"),
        IDX_GM: T.Tensor((n,), "int32"),
        OUT_GM: T.Tensor((n,), "float32"),
    ):
        with T.Kernel(T.ceildiv(n, block), is_npu=True) as (pid, _):
            start = pid * block
            IDX_UB = T.alloc_ub((block,), "int32")
            O_UB = T.alloc_ub((block,), "float32")

            T.copy(IDX_GM[start:start + block], IDX_UB[0:block])
            for i in T.Parallel(block):
                O_UB[i] = X[IDX_UB[i]]
            T.copy(O_UB[0:block], OUT_GM[start:start + block])

    return main


def main(n, block):
    if n <= 0 or block <= 0:
        raise ValueError("n and block must be positive")
    if n % block != 0:
        raise ValueError(
            "full-block example requires n % block == 0; use tail-mask examples otherwise")

    torch.manual_seed(0)
    torch.npu.set_device(0)

    x = torch.randn(n * 2, device="npu", dtype=torch.float32)
    idx = torch.randint(0, n * 2, (n,), device="npu", dtype=torch.int32)
    out = torch.empty(n, device="npu", dtype=torch.float32)

    kernel = tilelang.compile(indirect_load_full_block(n, block), target="npuir")
    kernel(x, idx, out)

    torch.testing.assert_close(out, x[idx.long()], rtol=1e-3, atol=1e-3)
    print("PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", "--shape", dest="n", type=int,
                        default=env_int("TILELANG_SIMT_N", 1024))
    parser.add_argument("--block", type=int,
                        default=env_int("TILELANG_SIMT_BLOCK", 256))
    args = parser.parse_args()
    main(args.n, args.block)

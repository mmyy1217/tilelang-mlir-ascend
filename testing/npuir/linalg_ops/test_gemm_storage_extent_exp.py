# Copyright (c) Tile-AI Corporation.
# Licensed under the MIT License.
"""GEMM logical extents must not change the physical NZ storage pitch."""

import pytest
import torch
import torch_npu  # noqa: F401

import tilelang
import tilelang.language as T

pytestmark = [pytest.mark.op("gemm"), pytest.mark.mode("Expert")]


def tail_gemm():
    @T.prim_func
    def main(
        A: T.Tensor((128, 128), "bfloat16"),
        B: T.Tensor((128, 128), "bfloat16"),
        C: T.Tensor((128, 128), "float32"),
        Length: T.Tensor((2,), "int32"),
    ):
        with T.Kernel(1, is_npu=True) as (cid, vid):
            with T.Scope("Cube"):
                m, n = Length[0], Length[1]
                aa = T.alloc_L1((128, 128), "bfloat16")
                bb = T.alloc_L1((128, 128), "bfloat16")
                cc = T.alloc_L0C((128, 128), "float32")
                T.copy(A[:m, :], aa[:m, :])
                T.copy(B[:n, :], bb[:n, :])
                T.gemm(aa, bb, cc, initC=True, b_transpose=True, size=[m, 128, n])
                T.store_fixpipe(cc, C, size=[m, n], enable_nz2nd=True)

    return main


def sliced_gemm():
    @T.prim_func
    def main(
        A: T.Tensor((32, 288), "bfloat16"),
        B: T.Tensor((128, 288), "bfloat16"),
        C: T.Tensor((32, 128), "float32"),
    ):
        with T.Kernel(1, is_npu=True) as (cid, vid):
            with T.Scope("Cube"):
                aa = T.alloc_L1((32, 288), "bfloat16")
                bb = T.alloc_L1((128, 288), "bfloat16")
                cc = T.alloc_L0C((32, 128), "float32")
                T.copy(A, aa)
                T.copy(B, bb)
                for k in T.serial(3):
                    T.gemm(
                        aa[:, k * 96 : (k + 1) * 96],
                        bb[:, k * 96 : (k + 1) * 96],
                        cc,
                        initC=k == 0,
                        b_transpose=True,
                    )
                T.store_fixpipe(cc, C, enable_nz2nd=True)

    return main


def test_dynamic_logical_extent_preserves_storage():
    torch.manual_seed(20260921)
    a = torch.randn(128, 128).bfloat16()
    b = torch.randn(128, 128).bfloat16()
    ad, bd = a.npu(), b.npu()
    expected = a.float() @ b.float().T
    kernel = tilelang.compile(tail_gemm(), target="npuir")
    for m, n in ((128, 128), (112, 128), (96, 112), (16, 96)):
        out = torch.full((128, 128), -999.0).npu()
        length = torch.tensor([m, n], dtype=torch.int32).npu()
        kernel(ad, bd, out, length)
        torch.npu.synchronize()
        actual = out.cpu()
        torch.testing.assert_close(
            actual[:m, :n], expected[:m, :n], rtol=1e-4, atol=0.005
        )
        assert torch.equal(actual[m:], torch.full_like(actual[m:], -999.0))
        assert torch.equal(actual[:m, n:], torch.full_like(actual[:m, n:], -999.0))


def test_nonzero_k_slice_preserves_offset():
    torch.manual_seed(20260921)
    a = torch.randn(32, 288).bfloat16()
    b = torch.randn(128, 288).bfloat16()
    out = torch.empty((32, 128), device="npu", dtype=torch.float32)
    kernel = tilelang.compile(sliced_gemm(), target="npuir")
    kernel(a.npu(), b.npu(), out)
    torch.npu.synchronize()
    torch.testing.assert_close(
        out.cpu(), a.float() @ b.float().T, rtol=1e-4, atol=0.005
    )

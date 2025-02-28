import torch
import pytest

import flag_attn

torch.random.manual_seed(10086)

def max_diff(a, b):
    return (a - b).abs().max().item()

def zero_percent(a, b):
    diff = (a - b).abs()
    num_non_zeros = diff.nonzero().shape[0]
    return (1.0 - num_non_zeros/ diff.numel()) * 100.0

def report(name, actual, expected):
    print(f"{name}: \tmax_difference: {max_diff(actual, expected):0.6f}\tzero_diff elements: {zero_percent(actual, expected):0.3f}%")


@pytest.mark.parametrize('device_id', list(range(torch.cuda.device_count())))
@pytest.mark.parametrize('scale', [1.0, 2.0, 3.0, 4.0])
@pytest.mark.parametrize('B, H, T, D, P_SEQ', [
    (2, 4, 512, 128, 100),
    (2, 4, 1024, 64, 10), 
    (2, 4, 2048, 32, 0),
    (2, 4, 4096, 16, 0),
    (1, 2, 8192, 16, 10),
    (1, 2, 8192, 32, 0),
])
@pytest.mark.parametrize('causal', [True, False])
@pytest.mark.parametrize('dtype', [torch.float16, torch.bfloat16])
@pytest.mark.parametrize('stride_order', ['BHTD', 'BTHD'])
def test_attention_fwd(B, H, T, D, P_SEQ, causal, stride_order, dtype, scale, device_id):
    device = f"cuda:{device_id}"
    if stride_order == "BHTD":
        q = torch.empty((B, H, T, D), dtype=dtype, device=device).normal_(mean=0., std=scale)
        k = torch.empty((B, H, T + P_SEQ, D), dtype=dtype, device=device).normal_(mean=0., std=scale)
        v = torch.empty((B, H, T + P_SEQ, D), dtype=dtype, device=device).normal_(mean=0., std=scale)
    else:
        q = torch.empty((B, T, H, D), dtype=dtype, device=device).normal_(mean=0., std=scale).transpose(1, 2)
        k = torch.empty((B, T + P_SEQ, H, D), dtype=dtype, device=device).normal_(mean=0., std=scale).transpose(1, 2)
        v = torch.empty((B, T + P_SEQ, H, D), dtype=dtype, device=device).normal_(mean=0., std=scale).transpose(1, 2)

    o_ref = flag_attn.testing.flash_attention(q, k, v, causal, upcast=True)
    o_torch = flag_attn.testing.flash_attention(q, k, v, causal, upcast=False)
    o_hyp = flag_attn.flash_attention(q, k, v, causal)
    
    torch_max_diff = max_diff(o_torch, o_ref)
    triton_max_diff = max_diff(o_hyp, o_ref)
    report("o hyp", o_hyp, o_ref)
    report("o torch", o_hyp, o_ref)
    assert triton_max_diff <= 2 * torch_max_diff + 1e-5


@pytest.mark.parametrize('device_id', list(range(torch.cuda.device_count())))
@pytest.mark.parametrize('scale', [1.0, 2.0, 3.0, 4.0])
@pytest.mark.parametrize('B, H, T, D, P_SEQ', [
    (2, 4, 512, 128, 100),
    (2, 4, 1024, 64, 0),
    (2, 4, 2048, 32, 10),
    (2, 4, 4096, 16, 0),
    (1, 2, 8192, 16, 0),
    (2, 2, 8192, 32, 0),
])
@pytest.mark.parametrize('causal', [True, False])
@pytest.mark.parametrize('dtype', [torch.float16, torch.bfloat16])
@pytest.mark.parametrize('stride_order', ['BHTD', 'BTHD'])
def test_attention_fwd_bwd(B, H, T, D, P_SEQ, causal, stride_order, dtype, scale, device_id):
    device = f"cuda:{device_id}"
    if stride_order == "BHTD":
        q = torch.empty((B, H, T, D), dtype=dtype, device=device).normal_(mean=0., std=scale).requires_grad_()
        k = torch.empty((B, H, T + P_SEQ, D), dtype=dtype, device=device).normal_(mean=0., std=scale).requires_grad_()
        v = torch.empty((B, H, T + P_SEQ, D), dtype=dtype, device=device).normal_(mean=0., std=scale).requires_grad_()
        do = torch.randn((B, H, T, D), dtype=dtype, device=device)
    else:
        q = torch.empty((B, T, H, D), dtype=dtype, device=device).normal_(mean=0., std=scale).transpose(1, 2).requires_grad_()
        k = torch.empty((B, T + P_SEQ, H, D), dtype=dtype, device=device).normal_(mean=0., std=scale).transpose(1, 2).requires_grad_()
        v = torch.empty((B, T + P_SEQ, H, D), dtype=dtype, device=device).normal_(mean=0., std=scale).transpose(1, 2).requires_grad_()
        do = torch.randn((B, T, H, D), dtype=dtype, device=device).transpose(1, 2)
    
    o_ref = flag_attn.testing.flash_attention(q, k, v, causal=causal, upcast=True)
    o_torch = flag_attn.testing.flash_attention(q, k, v, causal=causal, upcast=False)
    o_hyp = flag_attn.flash_attention(q, k, v, causal=causal)

    gq_ref, gk_ref, gv_ref = torch.autograd.grad(o_ref, (q, k, v), do)
    gq_torch, gk_torch, gv_torch = torch.autograd.grad(o_torch, (q, k, v), do)
    gq_hyp, gk_hyp, gv_hyp = torch.autograd.grad(o_hyp, (q, k, v), do)

    o_torch_max_diff = max_diff(o_torch, o_ref)
    gq_torch_max_diff = max_diff(gq_torch, gq_ref)
    gk_torch_max_diff = max_diff(gk_torch, gk_ref)
    gv_torch_max_diff = max_diff(gv_torch, gv_ref)

    o_triton_max_diff = max_diff(o_hyp, o_ref)
    gq_triton_max_diff = max_diff(gq_hyp, gq_ref)
    gk_triton_max_diff = max_diff(gk_hyp, gk_ref)
    gv_triton_max_diff = max_diff(gv_hyp, gv_ref)

    assert o_triton_max_diff < 2 * o_torch_max_diff + 1e-5
    assert gq_triton_max_diff < 2 * gq_torch_max_diff + 1e-5
    assert gk_triton_max_diff < 2 * gk_torch_max_diff + 1e-5
    assert gv_triton_max_diff < 2 * gv_torch_max_diff + 1e-5


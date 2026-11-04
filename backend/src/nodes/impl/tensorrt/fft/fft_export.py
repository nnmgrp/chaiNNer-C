"""PyTorch to ONNX exporter with TensorRT DFT plugins (Rfft/Irfft)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.onnx.symbolic_helper import parse_args
from torch.onnx import register_custom_op_symbolic

from .models.gfisrv2 import GFISRV2, detect_gfisrv2_params
from .models.lawfft import LAWFFT, detect_lawfft_params


def load_checkpoint(model_path: str | Path) -> dict[str, torch.Tensor]:
    """Loads weights from .pth, .pt, or .safetensors."""
    path_str = str(model_path)
    if path_str.endswith(".safetensors"):
        from safetensors.torch import load_file
        return load_file(path_str, device="cpu")

    raw = torch.load(path_str, map_location="cpu", weights_only=False)
    if isinstance(raw, dict):
        for key in ("state_dict", "model", "params", "params_ema"):
            if key in raw and isinstance(raw[key], dict):
                return raw[key]
        return raw

    raise ValueError(f"Unrecognized checkpoint format: {path_str}")


def load_fft_model(model_path: str | Path) -> tuple[nn.Module, dict[str, Any], int]:
    """
    Loads an FFT model either via direct architecture instantiation (preferred for
    native TensorRT DFT plugins) or via spandrel fallback.
    Returns (model, metadata, scale).
    """
    model_path = Path(model_path)
    state_dict = load_checkpoint(model_path)
    keys_str = " ".join(state_dict.keys())

    # 1. Direct custom architectures for GFISRV2 and LAWFFT (native ai.onnx.contrib plugins)
    if "gfisr_body" in keys_str or "rn.scale" in keys_str or "post_norm.scale" in keys_str:
        params = detect_gfisrv2_params(state_dict)
        params["normalized"] = 1
        model = GFISRV2(**params)
        model.load_state_dict(state_dict, strict=False)
        scale = params.get("scale", params.get("upscale", 1))
        meta = {"arch": "GFISRV2", "params": params, "scale": scale, "normalized": 1}
        model.eval()
        return model, meta, scale

    if "FSAS" in keys_str or "body.0.residual" in keys_str or "t_mid_factor" in keys_str:
        params = detect_lawfft_params(state_dict)
        params["normalized"] = 0
        model = LAWFFT(**params)
        model.load_state_dict(state_dict, strict=False)
        scale = params.get("scale", params.get("upscale", 1))
        meta = {"arch": "LAWFFT", "params": params, "scale": scale, "normalized": 0}
        model.eval()
        return model, meta, scale

    # 2. Fallback to spandrel
    try:
        from spandrel import ModelLoader
        descriptor = ModelLoader().load_from_file(str(model_path))
        if hasattr(descriptor, "model") and isinstance(descriptor.model, nn.Module):
            model = descriptor.model.eval()
            scale = getattr(descriptor, "scale", 1)
            arch_name = getattr(getattr(descriptor, "architecture", None), "name", "SpandrelModel")
            meta = {"arch": arch_name, "scale": scale, "spandrel": True}
            return model, meta, scale
    except Exception:
        pass

    raise ValueError(f"Could not identify FFT model architecture from weights: {model_path.name}")


def register_fft_symbolics(opset: int = 17, normalized: int = 1) -> None:
    """Registers custom FFT symbolics for ONNX export."""
    def symbolic_rfft2(g: Any, x: Any, *args: Any, **kwargs: Any) -> Any:
        return g.op(
            "ai.onnx.contrib::Rfft",
            x,
            normalized_i=normalized,
            onesided_i=1,
            signal_ndim_i=2,
        )

    def symbolic_irfft2(g: Any, x: Any, *args: Any, **kwargs: Any) -> Any:
        return g.op(
            "ai.onnx.contrib::Irfft",
            x,
            normalized_i=normalized,
            onesided_i=1,
            signal_ndim_i=2,
        )

    try:
        register_custom_op_symbolic("aten::fft_rfft2", symbolic_rfft2, opset)
        register_custom_op_symbolic("aten::fft_irfft2", symbolic_irfft2, opset)
    except Exception:
        pass


def export_fft_onnx(
    model: nn.Module,
    output_path: str | Path,
    tile_size: int = 256,
    dynamic: bool = True,
    opset: int = 17,
    normalized: int = 1,
) -> dict[str, Any]:
    """Exports PyTorch FFT model to ONNX with ai.onnx.contrib plugins."""
    register_fft_symbolics(opset=opset, normalized=normalized)

    dummy = torch.randn(1, 3, tile_size, tile_size, dtype=torch.float32)
    with torch.no_grad():
        out = model(dummy)

    scale = out.shape[-1] // dummy.shape[-1]

    dynamic_axes = None
    if dynamic:
        dynamic_axes = {
            "input": {0: "batch", 2: "height", 3: "width"},
            "output": {0: "batch", 2: "height", 3: "width"},
        }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    kwargs: dict[str, Any] = {
        "export_params": True,
        "opset_version": opset,
        "do_constant_folding": True,
        "input_names": ["input"],
        "output_names": ["output"],
        "dynamo": False,
    }
    if dynamic_axes is not None:
        kwargs["dynamic_axes"] = dynamic_axes

    torch.onnx.export(model, dummy, str(output_path), **kwargs)

    return {
        "scale": scale,
        "tile_size": tile_size,
        "dynamic": dynamic,
        "normalized": normalized,
    }

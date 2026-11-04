"""TensorRT FFT Plugin integration for chaiNNer-C."""

from __future__ import annotations

from pathlib import Path

# TensorRT DFT Plugin DLL
PLUGIN_LIBRARY = Path(__file__).resolve().parent.parent / "trt_dft_plugins.dll"

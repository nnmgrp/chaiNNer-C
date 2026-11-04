from __future__ import annotations

import os
import sys
import json
import tempfile
import hashlib
from pathlib import Path

from sanic.log import logger

from api import NodeContext
from nodes.properties.inputs import (
    BoolInput,
    NumberInput,
    PthFileInput,
)
from nodes.properties.outputs import (
    DirectoryOutput,
    FileNameOutput,
    TensorRTEngineOutput,
)
from nodes.impl.compressed_file import read_file
from nodes.impl.tensorrt.cache import engines
from nodes.impl.tensorrt.model import TensorRTEngine, TensorRTEngineInfo
from nodes.impl.tensorrt.build_cache import cached_engine
from nodes.impl.tensorrt.memory import get_cuda_device_name, get_cuda_compute_capability
from nodes.impl.tensorrt import dual
from nodes.utils.utils import file_identity, split_file_path

from ...settings import get_settings
from .. import utility_group


def locate_dft_plugin() -> Path:
    """Locate the TensorRT DFT plugin DLL, or raise an informative error."""
    cand1 = dual.SOURCE / "nodes" / "impl" / "tensorrt" / "trt_dft_plugins.dll"
    if cand1.is_file():
        return cand1
    site_packages = Path(sys.executable).parent / "Lib" / "site-packages"
    cand2 = site_packages / "tensorrt_libs" / "trt_dft_plugins.dll"
    if cand2.is_file():
        return cand2
    raise RuntimeError(
        f"TensorRT DFT plugin library (trt_dft_plugins.dll) was not found at {cand1}. "
        "To build FFT models in TensorRT, please ensure trt_dft_plugins.dll is present, "
        "or use the standard PyTorch nodes (Load Model -> Upscale Image) for pure Python execution."
    )


if utility_group is not None:

    @utility_group.register(
        schema_id="chainner:tensorrt:build_fft_engine",
        name="Build FFT Engine",
        description=[
            "Convert a PyTorch FFT model (.pth, .pt, .safetensors — e.g. GFISRV2, LAWFFT) directly to an optimized TensorRT engine.",
            "Uses chaiNNer-C's native TensorRT DFT plugins (Rfft/Irfft) for accelerated frequency-domain inference.",
            "The output TensorRT Engine can be connected directly to 'Upscale Image (TensorRT)' or saved via 'Save Engine'.",
            "Engines are automatically cached so that subsequent runs with the same model and settings load instantly.",
        ],
        icon="BsNvidia",
        inputs=[
            PthFileInput(primary_input=False),
            NumberInput("Tile Size", default=256, min=64, max=1024, step=64, unit="px").with_docs(
                "Spatial tile size for inference. Larger tiles (256, 512) significantly reduce tiling overhead."
            ),
            BoolInput("Dynamic Shapes", default=False).with_docs(
                "When disabled (recommended for FFT), builds an engine optimized for the exact tile size with fixed cuFFT plans."
            ),
            BoolInput("FP16", default=True).with_docs(
                "Enable FP16 precision for faster inference and lower VRAM usage."
            ),
            NumberInput("Workspace (MB)", default=2048, min=512, max=16384, step=512).with_docs(
                "Maximum GPU workspace memory allocated for TensorRT kernel autotuning."
            ),
        ],
        outputs=[
            TensorRTEngineOutput(kind="tagged").suggest(),
            DirectoryOutput("Directory", of_input=0).with_id(2),
            FileNameOutput("Name", of_input=0).with_id(1),
        ],
        node_context=True,
    )
    def build_fft_engine_node(
        context: NodeContext,
        model_path: Path,
        tile_size: int = 256,
        dynamic_shapes: bool = False,
        fp16: bool = True,
        workspace_mb: int = 2048,
    ) -> tuple[TensorRTEngine, Path, str]:
        import tensorrt as trt

        assert os.path.exists(model_path), f"Model file {model_path} does not exist"
        assert os.path.isfile(model_path), f"Path {model_path} is not a file"

        settings = get_settings(context)
        gpu_index = settings.gpu_index
        plugin_dll = locate_dft_plugin()

        def build() -> TensorRTEngine:
            logger.info("Starting Build FFT Engine for: %s", model_path.name)

            with tempfile.TemporaryDirectory(prefix="chainner-fft-") as temp_dir:
                temp_engine = Path(temp_dir) / "model.engine"
                meta_json = Path(temp_dir) / "meta.json"

                # Build TensorRT Engine via worker child process
                logger.info("[FFT] Exporting model and compiling TensorRT engine in worker child process...")
                dual.run_child(
                    [
                        "-m",
                        "nodes.impl.tensorrt.fft.fft_engine_worker",
                        str(model_path),
                        str(temp_engine),
                        str(gpu_index),
                        str(plugin_dll),
                        str(workspace_mb),
                        "1" if fp16 else "0",
                        "1" if dynamic_shapes else "0",
                        str(tile_size),
                        str(meta_json),
                    ],
                    "FFT Engine Build",
                    dual.SOURCE,
                    context,
                )
                logger.info("[FFT] TensorRT compilation finished.")

                scale = 1
                if meta_json.is_file():
                    try:
                        scale = int(json.loads(meta_json.read_text(encoding="utf-8")).get("scale", 1))
                    except Exception:
                        pass

                engine_bytes = read_file(temp_engine)
                major, minor = get_cuda_compute_capability(gpu_index)
                min_shape = (1, 3, 64, 64) if dynamic_shapes else (1, 3, tile_size, tile_size)
                opt_shape = (1, 3, tile_size, tile_size)
                max_shape = (
                    (1, 3, max(tile_size * 2, 512), max(tile_size * 2, 512))
                    if dynamic_shapes
                    else (1, 3, tile_size, tile_size)
                )

                info = TensorRTEngineInfo(
                    precision="fp16" if fp16 else "fp32",
                    input_channels=3,
                    output_channels=3,
                    scale=scale,
                    gpu_architecture=f"sm_{major}{minor}",
                    tensorrt_version=trt.__version__,
                    has_dynamic_shapes=dynamic_shapes,
                    min_shape=min_shape,
                    opt_shape=opt_shape,
                    max_shape=max_shape,
                )
                return TensorRTEngine(engine_bytes, info)

        # Build cache key
        model_stat = os.stat(model_path)
        cache_key = hashlib.sha256(
            f"{model_path.name}_{model_stat.st_size}_{model_stat.st_mtime}_{tile_size}_{dynamic_shapes}_{fp16}_{workspace_mb}_{trt.__version__}_{get_cuda_device_name(gpu_index)}".encode(
                "utf-8"
            )
        ).hexdigest()

        engine = cached_engine(cache_key, build, settings.engine_cache_path)
        dirname, basename, _ = split_file_path(model_path)
        return engine, dirname, basename

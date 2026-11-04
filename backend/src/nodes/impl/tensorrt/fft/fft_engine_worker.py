"""Child process worker for building TensorRT engines with DFT plugins."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(
    onnx_path: str,
    engine_path: str,
    gpu_index: str,
    plugin_dll: str,
    workspace_mb: str,
    fp16: str,
    dynamic: str,
    tile_size: str,
) -> None:
    # 1. Setup CUDA DLL directories on Windows
    site_packages = Path(sys.executable).parent / "Lib" / "site-packages"
    trt_libs = site_packages / "tensorrt_libs"
    if trt_libs.is_dir() and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(trt_libs))
        os.environ["PATH"] = str(trt_libs) + ";" + os.environ.get("PATH", "")

    for cuda_cand in (
        r"G:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
        r"G:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin",
        r"G:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin",
    ):
        if os.path.isdir(cuda_cand) and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(cuda_cand)
                os.environ["PATH"] = cuda_cand + ";" + os.environ.get("PATH", "")
            except Exception:
                pass

    import tensorrt as trt
    from cuda.bindings import runtime as cudart

    # 2. Select GPU
    device_id = int(gpu_index)
    (error,) = cudart.cudaSetDevice(device_id)
    if error != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f"cudaSetDevice({device_id}) failed: {error}")

    # 3. Load Plugin Library
    reg = trt.get_plugin_registry()
    if os.path.isfile(plugin_dll):
        loaded_lib = reg.load_library(plugin_dll)
        if loaded_lib is None:
            raise RuntimeError(f"TensorRT failed to load plugin DLL: {plugin_dll}")
        print(f"[WORKER] Successfully loaded plugin: {plugin_dll}")

    # 4. Create Builder & Network
    logger = trt.Logger(trt.Logger.INFO)
    builder = trt.Builder(logger)
    network_flags = 0
    if hasattr(trt.NetworkDefinitionCreationFlag, "EXPLICIT_BATCH"):
        network_flags |= 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    if hasattr(trt.NetworkDefinitionCreationFlag, "STRONGLY_TYPED"):
        network_flags |= 1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED)
    network = builder.create_network(network_flags)
    parser = trt.OnnxParser(network, logger)

    if not parser.parse_from_file(onnx_path):
        errors = [str(parser.get_error(i)) for i in range(parser.num_errors)]
        raise RuntimeError("ONNX parse failed:\n" + "\n".join(errors))

    config = builder.create_builder_config()

    # Workspace
    ws_bytes = int(workspace_mb) * 1024 * 1024
    if ws_bytes > 0:
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, ws_bytes)

    # Precision
    if hasattr(trt.BuilderFlag, "FP16") and fp16 == "1":
        if getattr(builder, "platform_has_fast_fp16", True):
            config.set_flag(trt.BuilderFlag.FP16)
            print("[WORKER] FP16 precision enabled.")
    if hasattr(trt.BuilderFlag, "TF32"):
        config.set_flag(trt.BuilderFlag.TF32)
        print("[WORKER] TF32 precision enabled.")

    # Profile (only if dynamic dimensions exist)
    has_dynamic = any(
        any(d == -1 for d in network.get_input(i).shape)
        for i in range(network.num_inputs)
    )
    if has_dynamic:
        input_name = network.get_input(0).name
        t_size = int(tile_size)
        is_dyn = dynamic == "1"

        profile = builder.create_optimization_profile()
        if is_dyn:
            min_s = 64
            opt_s = max(t_size, 128)
            max_s = max(t_size * 2, 512)
            profile.set_shape(input_name, (1, 3, min_s, min_s), (1, 3, opt_s, opt_s), (1, 3, max_s, max_s))
            print(f"[WORKER] Dynamic profile: min=({min_s}x{min_s}), opt=({opt_s}x{opt_s}), max=({max_s}x{max_s})")
        else:
            profile.set_shape(input_name, (1, 3, t_size, t_size), (1, 3, t_size, t_size), (1, 3, t_size, t_size))
            print(f"[WORKER] Fixed profile: ({t_size}x{t_size})")

        config.add_optimization_profile(profile)
    else:
        print(f"[WORKER] Static input shapes: {network.get_input(0).shape}")

    # 5. Build Engine
    print("[WORKER] Building serialized TensorRT engine...")
    plan = builder.build_serialized_network(network, config)
    if plan is None:
        raise RuntimeError("TensorRT failed to build serialized engine plan.")

    Path(engine_path).parent.mkdir(parents=True, exist_ok=True)
    with open(engine_path, "wb") as f:
        f.write(bytes(plan))
    print(f"[WORKER] Engine successfully built and saved to: {engine_path}")


if __name__ == "__main__":
    if len(sys.argv) < 9:
        print("Usage: python -m nodes.impl.tensorrt.fft.fft_engine_worker <onnx> <engine> <gpu> <plugin> <workspace_mb> <fp16> <dynamic> <tile_size>")
        sys.exit(1)
    main(*sys.argv[1:9])

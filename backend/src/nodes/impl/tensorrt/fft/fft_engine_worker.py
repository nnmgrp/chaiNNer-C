"""Child process worker for exporting FFT models and building TensorRT engines with DFT plugins."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(
    input_path: str,
    engine_path: str,
    gpu_index: str,
    plugin_dll: str,
    workspace_mb: str,
    fp16: str,
    dynamic: str,
    tile_size: str,
    meta_json_path: str | None = None,
) -> None:
    # 1. Register CUDA DLL directories on Windows dynamically
    from nodes.impl.tensorrt.cuda_dll import register_cuda_dll_directories

    register_cuda_dll_directories()

    import tensorrt as trt
    from cuda.bindings import runtime as cudart

    # 2. Select GPU
    device_id = int(gpu_index)
    (error,) = cudart.cudaSetDevice(device_id)
    if error != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f"cudaSetDevice({device_id}) failed: {error}")

    input_file = Path(input_path)
    is_pytorch = input_file.suffix.lower() in (".pth", ".pt", ".safetensors")
    temp_onnx: Path | None = None

    if is_pytorch:
        import torch
        from nodes.impl.tensorrt.fft.fft_export import load_fft_model, export_fft_onnx

        device = f"cuda:{device_id}" if torch.cuda.is_available() else "cpu"
        print(f"[WORKER] Loading FFT model on {device}...")
        model, meta, scale = load_fft_model(input_file, device=device)
        norm_val = int(meta.get("normalized", 0))

        temp_onnx = Path(engine_path).with_suffix(".tmp.onnx")
        print(f"[WORKER] Exporting to ONNX on {device} (tile={tile_size}, dynamic={dynamic == '1'})...")
        export_fft_onnx(
            model=model,
            output_path=temp_onnx,
            tile_size=int(tile_size),
            dynamic=(dynamic == "1"),
            opset=17,
            normalized=norm_val,
            device=device,
        )
        print("[WORKER] ONNX export completed successfully.")

        if meta_json_path:
            meta_data = {
                "scale": scale,
                "arch": meta.get("arch", "FFT"),
                "normalized": norm_val,
            }
            Path(meta_json_path).write_text(json.dumps(meta_data), encoding="utf-8")

        # Free PyTorch model from GPU memory before TensorRT builder starts
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        onnx_file_to_parse = str(temp_onnx)
    else:
        onnx_file_to_parse = input_path

    try:
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

        if not parser.parse_from_file(onnx_file_to_parse):
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

    finally:
        if temp_onnx is not None and temp_onnx.is_file():
            try:
                temp_onnx.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 9:
        print("Usage: python -m nodes.impl.tensorrt.fft.fft_engine_worker <input_path> <engine> <gpu> <plugin> <workspace_mb> <fp16> <dynamic> <tile_size> [meta_json]")
        sys.exit(1)
    meta_json = sys.argv[9] if len(sys.argv) >= 10 else None
    main(*sys.argv[1:9], meta_json_path=meta_json)

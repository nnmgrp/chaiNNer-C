"""Automatic dynamic discovery and registration of CUDA Toolkit and TensorRT DLL directories on Windows."""

from __future__ import annotations

import os
import string
import sys
from functools import lru_cache
from pathlib import Path

_ADDED_DLL_DIRS: list[object] = []


def discover_cuda_dll_directories() -> list[str]:
    """Discover all potential CUDA Toolkit and TensorRT DLL directories across environment variables,
    Python site-packages, and system drive Program Files."""
    if os.name != "nt":
        return []

    dirs: set[str] = set()

    # 1. Environment variables (CUDA_PATH, CUDA_PATH_V*, CUDA_HOME, CUDA_ROOT)
    for var, val in os.environ.items():
        if (var.startswith("CUDA_PATH") or var in ("CUDA_HOME", "CUDA_ROOT")) and val:
            p = Path(val)
            for sub in (p / "bin" / "x64", p / "bin", p):
                if sub.is_dir():
                    dirs.add(str(sub.resolve()))

    # 2. Python environment site-packages (tensorrt_libs, nvidia/cu*, nvidia/cuda_*, etc.)
    site_packages = Path(sys.executable).parent / "Lib" / "site-packages"
    if site_packages.is_dir():
        for cand in [
            site_packages / "tensorrt_libs",
            *list(site_packages.glob("nvidia/*/bin")),
        ]:
            if cand.is_dir():
                dirs.add(str(cand.resolve()))

    # 3. Standard NVIDIA GPU Computing Toolkit installations across all existing Windows drive letters
    for drive_letter in string.ascii_uppercase:
        drive_path = Path(f"{drive_letter}:\\")
        if not drive_path.exists():
            continue
        cuda_base = (
            drive_path / "Program Files" / "NVIDIA GPU Computing Toolkit" / "CUDA"
        )
        if cuda_base.is_dir():
            try:
                for ver_dir in cuda_base.iterdir():
                    if ver_dir.is_dir():
                        for sub in (ver_dir / "bin" / "x64", ver_dir / "bin"):
                            if sub.is_dir():
                                dirs.add(str(sub.resolve()))
            except Exception:
                pass

    return sorted(dirs)


@lru_cache(maxsize=1)
def register_cuda_dll_directories() -> list[str]:
    """Register discovered CUDA and TensorRT DLL directories in current process via os.add_dll_directory and prepend to PATH.
    Does NOT install anything — only informs Windows where to look for existing CUDA/TensorRT DLLs."""
    if os.name != "nt":
        return []

    registered: list[str] = []
    path_prepend: list[str] = []

    for d in discover_cuda_dll_directories():
        if hasattr(os, "add_dll_directory"):
            try:
                cookie = os.add_dll_directory(d)
                _ADDED_DLL_DIRS.append(cookie)
                registered.append(d)
            except Exception:
                pass
        path_prepend.append(d)

    if path_prepend:
        os.environ["PATH"] = ";".join(path_prepend) + ";" + os.environ.get("PATH", "")

    return registered


# Alias for backward compatibility
setup_cuda_dll_directories = register_cuda_dll_directories

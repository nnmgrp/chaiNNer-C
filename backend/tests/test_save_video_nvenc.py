"""Save Video's NVENC encoders: the container matrix, FFmpeg's options, the probe that
proves a GPU and driver can encode, and refusals without a silent CPU fallback. The
round-trip tests need a real NVIDIA GPU and skip without one (NVENC is nondeterministic,
so they check structure, not bytes)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

# The node's modules load the native backend, which GitHub's checks do not build.
pytest.importorskip("nodes.impl._chainner_graph")

from api import NodeContext
from nodes.impl.ffmpeg import get_executable_path
from packages.chaiNNer_standard.image.video_frames import save_video as module
from packages.chaiNNer_standard.image.video_frames.save_video import (
    AudioSettings,
    BitDepth,
    NvencPreset,
    SimpleVideoFormat,
    Simplicity,
    SitingCorrection,
    VideoEncoder,
    VideoFormat,
    VideoInputMode,
    VideoPreset,
    Writer,
    save_video_node,
)

STORAGE = Path(os.environ.get("APPDATA", "")) / "chaiNNer" / "backend-storage"
FFMPEG, FFPROBE = map(str, get_executable_path(STORAGE / "ffmpeg"))

NVENC = (
    VideoEncoder.H264_NVENC,
    VideoEncoder.H265_NVENC,
    VideoEncoder.AV1_NVENC,
)


@pytest.fixture(autouse=True)
def fresh_probe_cache():
    module.nvenc_probe.cache_clear()
    yield
    module.nvenc_probe.cache_clear()


class Context:
    """The NodeContext members Save Video uses."""

    chosen_item = None

    def __init__(self):
        self.storage_dir = STORAGE
        self.cleanups: list[Any] = []

    def add_cleanup(self, fn: Callable[[], None]):
        self.cleanups.append(fn)


def writer(encoder: VideoEncoder = VideoEncoder.H265_NVENC, **changes) -> Writer:
    fields = {
        "container": VideoFormat.MKV,
        "encoder": encoder,
        "fps": 30,
        "audio": None,
        "audio_settings": AudioSettings.AUTO,
        "save_path": "out.mkv",
        "output_params": {"pix_fmt": "yuv420p", "vcodec": encoder.value},
        "global_params": [],
        "ffmpeg_env": SimpleNamespace(ffmpeg=FFMPEG),
        "target": None,
        "settings": None,
    }
    fields.update(changes)
    return Writer(**fields)


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS FFmpeg has no NVENC")
def test_nvenc_encoder_lists():
    # CPU encoders keep their order; each container adds the NVENC encoders it muxes.
    assert VideoFormat.MKV.encoders == (
        VideoEncoder.H264,
        VideoEncoder.H264_NVENC,
        VideoEncoder.H265,
        VideoEncoder.H265_NVENC,
        VideoEncoder.AV1_NVENC,
        VideoEncoder.VP9,
        VideoEncoder.FFV1,
    )
    assert VideoFormat.MP4.encoders == (
        VideoEncoder.H264,
        VideoEncoder.H264_NVENC,
        VideoEncoder.H265,
        VideoEncoder.H265_NVENC,
        VideoEncoder.AV1_NVENC,
        VideoEncoder.VP9,
    )
    assert VideoFormat.MOV.encoders == (
        VideoEncoder.H264,
        VideoEncoder.H264_NVENC,
        VideoEncoder.H265,
        VideoEncoder.H265_NVENC,
    )
    assert VideoFormat.WEBM.encoders == (VideoEncoder.VP9, VideoEncoder.AV1_NVENC)
    assert VideoFormat.AVI.encoders == (VideoEncoder.H264,)
    assert VideoFormat.GIF.encoders == ()
    assert all(encoder.nvenc for encoder in NVENC)


def test_nvenc_formats_and_depths():
    assert VideoEncoder.H264_NVENC.bit_depths == (8,)
    assert VideoEncoder.H265_NVENC.bit_depths == (8, 10)
    assert VideoEncoder.AV1_NVENC.bit_depths == (8, 10)
    assert VideoEncoder.H264_NVENC.pixel_formats == ("yuv420p",)
    assert VideoEncoder.H265_NVENC.pixel_formats == ("yuv420p", "yuv420p10le")
    assert VideoEncoder.AV1_NVENC.pixel_formats == ("yuv420p", "yuv420p10le")
    assert VideoEncoder.H264_NVENC.auto_depth == 8
    assert VideoEncoder.H265_NVENC.auto_depth == 10
    assert VideoEncoder.AV1_NVENC.auto_depth == 10


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS FFmpeg has no NVENC")
@pytest.mark.parametrize("encoder", NVENC)
def test_nvenc_ffmpeg_options(encoder: VideoEncoder):
    params, _ = module.graph().video_output_options(
        vars(module),
        "out.tmp",
        VideoFormat.MKV,
        encoder,
        VideoPreset.MEDIUM,
        23,
        NvencPreset.P5,
        21,
        30,
        Simplicity.ADVANCED,
        None,
    )
    assert params["vcodec"] == encoder.value
    assert params["preset"] == "p5"
    assert params["tune"] == "hq"
    assert params["rc"] == "vbr"
    assert params["cq"] == 21
    assert params["b:v"] == 0
    assert params["multipass"] == "qres"
    assert params["spatial-aq"] == 1


def test_cpu_encoders_keep_their_options():
    params, _ = module.graph().video_output_options(
        vars(module),
        "out.tmp",
        VideoFormat.MP4,
        VideoEncoder.H265,
        VideoPreset.MEDIUM,
        23,
        NvencPreset.P5,
        21,
        30,
        Simplicity.ADVANCED,
        None,
    )
    assert params["preset"] == "medium"
    assert params["crf"] == 23
    assert "cq" not in params and "tune" not in params


def test_nvenc_probe_is_cached(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.nvenc_probe("ffmpeg-bin", VideoEncoder.H265_NVENC, "yuv420p10le")
    module.nvenc_probe("ffmpeg-bin", VideoEncoder.H265_NVENC, "yuv420p10le")
    module.nvenc_probe("ffmpeg-bin", VideoEncoder.AV1_NVENC, "yuv420p10le")
    assert len(calls) == 2
    assert calls[0][calls[0].index("-pix_fmt") + 1] == "yuv420p10le"
    assert calls[0][calls[0].index("-c:v") + 1] == "hevc_nvenc"
    assert calls[1][calls[1].index("-c:v") + 1] == "av1_nvenc"


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            b"Unknown encoder 'hevc_nvenc'",
            "This FFmpeg build has no NVENC",
        ),
        (
            b"Cannot load nvcuda.dll",
            "NVENC needs an NVIDIA driver",
        ),
        (
            b"[hevc_nvenc @ 0x1] Driver does not support the required nvenc API version",
            "The NVIDIA driver is too old",
        ),
        (
            b"[av1_nvenc @ 0x1] No capable devices found",
            "This GPU has no AV1 NVENC encoder",
        ),
        (
            b"[hevc_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory",
            "NVIDIA limits concurrent NVENC sessions",
        ),
        (
            b"[hevc_nvenc @ 0x1] 10 bit encode not supported",
            "has no 10-bit support",
        ),
        (
            b"[hevc_nvenc @ 0x1] Unsupported pixel format",
            "NVENC writes 4:2:0 8/10-bit only",
        ),
        (b"[hevc_nvenc @ 0x1] something else", "NVENC failed to start"),
    ],
)
def test_nvenc_probe_errors(
    monkeypatch: pytest.MonkeyPatch, stderr: bytes, expected: str
):
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr),
    )
    with pytest.raises(RuntimeError, match=expected) as error:
        module.nvenc_probe("ffmpeg-bin", VideoEncoder.H265_NVENC, "yuv420p")
    assert stderr.decode() in str(error.value)


def test_nvenc_refuses_unsupported_pixel_formats():
    for encoder, fmt, wanted in [
        (VideoEncoder.H264_NVENC, "yuv420p10le", "at 8 bits"),
        (VideoEncoder.H265_NVENC, "yuv422p10le", "at 8 or 10 bits"),
        (VideoEncoder.AV1_NVENC, "yuv444p16le", "at 8 or 10 bits"),
        (VideoEncoder.H265_NVENC, "gbrp16le", "at 8 or 10 bits"),
    ]:
        probe = writer(
            encoder,
            output_params={"pix_fmt": fmt, "vcodec": encoder.value},
        )
        with pytest.raises(ValueError, match="4:2:0") as error:
            probe._check_pixel_format(64, 64)
        assert wanted in str(error.value)


def test_nvenc_requires_even_dimensions():
    probe = writer(VideoEncoder.H265_NVENC)
    with pytest.raises(ValueError, match="even-number frame resolution"):
        module.graph().video_writer_start(vars(module), probe, 63, 63)


def test_nvenc_failure_does_not_fall_back(monkeypatch: pytest.MonkeyPatch):
    abandoned: list[bool] = []

    class Target:
        def abandon(self):
            abandoned.append(True)

    def fail(*args: Any):
        raise RuntimeError("no NVENC")

    started: list[bool] = []

    class Graph:
        def video_writer_start(self, *args: Any):
            started.append(True)

    monkeypatch.setattr(module, "nvenc_probe", fail)
    monkeypatch.setattr(module, "graph", Graph)
    probe = writer(VideoEncoder.H264_NVENC, target=Target())
    with pytest.raises(RuntimeError, match="no NVENC"):
        probe.start(64, 48)
    assert abandoned == [True]
    assert started == []


def test_nvenc_hdr_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(module, "nvenc_probe", lambda *args: None)
    settings = module.WriteSettings(
        VideoInputMode.RGB,
        SimpleNamespace(hdr=object()),
        (
            module.Matrix.AUTO,
            module.Transfer.AUTO,
            module.Primaries.AUTO,
            module.Range.AUTO,
            module.ChromaLocation.AUTO,
        ),
        BitDepth.AUTO,
        SitingCorrection.AUTO,
    )
    writer(VideoEncoder.H265_NVENC, settings=settings)._check_nvenc()
    assert "NVENC does not write HDR10 mastering metadata" in caplog.text


def require_nvenc(encoder: VideoEncoder, pix_fmt: str) -> None:
    try:
        module.nvenc_probe(FFMPEG, encoder, pix_fmt)
    except RuntimeError as error:
        pytest.skip(str(error))


def bars(width: int, height: int) -> np.ndarray:
    image = np.zeros((height, width, 3), np.uint8)
    image[:, : width // 2] = (32, 128, 224)  # BGR
    image[:, width // 2 :] = (224, 128, 32)
    return image.astype(np.float32) / 255


def save(
    tmp_path: Path,
    image: np.ndarray,
    container: VideoFormat,
    encoder: VideoEncoder,
    bit_depth: BitDepth = BitDepth.AUTO,
    additional: str | None = None,
) -> Path:
    collector = save_video_node(
        cast(NodeContext, Context()),
        None,
        tmp_path,
        "out",
        VideoInputMode.RGB,
        None,
        None,
        None,
        Simplicity.ADVANCED,
        container,
        encoder,
        VideoPreset.ULTRA_FAST,
        23,
        NvencPreset.P5,
        23,
        additional,
        SimpleVideoFormat.MP4_H264,
        75,
        30,
        None,
        AudioSettings.AUTO,
        None,
        module.Matrix.AUTO,
        module.Transfer.AUTO,
        module.Primaries.AUTO,
        module.Range.AUTO,
        module.ChromaLocation.AUTO,
        bit_depth,
        SitingCorrection.AUTO,
    )
    for _ in range(2):
        collector.on_iterate((image, None, None, None))
    collector.on_complete()
    return tmp_path / f"out.{container.ext}"


def stream(path: Path) -> dict[str, Any]:
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    ).stdout
    return json.loads(probe)["streams"][0]


@pytest.mark.skipif(
    not Path(FFMPEG).exists(), reason="the integrated FFmpeg is not downloaded"
)
def test_nvenc_h264_mp4_round_trip(tmp_path: Path):
    require_nvenc(VideoEncoder.H264_NVENC, "yuv420p")
    path = save(tmp_path, bars(1280, 720), VideoFormat.MP4, VideoEncoder.H264_NVENC)
    info = stream(path)
    assert info["codec_name"] == "h264"
    assert info["pix_fmt"] == "yuv420p"
    assert (
        info.get("color_space"),
        info.get("color_primaries"),
        info.get("color_transfer"),
        info.get("color_range"),
    ) == ("bt709", "bt709", "bt709", "tv")


@pytest.mark.skipif(
    not Path(FFMPEG).exists(), reason="the integrated FFmpeg is not downloaded"
)
def test_nvenc_hevc_10bit_mkv(tmp_path: Path):
    require_nvenc(VideoEncoder.H265_NVENC, "yuv420p10le")
    path = save(
        tmp_path,
        bars(320, 240),
        VideoFormat.MKV,
        VideoEncoder.H265_NVENC,
        BitDepth.BITS_10,
    )
    info = stream(path)
    assert info["codec_name"] == "hevc"
    assert info["pix_fmt"] == "yuv420p10le"
    assert info.get("color_range") == "tv"


@pytest.mark.skipif(
    not Path(FFMPEG).exists(), reason="the integrated FFmpeg is not downloaded"
)
def test_nvenc_lossless_colour(tmp_path: Path):
    require_nvenc(VideoEncoder.H265_NVENC, "yuv420p")
    path = save(
        tmp_path,
        bars(320, 240),
        VideoFormat.MKV,
        VideoEncoder.H265_NVENC,
        additional="-tune lossless -rc constqp -qp 0",
    )
    info = stream(path)
    assert (
        info.get("color_space"),
        info.get("color_primaries"),
        info.get("color_transfer"),
        info.get("color_range"),
    ) == ("bt709", "bt709", "bt709", "tv")


@pytest.mark.skipif(
    not Path(FFMPEG).exists(), reason="the integrated FFmpeg is not downloaded"
)
@pytest.mark.parametrize("container", [VideoFormat.MP4, VideoFormat.WEBM])
def test_nvenc_av1(tmp_path: Path, container: VideoFormat):
    require_nvenc(VideoEncoder.AV1_NVENC, "yuv420p")
    path = save(tmp_path, bars(320, 240), container, VideoEncoder.AV1_NVENC)
    info = stream(path)
    assert info["codec_name"] == "av1"
    assert info["pix_fmt"] == "yuv420p"

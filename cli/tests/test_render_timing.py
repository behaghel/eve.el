"""Real-media regressions for sample-accurate cuts and assembly timestamps."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from eve_cli import alignment, subtitles, tjm_v11
from eve_cli.commands import text_edit, transcribe

pytestmark = [
    pytest.mark.media,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable"),
]


def streams(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    return {stream["codec_type"]: stream for stream in data["streams"]}


def pcm(path: Path) -> tuple[int, ...]:
    data = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "-",
        ]
    )
    return struct.unpack(f"<{len(data) // 2}h", data)


def test_joint_caption_plan_keeps_real_samples_and_frame_ids_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eve_cli.main import main

    rate = 48000
    samples = tuple((index * 73) % 20000 - 10000 for index in range(rate * 2))
    audio, source = tmp_path / "source.wav", tmp_path / "source.mp4"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=size=160x90:rate=60:duration=2,"
            "geq=lum='16+219*mod(floor(N/pow(2,floor(X/20))),2)':cb=128:cr=128",
            "-i",
            str(audio),
            "-c:v",
            "libx264",
            "-bf",
            "0",
            "-c:a",
            "alac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": "a",
                "source": "clip01",
                "start": 0.1,
                "end": 1.1,
                "words": [{"token": "first", "start": 0.2, "end": 1.1}],
            },
            {
                "id": "b",
                "source": "clip01",
                "start": 1.0,
                "end": 1.9,
                "words": [{"token": "second", "start": 1.0, "end": 1.8}],
            },
        ],
    )
    monkeypatch.setattr(
        alignment,
        "align_ctc",
        lambda *a, **kw: [
            alignment.WordAlignment(0, "first", Fraction("0.2"), Fraction("1.04")),
            alignment.WordAlignment(1, "second", Fraction("1.1"), Fraction("1.8")),
        ],
    )
    monkeypatch.setattr(
        alignment, "pause_diagnostics", lambda *a: {"status": "provisional"}
    )
    path = tmp_path / "edit.tjm.json"
    assert (
        main(
            [
                "transcribe",
                str(source),
                "--output",
                str(path),
                "--word-aligner",
                "ctc",
                "--max-segment-words",
                "0",
            ]
        )
        == 0
    )
    manifest = json.loads(path.read_text())
    tjm_v11.validate_manifest(manifest)
    assert (
        manifest["segments"][0]["end_tick"]
        == manifest["segments"][1]["start_tick"]
        == 51360
    )
    working = tmp_path / "render"
    working.mkdir()
    parts = text_edit.render_segments(
        text_edit.prepare_render_manifest(manifest, tmp_path),
        tmp_path,
        working,
        quality="final",
    )
    decoded = [pcm(part) for part in parts]
    source_ranges = [(4800, 51360), (51360, 91200)]
    assert sum(map(len, decoded)) == 86400
    for actual, (start, end) in zip(decoded, source_ranges):
        assert len(actual) == end - start
        assert actual[600:-600] == samples[start + 600 : end - 600]
    output = tmp_path / "joined.mp4"
    text_edit.concat_segments(
        parts, output, durations=[Fraction(len(p), rate) for p in decoded]
    )
    expected_pts = [
        p - Fraction("0.1")
        for p in subtitles.probe_frame_times(source)
        if Fraction("0.1") <= p < Fraction("1.9")
    ]
    actual_pts = subtitles.probe_frame_times(output)
    assert len(actual_pts) == len(expected_pts)

    # Each picture carries its source frame index in eight large luma blocks.
    # Check identity/order, not timestamp quantization by the existing encoder.
    def frame_ids(path: Path) -> list[int]:
        pixels = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-fps_mode",
                "passthrough",
                "-pix_fmt",
                "gray",
                "-f",
                "rawvideo",
                "-",
            ]
        )
        size = 160 * 90
        return [
            sum(
                (pixels[offset + 45 * 160 + 10 + bit * 20] > 128) << bit
                for bit in range(8)
            )
            for offset in range(0, len(pixels), size)
        ]

    assert frame_ids(source) == list(range(120))
    assert frame_ids(output) == list(range(6, 114))
    info = streams(output)["audio"]
    assert abs(
        int(info["duration_ts"]) * Fraction(info["time_base"]) - Fraction("1.8")
    ) <= Fraction(1, rate)


@pytest.mark.parametrize("sample_rate", [44100, 48000])
def test_transcription_audio_clock_keeps_leading_offset(
    tmp_path: Path,
    sample_rate: int,
) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=160x90:rate=60:duration=0.75",
            "-itsoffset",
            "0.125",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate={sample_rate}:duration=0.25",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-bf",
            "0",
            "-c:a",
            "alac",
            str(source),
        ],
        check=True,
    )
    assert transcribe.probe_audio_sample_rate(source) == sample_rate
    audio = tmp_path / "asr.wav"
    transcribe.ffmpeg_extract(source, audio)
    with wave.open(str(audio), "rb") as file:
        assert file.getframerate() == 16000
        assert abs(file.getnframes() - 6000) <= 1
        samples = struct.unpack(
            f"<{file.getnframes()}h", file.readframes(file.getnframes())
        )
    assert max(abs(value) for value in samples[:1600]) == 0
    assert max(abs(value) for value in samples[2400:3200]) > 1000
    from eve_cli.main import main

    output = tmp_path / "stub.tjm.json"
    assert main(["transcribe", str(source), "--output", str(output), "--stub"]) == 0
    manifest = json.loads(output.read_text())
    tjm_v11.validate_manifest(manifest)
    assert manifest["sources"][0]["timebase"] == {
        "numerator": 1,
        "denominator": sample_rate,
    }


def test_concat_rounds_cumulative_endpoints_not_each_clip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[Fraction] = []

    def capture(command: list[str], **_: Any) -> None:
        listing = Path(command[command.index("-i") + 1]).read_text()
        observed.extend(
            Fraction(line.split()[1])
            for line in listing.splitlines()
            if line.startswith("duration ")
        )

    monkeypatch.setattr(text_edit, "run_ffmpeg", capture)
    durations = [Fraction(1, 48000)] * 1000
    text_edit.concat_segments(
        [tmp_path / "clip.mp4"] * 1000, tmp_path / "final.mp4", durations=durations
    )
    assert len(observed) == 1000
    assert abs(sum(observed) - sum(durations)) <= Fraction(1, 2_000_000)


@pytest.mark.parametrize("burn_style", [None, "plain", "karaoke"])
def test_submillisecond_cuts_keep_samples_and_do_not_accumulate_padding(
    tmp_path: Path,
    burn_style: str | None,
) -> None:
    rate = 48000
    samples = tuple((index * 73) % 20000 - 10000 for index in range(rate * 2))
    audio = tmp_path / "source.wav"
    with wave.open(str(audio), "wb") as file:
        file.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        file.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=0x203040:size=160x90:rate=60:duration=2",
            "-i",
            str(audio),
            "-c:v",
            "libx264",
            "-c:a",
            "alac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    sample_ranges = [(4801, 12007), (24011, 31217), (48021, 55227)]
    ranges = [(start / rate, end / rate) for start, end in sample_ranges]
    cut = tmp_path / "cut.mp4"
    text_edit.render_source_ranges(
        source, ranges, cut, tmp_path, context="timing regression"
    )
    decoded = pcm(cut)
    expected = tuple(
        sample for start, end in sample_ranges for sample in samples[start:end]
    )
    assert len(decoded) == len(expected)
    # Edge fades are intentional. Interior samples must retain the exact source
    # positions, not a rounded millisecond offset or codec-induced extra samples.
    offset = 0
    for start, end in sample_ranges:
        length = end - start
        assert (
            decoded[offset + 600 : offset + length - 600]
            == samples[start + 600 : end - 600]
        )
        offset += length

    final = tmp_path / "final.mp4"
    text_edit.concat_segments(
        [cut] * 12, final, durations=[Fraction(len(expected), rate)] * 12
    )
    info = streams(final)
    assert Fraction(info["video"]["start_time"]) == 0
    assert Fraction(info["audio"]["start_time"]) == 0
    expected_duration = Fraction(len(expected) * 12, rate)
    actual_duration = int(info["audio"]["duration_ts"]) * Fraction(
        info["audio"]["time_base"]
    )
    assert abs(actual_duration - expected_duration) <= Fraction(1, rate)
    video_duration = int(info["video"]["duration_ts"]) * Fraction(
        info["video"]["time_base"]
    )
    assert abs(video_duration - expected_duration) <= Fraction(1, 60)
    if burn_style is None:
        # Exercise the public TJM path too: exact durations must reach final
        # assembly rather than only working when concat is called directly.
        from eve_cli.main import main

        word_ranges = [
            sample_ranges[0],
            (12007, 24011),
            sample_ranges[1],
            (31217, 48021),
            sample_ranges[2],
        ]
        manifest = {
            "version": "1.1",
            "sources": [
                {
                    "id": "s",
                    "file": "source.mp4",
                    "timebase": {"numerator": 1, "denominator": rate},
                }
            ],
            "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
            "segments": [
                {
                    "id": f"segment-{index}",
                    "source": "s",
                    "start_tick": 4801,
                    "end_tick": 55227,
                    "words": [
                        {
                            "spoken": f"word-{number}",
                            "start_tick": start,
                            "end_tick": end,
                            "edit": {"deleted": number in (1, 3)},
                        }
                        for number, (start, end) in enumerate(word_ranges)
                    ],
                }
                for index in range(2)
            ],
        }
        path = tmp_path / "edit.tjm.json"
        path.write_text(json.dumps(manifest))
        public_output = tmp_path / "public.mp4"
        assert (
            main(
                [
                    "text-edit",
                    str(path),
                    "--output",
                    str(public_output),
                    "--quality",
                    "final",
                    "--no-cache",
                ]
            )
            == 0
        )
        public_audio = streams(public_output)["audio"]
        public_duration = int(public_audio["duration_ts"]) * Fraction(
            public_audio["time_base"]
        )
        assert abs(public_duration - Fraction(len(expected) * 2, rate)) <= Fraction(
            1, rate
        )
        phase_manifest = {
            "version": "1.1",
            "sources": manifest["sources"],
            "render": {"filler_policy": "keep", "preserve_short_gaps": 0.6},
            "segments": [
                {"id": "a", "source": "s", "start_tick": 0, "end_tick": 26014},
                {"id": "b", "source": "s", "start_tick": 54814, "end_tick": 87848},
            ],
        }
        phase_path = tmp_path / "phase.tjm.json"
        phase_path.write_text(json.dumps(phase_manifest))
        phase_output = tmp_path / "phase.mp4"
        assert (
            main(
                [
                    "text-edit",
                    str(phase_path),
                    "--output",
                    str(phase_output),
                    "--quality",
                    "final",
                    "--no-cache",
                ]
            )
            == 0
        )
        phase_video = streams(phase_output)["video"]
        video_end = int(phase_video["duration_ts"]) * Fraction(phase_video["time_base"])
        assert 0 <= video_end - Fraction(87848, rate) <= Fraction(1, 60)
        return
    if shutil.which("fc-match") is None:
        pytest.skip("fontconfig required for the burning variants")
    for family in ("DejaVu Sans", "Arial", "Inter", "Liberation Sans"):
        try:
            font = subtitles.resolve_font(family, True)
            break
        except subtitles.SubtitleError:
            continue
    else:
        pytest.skip("No supported test font installed")
    times_before = subtitles.probe_frame_times(final)
    audio_before = pcm(final)
    config = {
        "mode": "burn",
        "style": burn_style,
        "font": {"family": family, "size": 48, "bold": True},
        "placement": {"anchor": "bottom-center", "bottom_margin": 60},
        "color": "#FFFFFF",
        "highlight_color": "#FFD700",
        "outline": {"color": "#000000", "width": 3},
    }
    captions = [
        subtitles.TimedCaption(
            Fraction(0),
            expected_duration,
            [subtitles.TimedToken("TIMING", [(Fraction(1, 10), Fraction(1, 5))])],
        )
    ]
    subtitles.burn_video(
        final,
        [(0, float(expected_duration), "TIMING")],
        config,
        160,
        90,
        text_edit.encoding_args("final"),
        font,
        timed_captions=captions,
        frame_rate="60/1",
    )
    assert subtitles.probe_frame_times(final) == times_before
    assert pcm(final) == audio_before
    burned_info = streams(final)
    assert burned_info["audio"]["duration_ts"] == info["audio"]["duration_ts"]
    assert Fraction(burned_info["video"]["start_time"]) == 0


def test_fractional_clock_fix_invalidates_old_clips_and_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unchanged source identity")
    item = {
        "type": "segment",
        "source_path": source,
        "segment": {"start": 0.05, "end": 1.04},
    }
    current = (
        text_edit.segment_cache_key(item, "final"),
        text_edit._render_config_hash("final", "h264", 1.0),
    )
    monkeypatch.setattr(text_edit, "TIMELINE_RENDER_VERSION", "sample-accurate-v1")
    old = (
        text_edit.segment_cache_key(item, "final"),
        text_edit._render_config_hash("final", "h264", 1.0),
    )
    assert all(new != stale for new, stale in zip(current, old))


@pytest.mark.parametrize("burn_style", [None, "plain", "karaoke"])
@pytest.mark.parametrize("source_b_frames", [0, 2])
def test_fractional_frame_cuts_keep_source_clock_audio_and_highlights(
    tmp_path: Path, burn_style: str | None, source_b_frames: int
) -> None:
    """A fractional cut retains native frame phase, not an invented zero/CFR grid."""
    import numpy as np

    from eve_cli.main import main

    rate, width, height = 48000, 320, 180
    ranges = [(2400, 49920), (118560, 212640), (220800, 283200)]
    events = [(24024, 32664), (144144, 152784), (240240, 248880)]
    samples = np.zeros(rate * 6, dtype=np.int16)
    for index, (start, end) in enumerate(events):
        time = np.arange(end - start) / rate
        samples[start:end] = np.rint(
            12000 * np.sin(2 * np.pi * ((900 + index * 400) * time + 900 * time**2))
        ).astype(np.int16)
    audio, source = tmp_path / "source.wav", tmp_path / "source.mp4"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=size=320x180:rate=30000/1001:duration=6,"
            "geq=lum='16+219*mod(floor(N/pow(2,floor(X/40))),2)':cb=128:cr=128",
            "-i",
            str(audio),
            "-c:v",
            "libx264",
            "-bf",
            str(source_b_frames),
            "-c:a",
            "alac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    original_source = source.read_bytes()
    source_times = subtitles.probe_frame_times(source)
    manifest = {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": source.name,
                "timebase": {"numerator": 1, "denominator": rate},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": [
            {
                "id": f"caption-{i}",
                "source": "s",
                "start_tick": start,
                "end_tick": end,
                "words": [
                    {"spoken": label, "start_tick": event[0], "end_tick": event[1]}
                ],
            }
            for i, ((start, end), event, label) in enumerate(
                zip(ranges, events, ("ALPHA", "BETA", "GAMMA"))
            )
        ],
    }
    if burn_style:
        if shutil.which("fc-match") is None:
            pytest.skip("fontconfig required")
        for family in ("DejaVu Sans", "Arial", "Inter", "Liberation Sans"):
            try:
                subtitles.resolve_font(family, True)
                break
            except subtitles.SubtitleError:
                continue
        else:
            pytest.skip("No supported test font installed")
        manifest["subtitles"] = {
            "mode": "burn",
            "style": burn_style,
            "reference_canvas": {"width": width, "height": height},
            "font": {"family": family, "size": 28, "bold": True},
            "placement": {"anchor": "bottom-center", "bottom_margin": 15},
            "color": "#FFFFFF",
            "highlight_color": "#FFD700",
            "outline": {"color": "#000000", "width": 1},
        }
    path, output = tmp_path / "edit.tjm.json", tmp_path / "output.mp4"
    path.write_text(json.dumps(manifest))
    original_manifest = path.read_bytes()
    assert (
        main(
            [
                "text-edit",
                str(path),
                "--output",
                str(output),
                "--quality",
                "final",
                "--no-cache",
            ]
            + (["--subtitles"] if burn_style else [])
        )
        == 0
    )

    def pictures(video: Path) -> np.ndarray:
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-fps_mode",
                "passthrough",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ]
        )
        return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 3)

    def frame_ids(frames: np.ndarray) -> list[int]:
        return [
            sum((int(frame[20, 20 + bit * 40, 0]) > 128) << bit for bit in range(8))
            for frame in frames
        ]

    assert frame_ids(pictures(source)) == list(range(len(source_times)))
    expected_ids, expected_times, intervals, expected_audio = [], [], [], []
    cursor = Fraction(0)
    for (start, end), (on, off) in zip(ranges, events):
        left, right = Fraction(start, rate), Fraction(end, rate)
        for index, time in enumerate(source_times):
            if left <= time < right:
                expected_ids.append(index)
                expected_times.append(cursor + time - left)
        intervals.append(
            (cursor + Fraction(on - start, rate), cursor + Fraction(off - start, rate))
        )
        expected_audio.extend(samples[start:end])
        cursor += right - left
    frames = pictures(output)
    assert frame_ids(frames) == expected_ids
    actual_times = subtitles.probe_frame_times(output)
    assert len(actual_times) == len(expected_times)
    info = streams(output)
    tolerance = Fraction(info["video"]["time_base"]) + Fraction(1, 1000000)
    assert all(
        abs(actual - expected) <= tolerance
        for actual, expected in zip(actual_times, expected_times)
    )
    # Nonzero first-frame PTS is legitimate; its source-relative phase is not
    # permission for additional encoder/movie-timescale rounding.
    assert abs(actual_times[0] - Fraction(251, 15000)) <= tolerance
    audio_duration = int(info["audio"]["duration_ts"]) * Fraction(
        info["audio"]["time_base"]
    )
    assert abs(audio_duration - cursor) <= Fraction(1, rate)
    assert Fraction(info["audio"]["start_time"]) == 0
    decoded = np.array(pcm(output), dtype=np.float64)
    expected_signal = np.array(expected_audio, dtype=np.float64)
    for on, off in intervals:
        left, right = round(on * rate) + 600, round(off * rate) - 600
        margin = 1600
        template = expected_signal[left:right]
        search = decoded[left - margin : right + margin]
        correlation = np.correlate(search, template, mode="valid")
        lag = int(np.argmax(correlation)) - margin
        assert abs(lag) <= 1
    if burn_style:
        for frame, time in zip(frames, actual_times):
            rgb = frame[height // 2 :].astype(np.int16)
            gold = (
                (rgb[:, :, 0] > 180)
                & (rgb[:, :, 1] > 100)
                & (rgb[:, :, 1] < 240)
                & (rgb[:, :, 2] < 70)
            )
            active = burn_style == "karaoke" and any(
                on <= time < off for on, off in intervals
            )
            assert (int(np.count_nonzero(gold)) > 10) is active
    assert source.read_bytes() == original_source
    assert path.read_bytes() == original_manifest

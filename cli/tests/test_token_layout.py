"""Known text separators are layout, not word identity or audio alignment."""

import re
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from eve_cli import subtitles, tjm_v11
from eve_cli.commands import export_transcript, text_edit, transcribe

CASES = [
    (" I don't know", ["I", "do", "n't", "know"]),
    (" C'est-à-dire", ["C", "'est", "-à", "-dire"]),
    (" well-being matters", ["well", "-", "being", "matters"]),
    (" rock 'n' roll", ["rock", "'n'", "roll"]),
    (" run -f --verbose", ["run", "-f", "--verbose"]),
    ("今日も晴れ", ["今日", "も", "晴れ"]),
]


def backend(text, fragments):
    return SimpleNamespace(
        start=0,
        end=len(fragments),
        text=text,
        words=[
            SimpleNamespace(word=fragment, start=i, end=i + 1)
            for i, fragment in enumerate(fragments)
        ],
    )


def produced(text, fragments, max_words=2):
    raw = transcribe.segment_to_dict("s", 1, backend(text, fragments), 0)
    regrouped = transcribe.resegment_naturally(raw, max_words)
    return {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "source.wav",
                "timebase": {"numerator": 1, "denominator": 1000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": transcribe._v11_segments(regrouped, {"s": 1000}),
    }


@pytest.mark.parametrize("text,fragments", CASES)
def test_literal_layout_survives_production_and_both_subtitle_styles(text, fragments):
    data = produced(text, fragments)
    before = deepcopy(data)
    tjm_v11.validate_manifest(data)
    words = [w for s in data["segments"] for w in s["words"]]
    assert [w["spoken"] for w in words] == fragments
    assert [(w["start_tick"], w["end_tick"]) for w in words] == [
        (i * 1000, (i + 1) * 1000) for i in range(len(words))
    ]
    reconstructed = "".join(w.get("separator_before", "") + w["spoken"] for w in words)
    assert reconstructed.strip() == text.strip()
    assert not any(key.startswith("_") for word in words for key in word)
    prepared = text_edit.prepare_render_manifest(data, Path("."))
    cues = text_edit.build_subtitle_cues(prepared)
    captions = text_edit.build_token_captions(prepared)
    for segment, cue, caption in zip(data["segments"], cues, captions, strict=True):
        assert cue[2] == export_transcript._text(segment)
        event = subtitles._timed_events(
            [caption], {"color": "#FFFFFF", "highlight_color": "#FFFF00"}, Fraction(25)
        )
        assert cue[2] in re.sub(r"\{[^}]*\}", "", event)
    assert data == before


@pytest.mark.parametrize("text,fragments", CASES[:3])
def test_automatic_caption_boundaries_do_not_split_connected_fragments(text, fragments):
    data = produced(text, fragments, max_words=1)
    for left, right in zip(data["segments"], data["segments"][1:]):
        assert right["words"][0]["separator_before"] != ""
    assert data["segments"][0]["start_tick"] == 0
    assert data["segments"][-1]["end_tick"] == len(fragments) * 1000


def test_explicit_separator_overrides_legacy_heuristic_and_survives_display_edit():
    data = produced(" j 'quoted' next", ["j", "'quoted'", "next"], 10)
    word = data["segments"][0]["words"][1]
    word["display"] = "'replacement'"
    data["segments"][0].pop("display_text")
    prepared = text_edit.prepare_render_manifest(data, Path("."))
    assert text_edit.build_subtitle_cues(prepared)[0][2] == "j 'replacement' next"
    word["edit"] = {"deleted": True}
    prepared = text_edit.prepare_render_manifest(data, Path("."))
    assert text_edit.build_subtitle_cues(prepared)[0][2] == "j next"


@pytest.mark.parametrize("max_duration", [0, 0.6])
def test_layout_caption_regrouping_preserves_known_media_envelope_and_gaps(
    max_duration,
):
    sdk = backend(" alpha don't beta", ["alpha", "do", "n't", "beta"])
    sdk.end = 3
    for word, bounds in zip(sdk.words, [(0.5, 1), (1, 1.4), (1.5, 2), (2.2, 2.5)]):
        word.start, word.end = bounds
    raw = transcribe.segment_to_dict("s", 1, sdk, max_duration)
    before = deepcopy([word for segment in raw for word in segment["words"]])
    sdk.layout_known = False
    legacy = transcribe.resegment_naturally(
        transcribe.segment_to_dict("s", 1, sdk, max_duration), 1
    )
    grouped = transcribe.resegment_naturally(raw, 1)

    def coverage(groups):
        return text_edit.merge_ranges([(s["start"], s["end"]) for s in groups])

    assert coverage(grouped) == coverage(legacy)
    assert grouped[0]["start"] == legacy[0]["start"]
    assert grouped[-1]["end"] == legacy[-1]["end"]
    assert [word for group in grouped for word in group["words"]] == before


def test_unspaced_writing_keeps_native_envelopes_as_caption_boundaries():
    first = backend("今日も晴れ", ["今日", "も", "晴れ"])
    second = backend("明日も晴れ", ["明日", "も", "晴れ"])
    second.start, second.end = 3, 6
    for word in second.words:
        word.start += 3
        word.end += 3
    raw = transcribe.segment_to_dict("s", 1, first, 0) + transcribe.segment_to_dict(
        "s", 2, second, 0
    )
    grouped = transcribe.resegment_naturally(raw, 1)
    assert len(grouped) == 2
    canonical = transcribe._v11_segments(grouped, {"s": 1000})
    assert [s["display_text"] for s in canonical] == ["今日も晴れ", "明日も晴れ"]
    assert [(s["start_tick"], s["end_tick"]) for s in canonical] == [
        (0, 3000),
        (3000, 6000),
    ]


def test_layout_does_not_mask_missing_word_timing_diagnostic():
    first = backend(" hello", ["hello"])
    second = backend(" world", ["world"])
    second.start, second.end = 1, 2
    second.words[0].start, second.words[0].end = None, 2
    raw = transcribe.segment_to_dict("s", 1, first, 0) + transcribe.segment_to_dict(
        "s", 2, second, 0
    )
    with pytest.raises(tjm_v11.ValidationError, match="world.*missing"):
        grouped = transcribe.resegment_naturally(raw, 8)
        transcribe._v11_segments(grouped, {"s": 1000})


def test_joined_layout_does_not_mask_nonfinite_word_timing_diagnostic():
    sdk = backend("helloworld", ["hello", "world"])
    sdk.words[1].start = float("nan")
    raw = transcribe.segment_to_dict("s", 1, sdk, 0)
    with pytest.raises(tjm_v11.ValidationError, match="world.*non-finite"):
        grouped = transcribe.resegment_naturally(raw, 1)
        transcribe._v11_segments(grouped, {"s": 1000})


def test_inexact_literal_mapping_does_not_invent_separators_or_rewrite_words():
    raw = transcribe.segment_to_dict(
        "s", 1, backend("different text", ["original", "words"]), 0
    )
    assert [w["token"] for w in raw[0]["words"]] == ["original", "words"]
    assert not any("separator_before" in w for w in raw[0]["words"])


@pytest.mark.parametrize("value", [None, False, 3, "letters", "\n", "\r", "\u2028"])
def test_malformed_layout_is_rejected(value):
    data = produced(" hello world", ["hello", "world"])
    data["segments"][0]["words"][0]["separator_before"] = value
    with pytest.raises(tjm_v11.ValidationError, match="separator_before"):
        tjm_v11.validate_manifest(data)

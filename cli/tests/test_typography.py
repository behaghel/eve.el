"""French display joins must not merge or retime recognized tokens."""

import re
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest

from eve_cli import subtitles
from eve_cli.commands import export_transcript, text_edit, transcribe
from eve_cli.utils.typography import join_display_tokens


@pytest.mark.parametrize(
    "tokens, expected",
    [
        (["J", "'espère", "l’", "écouter"], "J'espère l’écouter"),
        (["c", "'", "est"], "c'est"),
        (["qu", "’il", "m", "'écoute"], "qu’il m'écoute"),
        (["aujourd", "'hui"], "aujourd'hui"),
        (["Elle", "dit", "'bonjour'"], "Elle dit 'bonjour'"),
        (["rock", "'n'", "roll"], "rock 'n' roll"),
        (["two written words", "next"], "two written words next"),
        (["j'espère", "écouter"], "j'espère écouter"),
    ],
)
def test_display_join(tokens, expected):
    before = deepcopy(tokens)
    assert join_display_tokens(tokens) == expected
    assert tokens == before


def test_existing_french_word_caches_render_without_rewriting_source():
    words = [
        {"spoken": token, "start_tick": i * 100, "end_tick": (i + 1) * 100}
        for i, token in enumerate(["J", "'espère", "l’", "écouter"])
    ]
    manifest = {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "s.wav",
                "timebase": {"numerator": 1, "denominator": 1000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": [
            {
                "id": "s1",
                "source": "s",
                "start_tick": 0,
                "end_tick": 400,
                "spoken_text": "J 'espère l’ écouter",
                "display_text": "J 'espère l’ écouter",
                "words": words,
            }
        ],
    }
    before = deepcopy(manifest)
    prepared = text_edit.prepare_render_manifest(manifest, Path("."))
    assert text_edit.build_subtitle_cues(prepared) == [(0, 0.4, "J'espère l’écouter")]
    captions = text_edit.build_token_captions(prepared)
    assert len(captions[0].tokens) == 4
    assert captions[0].tokens[1].intervals == [(Fraction(1, 10), Fraction(1, 5))]
    config = {"color": "#FFFFFF", "highlight_color": "#FFFF00"}
    events = subtitles._timed_events(captions, config, Fraction(25))
    # Per-token ASS tags may intervene; the visible text must not gain spaces.
    assert "J'espère l’écouter" in re.sub(r"\{[^}]*\}", "", events)
    assert manifest == before


def test_transcription_formats_display_but_preserves_native_spoken_tokens():
    words = [
        {"token": token, "start": i / 10, "end": (i + 1) / 10}
        for i, token in enumerate(["J", "'espère", "l’", "écouter"])
    ]
    asr = [{"id": "s1", "source": "s", "start": 0, "end": 0.4, "words": words}]
    before = deepcopy(asr)
    segment = transcribe._v11_segments(asr, {"s": 1000})[0]
    assert segment["spoken_text"] == "J 'espère l’ écouter"
    assert segment["display_text"] == "J'espère l’écouter"
    assert [w["spoken"] for w in segment["words"]] == [w["token"] for w in words]
    assert [(w["start_tick"], w["end_tick"]) for w in segment["words"]] == [
        (0, 100),
        (100, 200),
        (200, 300),
        (300, 400),
    ]
    assert asr == before


def test_source_export_keeps_approved_caption_over_derived_typography():
    segment = {
        "id": "s",
        "display_text": "Texte approuvé : j 'espère.",
        "words": [{"spoken": "j"}, {"spoken": "'espère"}],
    }
    assert export_transcript._text(segment) == segment["display_text"]
    del segment["display_text"]
    assert export_transcript._text(segment) == "j'espère"

#!/usr/bin/env python3
"""Extract integer lattice boundaries before MFA's export/EOF adjustments.

Runs only in the separately locked MFA environment. No TextGrid timestamps,
interpolation, silence-edge snapping or utterance-end stretching are used.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

from _kalpy.lat import (
    CompactLatticeToWordAlignment,
    WordAlignLatticeLexiconInfo,
    WordAlignLatticeLexiconOpts,
    linear_to_lattice,
    word_align_lattice_lexicon,
)
from kalpy.gmm.data import AlignmentArchive
from kalpy.models import AcousticModel


def decode(alignment, transition_model, lexicon, symbols) -> list[dict]:
    lattice = linear_to_lattice(alignment.alignment, alignment.words)
    success, lattice = word_align_lattice_lexicon(
        lattice, transition_model, lexicon, WordAlignLatticeLexiconOpts()
    )
    if not success:
        raise RuntimeError("Cannot align native word lattice")
    success, word_ids, starts, lengths = CompactLatticeToWordAlignment(lattice)
    if not success:
        raise RuntimeError("Cannot extract integer word boundaries")
    # This view borrows the current archive record: consume before advancing.
    scores = alignment.per_frame_likelihoods.numpy()
    entries = []
    for word_id, start, length in zip(word_ids, starts, lengths):
        label = symbols[word_id]
        if label == "<eps>":
            continue  # Explicit model silence, not a dropped transcript token.
        start, end = int(start), int(start + length)
        if end <= start:
            raise RuntimeError(
                f"Native lattice has a non-positive interval for {label!r}"
            )
        score = float(scores[start:end].mean())
        if not math.isfinite(score):
            raise RuntimeError("Native acoustic score is not finite")
        entries.append(
            {"text": label, "start_frame": start, "end_frame": end, "score": score}
        )
    return entries


def extract(
    state: Path, metadata: Path, *, batch: bool = False, adapted: bool = False
) -> dict:
    names = {}
    with sqlite3.connect(f"file:{state / 'corpus.db'}?mode=ro", uri=True) as database:
        utterances = list(database.execute("SELECT begin FROM utterance"))
        if not utterances or any(begin != 0 for (begin,) in utterances):
            raise RuntimeError(
                "Benchmark requires whole-file utterances at offset zero"
            )
        if not batch and len(utterances) != 1:
            raise RuntimeError("Benchmark requires one whole-file utterance")
        if batch:
            names = {
                f"{speaker}-{identifier}": name
                for identifier, speaker, name in database.execute(
                    "SELECT utterance.id, utterance.speaker_id, file.name "
                    "FROM utterance "
                    "JOIN file ON file.id=utterance.file_id"
                )
            }
        phones = dict(database.execute("SELECT kaldi_label, mapping_id FROM phone"))
        symbols = dict(database.execute("SELECT mapping_id, word FROM word"))
        lexicon = [
            [word_id, word_id, *(phones[phone] for phone in pronunciation.split())]
            for word_id, pronunciation in database.execute(
                "SELECT word.mapping_id, pronunciation.pronunciation "
                "FROM word JOIN pronunciation ON word.id=pronunciation.word_id"
            )
        ]
    prefix = "ali" if adapted else "ali_first_pass"
    archives = list((state / "alignment").glob(f"{prefix}.*.ark"))
    if len(archives) != 1:
        raise RuntimeError("Expected one selected native alignment archive")
    archive = archives[0]
    words = archive.with_name(
        archive.name.replace(
            prefix + ".", "words." if adapted else "words_first_pass.", 1
        )
    )
    likelihoods = archive.with_name(
        archive.name.replace(
            prefix + ".", "likelihoods." if adapted else "likelihoods_first_pass.", 1
        )
    )
    reader = AlignmentArchive(archive, words, likelihoods)
    model = AcousticModel(state / "alignment")
    information = WordAlignLatticeLexiconInfo(lexicon)
    records = []
    by_name = {}
    try:
        for alignment in reader:
            entries = decode(alignment, model.transition_model, information, symbols)
            records.append(entries)
            if batch:
                name = names[str(alignment.utterance_id)]
                if name in by_name:
                    raise RuntimeError("Duplicate native utterance")
                by_name[name] = entries
    finally:
        reader.close()
    quantum = json.loads(metadata.read_text())["features"]["frame_shift"]
    if batch:
        if set(by_name) != set(names.values()):
            raise RuntimeError("Missing native utterance alignments; no tokens dropped")
        return {"frame_shift_ms": quantum, "utterances": by_name}
    if len(records) != 1:
        raise RuntimeError("MFA did not produce exactly one native alignment")
    return {"frame_shift_ms": quantum, "words": records[0]}


if __name__ == "__main__":
    Path(sys.argv[3]).write_text(
        json.dumps(
            extract(
                Path(sys.argv[1]),
                Path(sys.argv[2]),
                batch="--batch" in sys.argv[4:],
                adapted="--adapted" in sys.argv[4:],
            ),
            indent=2,
        )
        + "\n"
    )

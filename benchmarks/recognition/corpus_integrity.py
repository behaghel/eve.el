"""Verify the checked-in corpus without model calls or accuracy grading."""
import hashlib
import json
import sys
import wave
from collections import Counter
from pathlib import Path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local_path(root, name):
    root = Path(root).resolve()
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Asset must be repository-relative: {name}")
    result = (root / path).resolve()
    if not result.is_relative_to(root):
        raise ValueError(f"Asset escapes corpus: {name}")
    return result


def verify_asset(root, name, record):
    path = local_path(root, name)
    if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
        raise ValueError(f"Changed asset: {name}")
    if "wave" in record:
        with wave.open(str(path), "rb") as stream:
            actual = {
                "channels": stream.getnchannels(),
                "sample_width": stream.getsampwidth(),
                "sample_rate": stream.getframerate(),
                "frames": stream.getnframes(),
            }
        if actual != record["wave"]:
            raise ValueError(f"Changed native audio clock/format: {name}")


def load(root, name):
    return json.loads(local_path(root, name).read_text(encoding="utf-8"))


def verify_corpus(root):
    root = Path(root).resolve()
    manifest = load(root, "corpus.json")
    for name, record in manifest["assets"].items():
        verify_asset(root, name, record)
    words = {}
    seconds = 0
    for case in manifest["cases"]:
        if case["id"] in words:
            raise ValueError("Duplicate corpus case")
        if case["audio"] not in manifest["assets"] or case["reference"] not in manifest["assets"]:
            raise ValueError("Unsealed case input")
        audio = manifest["assets"][case["audio"]]["wave"]
        if audio["sample_rate"] != case["sample_rate"] or audio["frames"] != case["frames"]:
            raise ValueError("Case/audio clock mismatch")
        seconds += audio["frames"] / audio["sample_rate"]
        reference = load(root, case["reference"])
        if case["reference_case_id"] is None:
            tokens = reference["words"]
        else:
            matches = [c for c in reference["cases"] if c["id"] == case["reference_case_id"]]
            if len(matches) != 1:
                raise ValueError("Missing/duplicate reference case")
            tokens = matches[0]["tokens"]
        words[case["id"]] = tokens
    ledger = load(root, manifest["presence_ledger"])["occurrences"]
    seen_ids, seen_positions = set(), set()
    classes = Counter()
    eligible_fillers = 0
    for item in ledger:
        position = (item["source_id"], item["reference_index"])
        if item["id"] in seen_ids or position in seen_positions:
            raise ValueError("Duplicate occurrence identity/position")
        seen_ids.add(item["id"])
        seen_positions.add(position)
        if position[0] not in words or not 0 <= position[1] < len(words[position[0]]):
            raise ValueError("Unknown/out-of-range occurrence")
        if words[position[0]][position[1]] != item["literal_token"]:
            raise ValueError("Reference/ledger literal mismatch")
        classes[item["eligibility"]] += 1
        if item["eligibility"] == "eligible_development_positive":
            token = item["literal_token"].casefold().strip(".,!?;:\"'“”‘’…")
            eligible_fillers += token in ("um", "uh")
    if len(ledger) != sum(map(len, words.values())):
        raise ValueError("Missing reference occurrence")
    labels = load(root, manifest["presence_labels"])["negative_cases"]
    if len({item["id"] for item in labels}) != len(labels):
        raise ValueError("Duplicate negative case")
    for item in labels:
        if item["source_id"] not in words:
            raise ValueError("Unknown negative source")
        if "evaluation_audio" in item and item["evaluation_audio"] not in manifest["assets"]:
            raise ValueError("Unsealed negative audio")
    counts = {
        "main_cases": len(words),
        "main_audio_seconds": seconds,
        "reference_slots": len(ledger),
        "eligible_occurrences": classes["eligible_development_positive"],
        "eligible_fillers": eligible_fillers,
        "quarantined_occurrences": classes["quarantined_reference_not_in_definitive_metrics"],
        "clear_negative_cases": sum(item["reference_class"] == "clear_absent" for item in labels),
    }
    if counts != manifest["expected_counts"] or sum(classes.values()) != len(ledger):
        raise ValueError("Corpus roster/count mismatch")
    return {"status": "integrity_verified", "corpus_id": manifest["id"], **counts,
            "verified_assets": len(manifest["assets"]), "accuracy_qualified": False,
            "model_calls": 0, "accuracy_grades": 0}


if __name__ == "__main__":
    try:
        print(json.dumps(verify_corpus(Path(__file__).resolve().parent), indent=2))
    except (ValueError, KeyError, OSError, wave.Error) as error:
        print(f"Corpus integrity failed: {error}", file=sys.stderr)
        raise SystemExit(2)

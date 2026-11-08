"""Integrity tests only: no recognition, alignment or model scoring."""
import hashlib
import tempfile
import unittest
import wave
from pathlib import Path

from corpus_integrity import verify_asset, verify_corpus


class CorpusIntegrityTests(unittest.TestCase):
    def make_wav(self, root):
        path = root / "sample.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00\x00\x01\x00")
        return {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "wave": {"channels": 1, "sample_width": 2, "sample_rate": 16000, "frames": 2},
        }

    def test_exact_wave_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verify_asset(root, "sample.wav", self.make_wav(root))

    def test_changed_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.make_wav(root)
            with (root / "sample.wav").open("ab") as stream:
                stream.write(b"changed")
            with self.assertRaises(ValueError):
                verify_asset(root, "sample.wav", record)

    def test_wrong_native_sample_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.make_wav(root)
            record["wave"]["frames"] = 3
            with self.assertRaises(ValueError):
                verify_asset(root, "sample.wav", record)

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                verify_asset(Path(directory), "missing.wav", {"bytes": 0, "sha256": "0" * 64})

    def test_parent_and_absolute_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("../outside", str(Path(directory) / "absolute")):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    verify_asset(Path(directory), name, {})

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            (root / "escape").symlink_to(Path(directory))
            with self.assertRaises(ValueError):
                verify_asset(root, "escape/outside", {})

    def test_repository_corpus_is_complete(self):
        result = verify_corpus(Path(__file__).resolve().parent)
        self.assertEqual(result["main_cases"], 4)
        self.assertEqual(result["main_audio_seconds"], 125.5)
        self.assertEqual(result["reference_slots"], 364)
        self.assertEqual(result["eligible_occurrences"], 343)
        self.assertEqual(result["eligible_fillers"], 25)
        self.assertEqual(result["quarantined_occurrences"], 21)
        self.assertEqual(result["clear_negative_cases"], 20)
        self.assertFalse(result["accuracy_qualified"])


if __name__ == "__main__":
    unittest.main()

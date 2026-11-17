import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from alart.release import apply, pack


class ReleaseTests(unittest.TestCase):
    def test_pack_and_apply_preserve_installation_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, destination = base / "source", base / "destination"
            (source / "alart").mkdir(parents=True)
            destination.mkdir()
            (source / "VERSION").write_text("v2")
            (source / "alart" / "main.py").write_text("new source")
            (source / ".env").write_text("new secret")
            (destination / ".env").write_text("existing secret")
            archive = base / "release.tar.gz"
            pack(archive, source)
            apply(archive, destination)
            self.assertEqual((destination / "VERSION").read_text(), "v2")
            self.assertEqual((destination / "alart" / "main.py").read_text(), "new source")
            self.assertEqual((destination / ".env").read_text(), "existing secret")

    def test_rejects_path_traversal_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for name in ("VERSION", "../.env"):
                    data = b"bad"
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    bundle.addfile(entry, io.BytesIO(data))
            with self.assertRaises(ValueError):
                apply(archive, base)
            self.assertFalse((base / "VERSION").exists())


if __name__ == "__main__":
    unittest.main()

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from alart.private_archive import pack


class PrivateArchiveTests(unittest.TestCase):
    def test_only_private_receipts_are_packed(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            receipts=root/"receipts"
            receipts.mkdir()
            file=receipts/("a"*32+".receipt")
            file.write_bytes(b"private")
            output=io.BytesIO()
            pack(root,output)
            output.seek(0)
            with tarfile.open(fileobj=output,mode="r:gz") as archive:
                self.assertEqual(archive.getnames(),["receipts/"+file.name])
                self.assertEqual(archive.extractfile(archive.getmembers()[0]).read(),b"private")


if __name__=="__main__": unittest.main()

"""Stream private receipt files into an archive for encrypted VPS backups."""
import os
import re
import sys
import tarfile
from pathlib import Path

RECEIPT_NAME=re.compile(r"^[0-9a-f]{32}\.receipt$")

def pack(root:Path,output):
    receipts=root/"receipts"
    with tarfile.open(fileobj=output,mode="w|gz") as archive:
        for path in sorted(receipts.iterdir()) if receipts.exists() else []:
            if not RECEIPT_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                raise ValueError("Unexpected private receipt file")
            if path.stat().st_size>3*1024*1024:
                raise ValueError("Private receipt exceeds upload limit")
            archive.add(path,arcname="receipts/"+path.name,recursive=False)
        guides=root/'guides'
        for path in sorted(guides.iterdir()) if guides.exists() else []:
            if not re.fullmatch(r'[0-9a-f]{32}\.(mp4|webm)',path.name) or path.is_symlink() or not path.is_file() or path.stat().st_size>50*1024*1024:
                raise ValueError('Unexpected guide video file')
            archive.add(path,arcname='guides/'+path.name,recursive=False)

if __name__=="__main__":
    if sys.argv[1:]!=["pack"]:
        raise SystemExit("Usage: python -m alart.private_archive pack")
    pack(Path(os.environ.get("PRIVATE_DATA_DIR","/srv/data")),sys.stdout.buffer)

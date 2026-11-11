"""Package only version-controlled server files and the frontend build."""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("revision")
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", args.revision + "^{commit}"], text=True).strip()
    paths = ["backend", "db", "docs", "deploy/ci/check_assets.py", "deploy/ci/idle.py"]
    tree = subprocess.check_output(["git", "ls-tree", "-rz", sha, "--", *paths])
    entries = []
    for row in tree.split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            name = name.decode()
            if mode not in (b"100644", b"100755") or kind != b"blob" or Path(name).name == ".env":
                raise ValueError(f"Unsupported release file: {name}")
            entries.append((oid, name))
    contents = subprocess.run(["git", "cat-file", "--batch"], check=True,
                              input=b"\n".join(oid for oid, _ in entries) + b"\n", capture_output=True)
    stream = io.BytesIO(contents.stdout)
    files = {}
    archive = None if args.baseline else tarfile.open(args.output, "w:gz")
    def put(name, data):
        item = tarfile.TarInfo(name)
        item.size = len(data)
        archive.addfile(item, io.BytesIO(data))
    try:
        for oid, name in entries:
            got, kind, size = stream.readline().split()
            if (got, kind) != (oid, b"blob"):
                raise ValueError("Git object mismatch")
            data = stream.read(int(size))
            if stream.read(1) != b"\n":
                raise ValueError("Truncated Git object")
            files[name] = hashlib.sha256(data).hexdigest()
            if archive:
                put("source/" + name, data)
        manifest = json.dumps({"commit": sha, "files": files}, sort_keys=True).encode()
        if archive:
            root = Path("frontend/dist")
            if not (root / "index.html").is_file():
                raise ValueError("Missing frontend build")
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    put("web/" + path.relative_to(root).as_posix(), path.read_bytes())
            put("manifest.json", manifest)
        else:
            args.output.write_bytes(manifest)
    finally:
        if archive:
            archive.close()

if __name__ == "__main__":
    main()

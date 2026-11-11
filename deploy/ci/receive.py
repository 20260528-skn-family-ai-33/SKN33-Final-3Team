"""EC2 release receiver. Installed by an administrator, outside application releases."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

import boto3

APP = Path("/opt/trade/app")
STATIC = Path("/srv/trade-web")
STATE = Path("/opt/trade/delivery")
BACKUPS = Path("/opt/trade/rollback")
MARKER = Path("/opt/trade/deployed-main.json")
BASE = STATE / "baseline.json"
JOURNAL = STATE / "transaction.json"
API = "/opt/trade/releases/c1af084/venv-api/bin/python"
COLLECTOR = "/opt/trade/venv/bin/python"
ML = "/opt/trade/venv-ml-c1af084/bin/python"
UNITS = ("trade-api", "trade-worker", "trade-scheduler")
BUCKET = "skn33-team3-trade-876143322800-ap-northeast-2"

def read(path):
    return json.loads(path.read_bytes())

def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)

def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def allowed(name):
    return (name.startswith(("backend/", "db/", "docs/")) or name in ("deploy/ci/check_assets.py", "deploy/ci/idle.py")) and Path(name).name != ".env"

def confined(root, name):
    if not name or Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
        raise ValueError("invalid release path")
    target = root / name
    if target.resolve() != target:
        raise ValueError("symbolic link in release path")
    return target

def unpack(archive, stage):
    total = 0
    seen = set()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            name = member.name
            if not member.isfile() or name in seen or not (name == "manifest.json" or name.startswith("web/") or
                                                          name.startswith("source/") and allowed(name[7:])):
                raise ValueError("unexpected bundle member")
            total += member.size
            if total > 512 * 1024**2:
                raise ValueError("release too large")
            seen.add(name)
            path = confined(stage, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as src, path.open("xb") as dst:
                shutil.copyfileobj(src, dst)
    manifest = read(stage / "manifest.json")
    if "web/index.html" not in seen:
        raise ValueError("missing frontend entry point")
    if set(manifest["files"]) != {name[7:] for name in seen if name.startswith("source/")}:
        raise ValueError("invalid source inventory")
    for name, digest in manifest["files"].items():
        if checksum(confined(stage / "source", name)) != digest:
            raise ValueError("source checksum mismatch")
    return manifest

def service(*args):
    subprocess.run(["systemctl", *args], check=True, timeout=160)

def trade(python, args, cwd, timeout=240):
    # Running candidate code as the application user also tests its real filesystem permissions.
    return subprocess.run(["runuser", "-u", "trade", "--", "env", "PYTHONDONTWRITEBYTECODE=1",
                           "HOME=/opt/trade/app/runtime", python, *args], cwd=cwd, timeout=timeout).returncode

def atomic_copy(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
        with src.open("rb") as stream:
            shutil.copyfileobj(stream, tmp)
    os.chmod(tmp.name, 0o644)
    os.replace(tmp.name, dest)

def ready():
    for _ in range(35):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=3) as response:
                if json.load(response)["status"] == "ready":
                    # With multiple units systemctl succeeds if *any* one is active.
                    for unit in (*UNITS, "nginx"):
                        service("is-active", "--quiet", unit)
                    return
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
        time.sleep(2)
    raise RuntimeError("readiness failed")

def rollback():
    tx = read(JOURNAL)
    backup = Path(tx["backup"])
    if tx["web"]:
        service("stop", *UNITS)
    for item in tx["paths"]:
        root = STATIC if item.startswith("web/") else APP
        dest = confined(root, item.split("/", 1)[1])
        old = backup / item
        if old.exists():
            atomic_copy(old, dest)
        else:
            dest.unlink(missing_ok=True)
    atomic_copy(backup / "baseline.json", BASE)
    atomic_copy(backup / "previous-deployed-main.json", MARKER)
    if tx["web"]:
        service("start", *UNITS)
        ready()
    JOURNAL.unlink()

def deploy(stage, manifest, web, status, expires):
    source = stage / "source"
    old = read(BASE)
    if read(MARKER)["commit"] != old["commit"]:
        raise RuntimeError("deployment marker differs from inventory")
    for name in (".env", "data", "runtime", "chroma_data"):
        (source / name).symlink_to(APP / name)
    status[0] = "production asset checks"
    if web:
        code = trade(API, ["deploy/ci/check_assets.py"], source)
        if code == 4:
            status.append("optional model B unavailable")
        elif code:
            status[0] = {11: "DB schema", 12: "required model A/registry", 13: "RAG article index"}.get(code, "asset loader")
            raise RuntimeError("asset or schema check failed")
        if trade(COLLECTOR, ["-c", "from backend.app.scheduler.main import build_scheduler; build_scheduler()"], source):
            raise RuntimeError("scheduler import failed")
    elif trade(ML, ["-c", "from backend.app.ml.candidate_models import run"], source):
        raise RuntimeError("training runtime import failed")
    if old["commit"] == manifest["commit"]:
        if web:
            ready()
        return True
    status[0] = "source integrity"
    changed = sorted(n for n in old["files"].keys() | manifest["files"].keys()
                     if old["files"].get(n) != manifest["files"].get(n))
    paths = ["source/" + n for n in changed]
    if web:
        for f in (stage / "web").rglob("*"):
            if f.is_file():
                name = f.relative_to(stage / "web").as_posix()
                target = confined(STATIC, name)
                if not target.exists() or checksum(target) != checksum(f):
                    paths.append("web/" + name)
    for name in changed:
        dest = confined(APP, name)
        if dest.exists() and name.startswith("backend/app/"):
            known = {old["files"].get(name), manifest["files"].get(name)}
            if checksum(dest) not in known and hashlib.sha256(dest.read_bytes().replace(b"\r\n", b"\n")).hexdigest() not in known:
                raise RuntimeError("local source changes: " + name)
    if web:
        status[0] = "waiting for active jobs"
        idle = trade(API, ["deploy/ci/idle.py"], source)
        if idle == 75:
            return False
        if idle:
            raise RuntimeError("job precheck failed")
        if time.time() >= expires:
            raise RuntimeError("release request expired before backup")
    status[0] = "backup and activation"
    backup = Path(tempfile.mkdtemp(prefix="cd-"+manifest["commit"][:7]+"-", dir=BACKUPS))
    shutil.copy2(BASE, backup / "baseline.json")
    shutil.copy2(MARKER, backup / "previous-deployed-main.json")
    for item in paths:
        root = STATIC if item.startswith("web/") else APP
        current = confined(root, item.split("/", 1)[1])
        if current.exists():
            dest = backup / item
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, dest)
    tx = {"backup": str(backup), "web": web, "paths": paths}
    if web and time.time() >= expires:
        raise RuntimeError("release request expired before activation")
    save(JOURNAL, tx)
    try:
        if web:
            service("stop", "trade-api")
            idle = trade(API, ["deploy/ci/idle.py"], source)
            if idle:
                service("start", "trade-api")
                JOURNAL.unlink()
                if idle == 75:
                    return False
                raise RuntimeError("job precheck failed")
            service("stop", "trade-worker", "trade-scheduler")
        for item in sorted(paths, key=lambda item: item == "web/index.html"):
            root = STATIC if item.startswith("web/") else APP
            dest = confined(root, item.split("/", 1)[1])
            if (stage / item).is_file():
                atomic_copy(stage / item, dest)
            else:
                dest.unlink(missing_ok=True)
        save(BASE, manifest)
        save(MARKER, {"commit": manifest["commit"], "backup": str(backup)})
        if web:
            status[0] = "service readiness"
            service("start", *UNITS)
            ready()
        JOURNAL.unlink()
        return True
    except BaseException:
        if JOURNAL.exists():
            rollback()
        raise

def receive(web):
    if JOURNAL.exists():
        rollback()
    s3 = boto3.client("s3", region_name="ap-northeast-2")
    pointer = "delivery/request.json" if web else "delivery/ml-approved.json"
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=pointer, MaxKeys=1)
    if not any(item["Key"] == pointer for item in listing.get("Contents", [])):
        return  # No release has been published yet.
    req = json.loads(s3.get_object(Bucket=BUCKET, Key=pointer)["Body"].read())
    if not req:
        return
    token, sha, digest = (req[k] for k in ("request", "commit", "sha256"))
    if not re.fullmatch(r"[0-9]+-[0-9]+", token) or not re.fullmatch("[0-9a-f]{40}", sha) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("invalid release request")
    done = STATE / "last-receipt.json"
    if done.exists() and read(done)["request"] == token:
        if not web and read(done)["status"] != "deployed":
            raise RuntimeError("previous ML release failed")
        return
    status = ["release validation"]
    reply = {"request": token, "commit": sha}
    try:
        if web and time.time() > req["expires"] and read(MARKER)["commit"] != sha:
            raise RuntimeError("release request expired")
        with tempfile.TemporaryDirectory(dir=STATE) as raw:
            stage = Path(raw)
            os.chmod(stage, 0o755)
            archive = stage / "release.tgz"
            s3.download_file(BUCKET, f"delivery/incoming/{token}/release.tgz", str(archive))
            if checksum(archive) != digest:
                raise ValueError("archive checksum mismatch")
            manifest = unpack(archive, stage)
            if manifest["commit"] != sha:
                raise ValueError("wrong commit")
            if not deploy(stage, manifest, web, status, req["expires"] if web else None):
                return
        reply.update(status="deployed", stage="ready", warnings=status[1:])
    except Exception as exc:
        print(f"Deployment failed in {status[0]}: {type(exc).__name__}", flush=True)
        reply.update(status="failed", stage=status[0])
        if not web:
            raise
    if web:
        if reply["status"] == "deployed":
            s3.put_object(Bucket=BUCKET, Key="delivery/ml-approved.json", Body=json.dumps(req).encode())
        s3.put_object(Bucket=BUCKET, Key=f"delivery/results/{token}.json", Body=json.dumps(reply).encode())
    save(done, reply)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["web","ml"])
    target = parser.parse_args().target
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receive(target == "web")

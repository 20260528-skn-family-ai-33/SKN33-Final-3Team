"""Send a verified main release and wait for a matching production receipt."""
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

def run(*args):
    return subprocess.check_output(args, text=True)

def main():
    env = os.environ
    bucket = env["RELEASE_BUCKET"]
    sha = env["GITHUB_SHA"]
    request = f"{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"
    bundle = Path(env["RUNNER_TEMP"]) / "release.tgz"
    prefix = f"delivery/incoming/{request}"
    receipt = f"delivery/results/{request}.json"
    if run("git", "ls-remote", "origin", "refs/heads/main").split()[0] != sha:
        raise SystemExit("Superseded by newer main; release not published")
    run("aws", "s3", "cp", str(bundle), f"s3://{bucket}/{prefix}/release.tgz", "--only-show-errors")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.json"
        with bundle.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        path.write_text(json.dumps({"request": request, "commit": sha, "expires": int(time.time()) + 1200,
                                    "sha256": digest}))
        run("aws", "s3", "cp", str(path), f"s3://{bucket}/delivery/request.json", "--only-show-errors")
        for _ in range(150):
            listing = json.loads(run("aws", "s3api", "list-objects-v2", "--bucket", bucket, "--prefix", receipt))
            if any(item["Key"] == receipt for item in listing.get("Contents", [])):
                run("aws", "s3api", "get-object", "--bucket", bucket, "--key", receipt, str(path))
                result = json.loads(path.read_text())
                if result["request"] != request or result["commit"] != sha:
                    raise SystemExit("Incorrect release receipt")
                message = f"Web EC2: {result['status']} ({result['stage']}) for {sha}"
                if result['stage'] == 'rollback':
                    message += (f"; original: {result['deploy_stage']} ({result['deploy_error']}), "
                                f"rollback: {result['rollback_error']}; operator recovery required")
                with open(env["GITHUB_STEP_SUMMARY"], "a") as stream:
                    print(message, file=stream)
                    for warning in result.get("warnings", []):
                        print(f"- Warning: {warning}", file=stream)
                print(message)
                if result["status"] != "deployed":
                    raise SystemExit(1)
                return
            time.sleep(10)
    raise SystemExit("No confirmed EC2 deployment receipt within 25 minutes")

if __name__ == "__main__":
    main()

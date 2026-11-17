"""Reject personal runtime files or credentials in every reachable Git history."""

from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_DIRS = {"data","workspace","backups","exports","screenshots","test-results","private",
                ".local",".codex",".agents",".vscode",".idea",".ssh",".aws",".venv","__pycache__"}
PRIVATE_SUFFIX = re.compile(r"\.(?:agentboard|sqlite3?|db)(?:-.*)?$|\.(?:pem|key|p12|pfx|log|bak|zip|7z|tar|gz)$",re.I)
SECRET = re.compile(rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")
PERSONAL_PATH = re.compile(rb"C:(?:/Users/1/|\\\\Users\\\\1\\\\|\\Users\\1\\)")


def git(*args):
    return subprocess.check_output(["git","-c","safe.directory="+ROOT.as_posix(),"-c","core.quotepath=false",*args],cwd=ROOT)


def private_path(name):
    if name in ("workspace/.gitkeep",".env.example"): return False
    path = PurePosixPath(name)
    return (bool(PRIVATE_DIRS.intersection(path.parts)) or bool(PRIVATE_SUFFIX.search(name)) or
            path.name in (".env","mcp-token") or path.name.startswith((".env.","credentials","secrets","codex-clipboard-")) or
            name.startswith("doc/") and path.suffix.lower() in (".png",".jpg",".jpeg",".webp",".gif") or
            bool(re.fullmatch(r"q.*-detail\.[^/]+",name)))


def main():
    objects = {}
    for line in git("rev-list","--objects","--all").decode("utf-8").splitlines():
        oid,_,name = line.partition(" ")
        if name: objects[oid] = name
    errors = set()
    history_names = git("log","--all","--format=","--name-only").decode("utf-8").splitlines()
    for name in [*history_names,*git("ls-files").decode("utf-8").splitlines()]:
        if not name: continue
        if private_path(name): errors.add((name,"private file in index/history"))
    process = subprocess.Popen(["git","-c","safe.directory="+ROOT.as_posix(),"cat-file","--batch"],cwd=ROOT,
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    try:
        for oid,name in objects.items():
            process.stdin.write((oid+"\n").encode()); process.stdin.flush()
            header = process.stdout.readline().decode().strip().split()
            if len(header) != 3: raise RuntimeError("Unable to inspect Git object")
            raw = process.stdout.read(int(header[2])); process.stdout.read(1)
            if header[1] != "blob": continue
            try: raw.decode("utf-8")
            except UnicodeDecodeError: continue
            if SECRET.search(raw): errors.add((name,"credential-like content"))
            if PERSONAL_PATH.search(raw): errors.add((name,"developer home path"))
    finally:
        process.stdin.close(); process.stdout.close(); process.wait()
    for name,reason in sorted(errors): print(f"BLOCKED {name}: {reason}")
    print(f"Publish audit: {len(objects)} objects, {len(errors)} findings. Secret values are never printed.")
    return bool(errors)


if __name__ == "__main__": sys.exit(main())

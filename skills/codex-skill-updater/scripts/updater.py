#!/usr/bin/env python3
"""Source-aware Codex skill updater. Python 3.11+, Git; no pip dependencies."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

VERSION = "0.1.0"
IGNORED = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def run(argv, *, data=None, timeout=180, env=None):
    """Bounded subprocess; kill our entire child tree on timeout."""
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    with subprocess.Popen([str(x) for x in argv], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **options) as p:
        try:
            out, err = p.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            raise RuntimeError(f"Command timed out after {timeout}s: {Path(str(argv[0])).name}")
        if p.returncode:
            raise RuntimeError(err.decode("utf-8", "replace")[-1500:] or f"Exit {p.returncode}")
        return out


def safe_rel(value):
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("Use a relative POSIX path")
    if value in ("", "."):
        return ""
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ("..", ".git") or re.search(r'[<>:"|?*\x00-\x1f]', x) or x.endswith((".", " "))
                              or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", x)
                              for x in p.parts):
        raise ValueError(f"Unsafe source path: {value}")
    return p.as_posix()


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def assert_plain(path):
    # Check ancestors before resolve(), which would hide links/junctions.
    for item in (path, *path.parents):
        if linked(item):
            raise ValueError(f"Symlink/junction is not managed: {item}")


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fingerprint(path):
    """Include unknown extra files; ignore only generated dependency/cache directories."""
    assert_plain(path)
    if not path.is_dir():
        raise ValueError(f"Installed directory missing: {path}")
    result = {}
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in list(dirs):
            child = Path(parent) / name
            if linked(child):
                raise ValueError(f"Linked directory: {child}")
        dirs[:] = [x for x in dirs if x not in IGNORED]
        for name in files:
            child = Path(parent) / name
            if linked(child):
                raise ValueError(f"Linked file: {child}")
            if child.suffix == ".pyc":
                continue
            result[child.relative_to(path).as_posix()] = blob(child.read_bytes().replace(b"\r\n", b"\n"))
    return result


def validate_skill(path):
    text = (path / "SKILL.md").read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        raise ValueError("SKILL.md must have YAML frontmatter")
    for field in ("name", "description"):
        found = re.search(r"^" + field + r":\s*(\S.*)$", match[1], re.M)
        if not found or found[1].strip() in ("''", '""', "null", "~"):
            raise ValueError(f"SKILL.md missing {field}")


class GitSource:
    def __init__(self, home, repository, ref="HEAD", proxy=None, timeout=180):
        self.repo = repository
        self.ref = ref
        self.cache = home / "cache" / hashlib.sha256(repository.encode()).hexdigest()[:24]
        self.timeout = timeout
        self.prefix = ["git", "-c", "core.hooksPath=" + os.devnull,
                       "-c", "http.lowSpeedLimit=100", "-c", "http.lowSpeedTime=30"]
        if os.name == "nt":
            self.prefix += ["-c", "http.sslBackend=openssl"]
        if proxy:
            self.prefix += ["-c", f"http.proxy={proxy}"]
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")

    def git(self, *args, data=None):
        return run(self.prefix + ["-C", str(self.cache), *args], data=data,
                   timeout=self.timeout, env=self.env)

    def refresh(self):
        url = f"https://github.com/{self.repo}.git"
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache.exists():
            temp = self.cache.with_name(self.cache.name + "." + uuid.uuid4().hex)
            try:
                run(self.prefix + ["clone", "--quiet", "--filter=blob:none", "--depth=1",
                                   "--no-checkout", url, str(temp)], env=self.env, timeout=self.timeout)
                os.replace(temp, self.cache)
            finally:
                if temp.exists():
                    shutil.rmtree(temp)
        if self.git("remote", "get-url", "origin").decode().strip() != url:
            raise ValueError("Cached origin no longer matches the registered source")
        self.git("fetch", "--quiet", "--depth=1", "--filter=blob:none", "--no-tags", "origin", self.ref)
        self.commit = self.git("rev-parse", "FETCH_HEAD").decode().strip()
        raw = self.git("ls-tree", "-r", "-z", self.commit)
        self.tree = []
        for item in raw.split(b"\0"):
            if item:
                meta, path = item.split(b"\t", 1)
                mode, kind, sha = meta.decode().split()
                self.tree.append((path.decode("utf-8"), mode, kind, sha))
        return self

    def select(self, source_path):
        prefix = safe_rel(source_path)
        prefix = prefix + "/" if prefix else ""
        result = {}
        folded = set()
        for path, mode, kind, sha in self.tree:
            if not path.startswith(prefix):
                continue
            rel = safe_rel(path[len(prefix):])
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError(f"Symlinks/submodules are not supported: {path}")
            if not rel or rel.casefold() in folded:
                raise ValueError(f"Duplicate/empty source path: {path}")
            folded.add(rel.casefold())
            result[rel] = (sha, mode)
        if not result:
            raise ValueError(f"Source path removed or empty: {source_path}")
        return result

    def materialize(self, entries, destination):
        shas = list(dict.fromkeys(sha for sha, _ in entries.values()))
        request = ("\n".join(shas) + "\n").encode()
        checks = self.git("cat-file", "--batch-check", data=request).decode().splitlines()
        missing = [line.split()[0] for line in checks if line.endswith(" missing")]
        if missing:
            self.git("-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin", "--no-tags",
                     "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "--stdin",
                     data=("\n".join(missing) + "\n").encode())
        raw = self.git("cat-file", "--batch", data=request)
        offset, objects = 0, {}
        for expected in shas:
            end = raw.index(b"\n", offset)
            header = raw[offset:end].split()
            if len(header) != 3 or header[0].decode() != expected or header[1] != b"blob":
                raise ValueError("Invalid Git blob response")
            size = int(header[2])
            data = raw[end + 1:end + 1 + size]
            offset = end + 2 + size
            if len(data) != size or blob(data) != expected:
                raise ValueError("Git object verification failed")
            objects[expected] = data
        for rel, (sha, mode) in entries.items():
            target = destination / safe_rel(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(objects[sha])
            if mode == "100755" and os.name != "nt":
                target.chmod(0o755)


def default_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skill-updater"


def validate_entry(entry, roots):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", entry["repository"]):
        raise ValueError("Expected GitHub owner/repository")
    ref = entry.get("ref", "HEAD")
    if not ref or ref.startswith("-") or any(x.isspace() for x in ref):
        raise ValueError("Invalid Git ref")
    safe_rel(entry.get("source_path", ""))
    path = Path(entry["path"]).expanduser().absolute()
    assert_plain(path)
    if path.name.startswith(".") or path.parent.resolve() not in [Path(x).expanduser().resolve() for x in roots]:
        raise ValueError("Destination must be an immediate non-hidden child of a configured skill root")
    if entry.get("kind", "skill") not in ("skill", "asset"):
        raise ValueError("Unknown entry kind")
    return path


@contextlib.contextmanager
def exclusive(home):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "run.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError(f"Another run or stale lock exists: {path}; inspect it before removal")
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "started": now()}).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def install(home, entry, dest, stage, baseline, state):
    """Journal + same-volume renames. Persist baseline before clearing the journal."""
    if fingerprint(dest) != state[str(dest)]["files"]:
        raise ValueError("Local files changed during download; update skipped")
    token = uuid.uuid4().hex
    backup = dest.parent / ".skill-updater-backups" / token / dest.name
    backup.parent.mkdir(parents=True)
    journal = home / "transactions" / (token + ".json")
    before_state = state.get(str(dest))
    record = {"id": token, "path": str(dest), "backup": str(backup),
              "stage": str(stage), "before": before_state, "after": baseline}
    write_json(journal, record)
    moved = False
    try:
        os.replace(dest, backup)
        moved = True
        os.replace(stage, dest)
        state[str(dest)] = baseline
        write_json(home / "state.json", state)
    except Exception:
        if moved:
            if dest.exists():
                failed = stage.with_name(stage.name + "-failed")
                os.replace(dest, failed)
            os.replace(backup, dest)
        if before_state is None:
            state.pop(str(dest), None)
        else:
            state[str(dest)] = before_state
        write_json(home / "state.json", state)
        journal.unlink(missing_ok=True)
        raise
    write_json(home / "history" / (token + ".json"), record)
    journal.unlink()
    return token


def process_entry(home, entry, roots, state, source, apply=False):
    dest = validate_entry(entry, roots)
    result = {"path": str(dest), "name": dest.name, "repository": entry["repository"]}
    if not entry.get("enabled", True):
        return dict(result, status="disabled")
    if entry.get("pinned", False):
        return dict(result, status="pinned", reason="Archived/pinned source kept unchanged")
    current = fingerprint(dest)
    previous = state.get(str(dest))
    identity = {key: entry.get(key, default) for key, default in
                (("repository", ""), ("source_path", ""), ("ref", "HEAD"))}
    if previous and previous.get("source") != identity:
        return dict(result, status="needs_adoption", reason="Registered source differs from the verified baseline")
    if previous and current != previous["files"]:
        return dict(result, status="local_changes", reason="Restore or review local modifications first")
    if source is None:
        return dict(result, status="needs_adoption", reason="No verified baseline; compare with upstream first")
    entries = source.select(entry.get("source_path", ""))
    if previous and previous.get("commit") == source.commit:
        return dict(result, status="current", commit=source.commit)
    # Sibling stage is always on the destination filesystem.
    with tempfile.TemporaryDirectory(prefix=".skill-updater-stage-", dir=dest.parent) as folder:
        stage = Path(folder) / dest.name
        stage.mkdir()
        source.materialize(entries, stage)
        if entry.get("kind", "skill") == "skill":
            validate_skill(stage)
        expected = fingerprint(stage)
        result["commit"] = source.commit
        if current == expected:
            if apply:
                state[str(dest)] = {"commit": source.commit, "files": expected, "source": identity}
                write_json(home / "state.json", state)
            return dict(result, status="current")
        if previous is None:
            return dict(result, status="needs_adoption", reason="Local copy differs and has no upstream baseline")
        if not apply:
            return dict(result, status="update_available")
        baseline = {"commit": source.commit, "files": expected, "source": identity}
        # install checks against the old baseline immediately before mutation.
        if fingerprint(dest) != previous["files"]:
            raise ValueError("Local changes detected before replacement")
        transaction = install(home, entry, dest, stage, baseline, state)
        return dict(result, status="updated", transaction=transaction, files=len(expected))


def plugins(apply=False, executable="codex"):
    """Only native APIs; app runtime and disabled/pinned plugins stay app-managed."""
    output = []
    if not shutil.which(executable):
        # Codex Desktop executable paths can change after a desktop update.
        candidates = list((Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin").glob("*/codex.exe"))
        if candidates:
            executable = str(max(candidates, key=lambda x: x.stat().st_mtime))
    listing = json.loads(run([executable, "plugin", "list", "--json"]).decode("utf-8-sig"))
    installed = listing.get("installed")
    if not isinstance(installed, list):
        raise ValueError("Unsupported Codex plugin list schema")
    if apply:
        try:
            result = json.loads(run([executable, "plugin", "marketplace", "upgrade", "--json"]).decode("utf-8-sig"))
            output.append({"status": "marketplaces_refreshed", "result": result})
        except Exception as exc:
            output.append({"status": "error", "scope": "marketplaces", "reason": str(exc)})
    for item in installed:
        ref = item.get("pluginId", "")
        source = item.get("source", {})
        row = {"plugin": ref, "version_before": item.get("version")}
        if item.get("enabled") is not True or source.get("sha") or source.get("version"):
            output.append(dict(row, status="skipped", reason="Disabled or version-pinned"))
            continue
        if source.get("source") == "local" and not item.get("marketplaceSource", {}).get("sourceType") == "git":
            output.append(dict(row, status="app_managed", reason="Local/runtime source; no independent update"))
            continue
        if not apply:
            output.append(dict(row, status="native_refresh_available"))
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+", ref):
            output.append(dict(row, status="error", reason="Invalid plugin selector"))
            continue
        try:
            result = json.loads(run([executable, "plugin", "add", ref, "--json"]).decode("utf-8-sig"))
            version = result["version"]
            if result.get("pluginId") != ref or not version or not Path(result["installedPath"]).is_dir():
                raise ValueError("Native plugin install result could not be verified")
            output.append(dict(row, status="updated" if version != row["version_before"] else "refreshed",
                               version_after=version))
        except Exception as exc:
            output.append(dict(row, status="error", reason=str(exc)))
    if apply and any(x["status"] in ("updated", "refreshed") for x in output):
        verified = json.loads(run([executable, "plugin", "list", "--json"]).decode("utf-8-sig"))
        versions = {x["pluginId"]: x.get("version") for x in verified["installed"]}
        for row in output:
            if row["status"] in ("updated", "refreshed"):
                if versions.get(row["plugin"]) != row["version_after"]:
                    row.update(status="error", reason="Post-install plugin version mismatch")
                else:
                    row["verified"] = True
    return output


def execute(home, apply=False, include_plugins=False, only=None):
    registry = read_json(home / "registry.json", {})
    roots = registry.get("roots", [])
    entries = registry.get("skills", [])
    state = read_json(home / "state.json", {})
    if list((home / "transactions").glob("*.json")):
        raise RuntimeError("Interrupted transaction exists; inspect transactions/ before another update")
    report = {"started": now(), "mode": "update" if apply else "check", "skills": [], "plugins": []}
    sources = {}
    for entry in entries:
        if only and entry.get("path") not in only and Path(entry["path"]).name not in only:
            continue
        try:
            dest = validate_entry(entry, roots)
            # Avoid all network access for pinned, disabled or modified installs.
            source = None
            eligible = entry.get("enabled", True) and not entry.get("pinned", False)
            previous = state.get(str(dest))
            dirty = previous is not None and fingerprint(dest) != previous["files"]
            if eligible and not dirty:
                key = (entry["repository"], entry.get("ref", "HEAD"))
                if key not in sources:
                    try:
                        sources[key] = GitSource(home, *key, proxy=registry.get("proxy"),
                                                 timeout=registry.get("timeout", 180)).refresh()
                    except Exception as exc:
                        sources[key] = exc
                if isinstance(sources[key], Exception):
                    raise sources[key]
                source = sources[key]
            row = process_entry(home, entry, roots, state, source, apply)
        except Exception as exc:
            row = {"path": entry.get("path"), "status": "error", "reason": str(exc)}
        report["skills"].append(row)
        print(f"{row['status']}: {row.get('path')}", flush=True)
        if list((home / "transactions").glob("*.json")):
            report["interrupted_transaction"] = True
            break
    if include_plugins:
        try:
            report["plugins"] = plugins(apply, registry.get("codex", "codex"))
        except Exception as exc:
            report["plugins"] = [{"status": "error", "reason": str(exc)}]
    report["finished"] = now()
    report_path = home / "reports" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".json")
    write_json(report_path, report)
    write_json(home / "last-report.json", report)
    print(f"Report: {report_path}")
    return report


def rollback(home, transaction):
    if not re.fullmatch(r"[a-f0-9]{32}", transaction):
        raise ValueError("Invalid transaction id")
    record_path = home / "history" / (transaction + ".json")
    record = read_json(record_path)
    if not record or record.get("rolled_back"):
        raise ValueError("Unknown or already rolled-back transaction")
    registry = read_json(home / "registry.json", {})
    entry = next((x for x in registry.get("skills", []) if x["path"] == record["path"]), None)
    if entry is None:
        raise ValueError("Rollback destination is no longer registered")
    dest = validate_entry(entry, registry["roots"])
    backup = Path(record["backup"])
    expected_parent = dest.parent / ".skill-updater-backups" / transaction
    assert_plain(backup)
    if backup.parent != expected_parent or backup.name != dest.name:
        raise ValueError("Backup location does not match the transaction")
    if fingerprint(dest) != record["after"]["files"]:
        raise ValueError("Local files changed after update; rollback would overwrite them")
    if fingerprint(backup) != record["before"]["files"]:
        raise ValueError("Backup verification failed")
    held = backup.with_name(backup.name + ".replaced")
    journal = home / "transactions" / (transaction + "-rollback.json")
    write_json(journal, {"operation": "rollback", **record})
    os.replace(dest, held)
    try:
        os.replace(backup, dest)
    except Exception:
        os.replace(held, dest)
        journal.unlink()
        raise
    state = read_json(home / "state.json", {})
    state[str(dest)] = record["before"]
    write_json(home / "state.json", state)
    record["rolled_back"] = now()
    write_json(record_path, record)
    journal.unlink()


def register(home, args):
    path = Path(args.path).expanduser().absolute()
    registry = read_json(home / "registry.json", {"schema": 1, "roots": [], "skills": []})
    root = str(path.parent)
    # Explicit register authorizes this root; system/plugin cache directories are excluded.
    if any(x in (".system", "plugins", "cache") for x in path.parts):
        raise ValueError("System skills and plugin caches are managed by Codex")
    if root not in registry["roots"]:
        registry["roots"].append(root)
    entry = {"path": str(path), "repository": args.repo, "source_path": args.source,
             "ref": args.ref, "pinned": args.pin, "kind": args.kind}
    validate_entry(entry, registry["roots"])
    if not path.is_dir():
        raise ValueError("Register an existing installation")
    if args.kind == "skill":
        validate_skill(path)
    if any(x["path"] == str(path) for x in registry["skills"]):
        raise ValueError("Already registered; edit registry.json to change its source")
    registry["skills"].append(entry)
    write_json(home / "registry.json", registry)
    print("Registered. Run update to adopt only if the installed files match upstream exactly.")


def inventory(roots):
    result = []
    for root in roots:
        root = Path(root).expanduser()
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.name.startswith(".") and (child / "SKILL.md").is_file():
                    result.append({"name": child.name, "path": str(child.absolute()), "linked": linked(child)})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=default_home(), help="Private registry/cache/report directory")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "update"):
        p = sub.add_parser(command)
        p.add_argument("--plugins", action="store_true", help="Also use Codex's native plugin commands")
        p.add_argument("--only", action="append", help="Filter skill name or full path; repeatable")
    p = sub.add_parser("register")
    p.add_argument("--path", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--source", default="")
    p.add_argument("--ref", default="HEAD")
    p.add_argument("--pin", action="store_true")
    p.add_argument("--kind", choices=["skill", "asset"], default="skill")
    p = sub.add_parser("inventory")
    p.add_argument("--root", action="append")
    p = sub.add_parser("rollback")
    p.add_argument("transaction")
    args = parser.parse_args(argv)
    home = args.home.expanduser().absolute()
    try:
        if args.command == "inventory":
            print(json.dumps(inventory(args.root or [Path.home() / ".agents/skills",
                                                     Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"]),
                             ensure_ascii=False, indent=2))
            return 0
        with exclusive(home):
            if args.command == "register":
                register(home, args)
            elif args.command == "rollback":
                rollback(home, args.transaction)
            else:
                report = execute(home, args.command == "update", args.plugins, args.only)
                return int(any(x["status"] == "error" for x in report["skills"] + report["plugins"]))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        try:
            write_json(home / "last-error.json", {"time": now(), "error": str(exc)})
        except OSError:
            pass
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/codex-skill-updater/scripts"
sys.path.insert(0, str(SCRIPTS))
import updater as u
import schedule as s

SKILL = b"---\nname: demo\ndescription: A useful demo skill.\n---\n\nVersion one.\n"


class Snapshot:
    def __init__(self, files=None, commit="b" * 40):
        self.files = files or {"SKILL.md": SKILL.replace(b"one", b"two")}
        self.commit = commit

    def select(self, path):
        return self.files

    def materialize(self, entries, target):
        for name, data in self.files.items():
            child = target / name
            child.parent.mkdir(parents=True, exist_ok=True)
            child.write_bytes(data)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "private"
        self.root = self.base / "skills"
        self.dest = self.root / "demo"
        self.dest.mkdir(parents=True)
        (self.dest / "SKILL.md").write_bytes(SKILL)
        self.entry = dict(path=str(self.dest), repository="author/repo", source_path="skills/demo", ref="HEAD")
        self.identity = {k: self.entry[k] for k in ("repository", "source_path", "ref")}
        self.state = {str(self.dest): dict(commit="a" * 40, files=u.fingerprint(self.dest), source=self.identity)}
        u.write_json(self.home / "registry.json", dict(roots=[str(self.root)], skills=[self.entry]))
        u.write_json(self.home / "state.json", self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def process(self, source=None, apply=True):
        return u.process_entry(self.home, self.entry, [str(self.root)], self.state, source or Snapshot(), apply)

    def test_update_receipt_backup_and_rollback(self):
        result = self.process()
        self.assertEqual(result["status"], "updated")
        self.assertIn(b"two", (self.dest / "SKILL.md").read_bytes())
        receipt = u.read_json(self.home / "history" / (result["transaction"] + ".json"))
        self.assertEqual((Path(receipt["backup"]) / "SKILL.md").read_bytes(), SKILL)
        self.assertEqual(receipt["after"]["files"], u.fingerprint(self.dest))
        self.assertEqual(u.read_json(self.home / "state.json")[str(self.dest)]["files"], u.fingerprint(self.dest))
        u.rollback(self.home, result["transaction"])
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), SKILL)
        self.assertEqual(u.read_json(self.home / "state.json")[str(self.dest)]["commit"], "a" * 40)

    def test_local_changes_extra_files_and_deletions_are_preserved(self):
        for mode in ("edit", "extra", "delete"):
            with self.subTest(mode=mode):
                original = self.dest / "SKILL.md"
                original.write_bytes(SKILL)
                extra = self.dest / "custom.md"
                extra.unlink(missing_ok=True)
                if mode == "edit":
                    original.write_text("My custom instructions", encoding="utf-8")
                elif mode == "extra":
                    extra.write_text("private notes", encoding="utf-8")
                else:
                    original.unlink()
                before = u.fingerprint(self.dest)
                self.assertEqual(self.process()["status"], "local_changes")
                self.assertEqual(before, u.fingerprint(self.dest))

    def test_failed_download_or_invalid_skill_does_not_modify_install(self):
        for source in (Snapshot({"SKILL.md": b"broken frontmatter"}), Snapshot({"other.txt": b"missing skill"})):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.process(source)
            self.assertEqual((self.dest / "SKILL.md").read_bytes(), SKILL)

    def test_unknown_baseline_only_adopted_when_exactly_equal(self):
        self.state.clear()
        self.assertEqual(self.process()["status"], "needs_adoption")
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), SKILL)
        self.assertEqual(self.process(Snapshot({"SKILL.md": SKILL}))["status"], "current")
        self.assertEqual(self.state[str(self.dest)]["source"], self.identity)

    def test_dry_run_changes_no_files_or_baseline(self):
        before = (self.home / "state.json").read_bytes()
        self.assertEqual(self.process(apply=False)["status"], "update_available")
        self.assertEqual(before, (self.home / "state.json").read_bytes())
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), SKILL)

    def test_pinned_and_changed_sources_are_not_updated(self):
        self.entry["pinned"] = True
        self.assertEqual(self.process()["status"], "pinned")
        self.entry["pinned"] = False
        self.entry["repository"] = "someone/else"
        self.assertEqual(self.process()["status"], "needs_adoption")

    def test_state_write_failure_restores_original_directory(self):
        real_write = u.write_json
        calls = 0

        def fail_once(path, data):
            nonlocal calls
            if path.name == "state.json":
                calls += 1
                if calls == 1:
                    raise OSError("Simulated disk failure")
            return real_write(path, data)

        with patch.object(u, "write_json", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.process()
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), SKILL)
        self.assertFalse(list((self.home / "transactions").glob("*.json")))

    def test_concurrent_modification_before_commit_is_preserved(self):
        source = Snapshot()
        original = source.materialize

        def alter(entries, stage):
            original(entries, stage)
            (self.dest / "SKILL.md").write_bytes(b"User editing while download runs")

        source.materialize = alter
        with self.assertRaises(ValueError):
            self.process(source)
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), b"User editing while download runs")

    def test_rollback_refuses_to_overwrite_new_local_edits(self):
        result = self.process()
        (self.dest / "SKILL.md").write_bytes(b"New local work")
        with self.assertRaises(ValueError):
            u.rollback(self.home, result["transaction"])
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), b"New local work")

    def test_directory_escape_and_archive_symlinks_rejected(self):
        self.entry["path"] = str(self.base / "outside")
        with self.assertRaises(ValueError):
            self.process()
        for rel in ("../../escape", "/absolute", "C:/drive", "foo\\..\\bar", "file:stream", "aux.txt"):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                u.safe_rel(rel)
        source = u.GitSource(self.home, "a/b")
        source.tree = [("demo/SKILL.md", "120000", "blob", "a" * 40)]
        with self.assertRaises(ValueError):
            source.select("demo")

    def test_source_case_collisions_rejected(self):
        source = u.GitSource(self.home, "a/b")
        source.tree = [(x, "100644", "blob", "a" * 40) for x in ("demo/a.txt", "demo/A.txt")]
        with self.assertRaises(ValueError):
            source.select("demo")

    def test_lock_and_interrupted_transaction_block_another_update(self):
        with u.exclusive(self.home):
            with self.assertRaises(RuntimeError):
                with u.exclusive(self.home):
                    pass
        u.write_json(self.home / "transactions/pending.json", {})
        with self.assertRaises(RuntimeError):
            u.execute(self.home, apply=True)

    def test_crlf_files_compare_equal(self):
        (self.dest / "SKILL.md").write_bytes(SKILL.replace(b"\n", b"\r\n"))
        self.assertEqual(self.process(Snapshot({"SKILL.md": SKILL}))["status"], "current")


class GitTests(unittest.TestCase):
    def test_real_git_blob_export_and_executable_bit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            repo = root / "git"
            repo.mkdir()
            def git(*args):
                return u.run(["git", "-C", str(repo), *args])
            git("init", "--quiet")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.invalid")
            (repo / "demo").mkdir()
            (repo / "demo/SKILL.md").write_bytes(SKILL)
            (repo / "demo/run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
            git("add", ".")
            git("update-index", "--chmod=+x", "demo/run.sh")
            git("commit", "--quiet", "-m", "fixture")
            source = u.GitSource(root, "test/source")
            source.cache = repo
            source.tree = []
            for row in git("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
                if row:
                    meta, path = row.split(b"\t")
                    mode, kind, sha = meta.decode().split()
                    source.tree.append((path.decode(), mode, kind, sha))
            output = root / "export"
            output.mkdir()
            source.materialize(source.select("demo"), output)
            self.assertEqual((output / "SKILL.md").read_bytes(), SKILL)
            self.assertEqual((output / "run.sh").read_bytes(), b"#!/bin/sh\nexit 0\n")
            if os.name != "nt":
                self.assertTrue(os.access(output / "run.sh", os.X_OK))


class PluginTests(unittest.TestCase):
    def test_native_only_enabled_plugin_updates_with_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            data = {"installed": [
                dict(pluginId="ok@market", version="1", enabled=True, source={"source": "remote"}),
                dict(pluginId="disabled@market", version="1", enabled=False, source={"source": "remote"}),
                dict(pluginId="runtime@market", version="1", enabled=True, source={"source": "local"}),
                dict(pluginId="pin@market", version="1", enabled=True, source={"source": "git", "sha": "a" * 40})]}
            calls = []
            def native(argv, **kwargs):
                calls.append(argv)
                if argv[2] == "list":
                    return json.dumps(data).encode()
                if argv[2] == "marketplace":
                    return b'{}'
                data["installed"][0]["version"] = "2"
                return json.dumps(dict(pluginId="ok@market", version="2", installedPath=folder)).encode()
            with patch.object(u, "run", side_effect=native):
                result = u.plugins(True)
            self.assertEqual([x[3] for x in calls if x[2] == "add"], ["ok@market"])
            self.assertTrue(next(x for x in result if x.get("plugin") == "ok@market")["verified"])
            self.assertEqual(next(x for x in result if x.get("plugin") == "runtime@market")["status"], "app_managed")


class ScheduleTests(unittest.TestCase):
    def make(self, **kwargs):
        return s.make_xml(frequency=kwargs.pop("frequency", "weekly"), at=kwargs.pop("at", "10:15"),
                          weekdays=kwargs.pop("weekdays", ["mon", "fri"]), day=kwargs.pop("day", None),
                          user="DOMAIN\\A&B", executable="C:\\Program Files\\Python\\pythonw.exe",
                          script=Path("C:/some path/更新.py"), home=Path("C:/private space"),
                          start=dt.date(2026, 10, 23), **kwargs)

    def test_user_selected_daily_weekly_monthly_and_quoted_paths(self):
        ns = {"t": s.NS}
        for frequency, marker, day in (("daily", "ScheduleByDay", None),
                                       ("weekly", "ScheduleByWeek", None),
                                       ("monthly", "ScheduleByMonth", 12)):
            xml = self.make(frequency=frequency, day=day)
            root = ET.fromstring(xml)
            self.assertIsNotNone(root.find(".//t:" + marker, ns))
            self.assertEqual(root.find(".//t:UserId", ns).text, "DOMAIN\\A&B")
            self.assertEqual(root.find(".//t:StartBoundary", ns).text, "2026-10-23T10:15:00")
            arguments = root.find(".//t:Arguments", ns).text
            self.assertIn('"C:', arguments)
            self.assertIn("--plugins", arguments)

    def test_invalid_time_or_incomplete_schedule_rejected(self):
        for kwargs in (dict(at="25:00"), dict(weekdays=[]), dict(frequency="monthly", day=31)):
            with self.assertRaises(ValueError):
                self.make(**kwargs)


if __name__ == "__main__":
    unittest.main()

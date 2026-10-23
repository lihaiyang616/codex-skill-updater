# Operation notes

## Source registry

The registry is private and intentionally contains machine paths. It is not bundled with the public skill. Example:

```json
{
  "schema": 1,
  "roots": ["/home/me/.agents/skills"],
  "skills": [
    {
      "path": "/home/me/.agents/skills/example",
      "repository": "author/repository",
      "source_path": "skills/example",
      "ref": "HEAD",
      "pinned": false,
      "kind": "skill"
    }
  ]
}
```

An optional `proxy` value supplies a Git HTTP proxy for this process only; it does not change global Git configuration. `timeout` is a per-command timeout in seconds (default 180). `codex` optionally selects an executable; the adapter can rediscover the Desktop binary after its version directory changes.

Only existing installations are registered. No source guessing, implicit trust of SKILL.md hyperlinks, runtime installation, or automatic reinstallation of deleted skills occurs. Unknown initial copies are adopted only after exact upstream comparison. Local changes after adoption include edited files, missing tracked files, and additional files. Generated `.git`, `__pycache__`, `.pytest_cache`, `node_modules` directories and `.pyc` files are excluded from content comparison, but original directories are fully retained in backups. LF/CRLF differences are normalized. Executable bits are retained on POSIX; local mode-only changes are not detected.

This release supports public GitHub sources and regular files, not Git LFS materialization, submodules or symlinked skill trees. A Git LFS pointer stays a pointer. Select a source that distributes usable skill assets without these features. Native plugin support depends on the installed Codex CLI version and account connectivity. Codex-managed system skills and plugin bundles are outside the manual skill inventory.

Repository/ref snapshots are reused across registered skills within a run. Filtered Git blobs are fetched in batches, verified against their object IDs, and exported without executing checkout hooks or downloaded code. Source paths removed by an author are an error; the tool does not silently select an archive or a different same-named skill.

## Scheduling

`schedule.py configure` requires an explicit frequency and time; weekly needs one or more `--weekday` values, monthly needs `--day 1..28`. Preview is the default. `--install` creates or replaces the task named `Codex-Skill-Updater` for the current user. `--task-name` can isolate multiple profiles. The configuration is recorded in `schedule.json` after Windows returns the task successfully.

Use `schedule.py status` to inspect actual task state. Change frequency/time by configuring the same task name again. To choose manual mode, remove the scheduled task with `schedule.py remove`. Registration uses the user's interactive token and least privilege, requires no stored password, and does not wake the computer. Windows may run missed starts later when the user signs in. If registration is denied by Windows, report the failure and provide the ready command; do not claim automation is enabled.

On macOS/Linux, the update engine is portable but the scheduler installer is Windows-only. A cron example is `0 10 * * 1 /absolute/python /absolute/updater.py update --plugins`; quoting, environment/PATH and scheduler setup are the user's environment's responsibility. Do not install a schedule without the user's requested timing.

## Reports and recovery

`last-report.json` and timestamped `reports/*.json` contain per-target outcomes. `last-error.json` records top-level failures such as a lock or unfinished transaction. Errors produce a nonzero process exit; pinned, modified and unadopted entries are reported as skips. There are no email, telemetry, GitHub uploads or automatic external notifications.

Updates use a stage folder beside the destination and a full backup under the same filesystem. A journal is written before renames. A regular Python exception restores the old directory if replacement or baseline persistence fails. A process/OS crash leaves a transaction journal, and subsequent runs stop for inspection. This is recoverable journaling, not a claim of crash-atomic transactions across all filesystems.

For an interrupted run, read the transaction's `path`, `stage`, `backup`, `before` and `after`. Compare current and backup fingerprints to the recorded receipts before restoring anything. The updater's `fingerprint(Path(...))` helper returns comparable hashes. Preserve all copies if the directory contains new edits. Restore a verified original and its `before` receipt, or confirm a complete new installation and its `after` receipt, then archive the resolved journal outside `transactions/`. Do not merely delete the journal to force a retry.

A stale `run.lock` contains its originating PID and UTC start time. Confirm that process is no longer this updater before removing the lock; a reused PID is not proof of a running updater. Backups/history are retained until the user chooses to remove them. Schedule removal does not delete backups, reports or the private registry.

Native plugin refreshes are handled by Codex. This tool verifies the resulting installed version but does not back up arbitrary Codex databases/configuration or offer plugin rollback. App-provided runtime plugins require updates from the Codex app/runtime itself. Other agents' plugin systems (for example Claude Code) are not updated.

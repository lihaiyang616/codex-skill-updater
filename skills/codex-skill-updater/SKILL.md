---
name: codex-skill-updater
description: Check and update GitHub-installed Codex skills with source tracking, local-change protection, backups and rollback. Refresh supported plugins through native Codex commands, or configure user-selected daily, weekly or monthly Windows updates.
---

# Codex Skill Updater

Use the Python scripts in this skill's `scripts/` directory. Requires Python 3.11+ and Git. The native plugin adapter additionally requires a Codex CLI with `plugin list --json`, `plugin marketplace upgrade --json` and `plugin add --json`.

## Check or update

```text
python <skill>/scripts/updater.py inventory
python <skill>/scripts/updater.py check --plugins
python <skill>/scripts/updater.py update --plugins
```

Omit `--plugins` to handle only registered skills. `check` downloads into staging/cache and writes a report, but does not replace installations or adopt baselines. `update` is appropriate when the user authorizes updates, including a configured recurring update; an inspection request alone calls for `check`.

The private registry, cache, receipts and reports live under `$CODEX_HOME/skill-updater` or `~/.codex/skill-updater`. Pass `--home <directory>` **before** the subcommand to override it. Never commit this private directory to a public repository.

For a previously untracked installation, identify its actual original repository and skill root from receipts or verified Git history. Do not infer ownership from library links in SKILL.md or a matching folder name alone. Register each actual destination separately, including duplicate installations:

```text
python <skill>/scripts/updater.py register --path <installed-folder> --repo owner/repo --source skills/example
```

Then run `update` to adopt the baseline **only if the local files exactly match upstream**. A differing untracked installation returns `needs_adoption`; compare its source history and preserve custom work before establishing a baseline. Do not manufacture a baseline from an arbitrary local snapshot to silence this result. Use `--ref <branch-or-tag>` and `--pin` for archived/historical skills; pinned installations remain unchanged.

Shared sibling resources can be separately registered with `--kind asset`. Confirm the author's required shared paths before updating dependent skills. This tool does not resolve dependency versions, install runtime packages, execute downloaded scripts, or infer shared resources.

For a particular skill use `--only <folder-name-or-full-path>`. Check the report's actual outcomes: `updated`, `current`, `local_changes`, `needs_adoption`, `pinned`, `disabled`, or `error`. Unknown sources, removals, invalid manifests, links and modified files must remain untouched. Report them without claiming everything is up to date.

## Automatic schedules

A skill does not run in the background by itself. When the user requests automation, let them choose frequency and time; for weekly schedules also ask for weekdays, and for monthly schedules the date (1–28). Offer manual operation as well. Do not impose a schedule or ask again for choices already given. Choose a reasonable time only if the user delegates it.

Windows examples (local Windows time):

```text
python <skill>/scripts/schedule.py configure --frequency daily --at 09:30
python <skill>/scripts/schedule.py configure --frequency weekly --weekday mon --at 10:00
python <skill>/scripts/schedule.py configure --frequency monthly --day 1 --at 18:00
```

These commands preview the task XML. Add `--install` to create/update the per-user task **after the user requests enabling it**. Add `--skills-only` to exclude plugins. Verify the returned task with `schedule.py status`. `schedule.py remove` stops automatic operation without removing skills or their backups. Read [the operation notes](references/operations.md) for scheduling limits and recovery.

The Windows job uses `pythonw.exe` when available, runs without a model call, and stores reports locally. It runs when the user is logged in and the computer/network are available; missed starts can run later. On macOS/Linux supply the same updater command to the user's chosen scheduler; this release does not install cron or launchd jobs.

## Backups, rollback and plugins

Every skill replacement keeps the original folder under its skill root's `.skill-updater-backups/<transaction>/<name>`. The report includes the transaction ID:

```text
python <skill>/scripts/updater.py rollback <transaction-id>
```

Rollback refuses to overwrite edits made since the update. Transaction journals and exclusive locks stop unsafe concurrent or interrupted runs. Follow the operation notes before handling a stale lock or interrupted transaction.

Plugins use Codex's native installation/refresh commands, with before/after version verification. Disabled or pinned plugins are skipped; local runtime bundles are reported as `app_managed`. Remote refreshes may keep the same version. The script does not change plugin cache files, permissions or OAuth settings; native plugin operations have no skill-style rollback guarantee. Do not call a same-version refresh an upgrade, claim a missing native capability exists, or substitute removal/reinstallation when native refresh fails.

Finish with counts of actual updates and skipped/error cases, the report location, and the verified schedule if configured. Changed skills may require a new Codex turn/session to be discovered.

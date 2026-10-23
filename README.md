# Codex Skill Updater

[English](#english) · [中文](#中文) · MIT

## 中文

让 Codex 检查并更新 GitHub 下载的 skills，通过原生接口刷新插件，并让使用者自己选择自动更新周期和时间。

**默认不启用定时任务。** 可选每天、每周、每月，设置时间、星期或日期，也可只手动调用。需要 Python 3.11+、Git；插件刷新需要支持相应命令的 Codex CLI。无 pip 依赖，不调用模型，不上传本机清单。

### 安装

在 Codex 中说：

```text
安装 https://github.com/lihaiyang250250-del/codex-skill-updater 中的 codex-skill-updater skill
```

也可以克隆本仓库，将 `skills/codex-skill-updater` 文件夹复制到 `~/.agents/skills/` 或 `~/.codex/skills/`。不要覆盖同名的自定义目录。新一轮对话中调用：

```text
$codex-skill-updater 检查我的 skills 和插件有没有更新
$codex-skill-updater 每周五下午 3 点自动更新，有本地修改的先跳过
```

### 功能和边界

- 为每个安装目录登记真实来源和路径，按 Git 内容校验；支持同名 skill 安装在不同目录。
- 更新前保留完整原目录，支持回滚；有修改、额外文件或缺失文件时跳过。
- 初次接管只自动接受与上游完全相同的副本；来源不明或初始内容不同的不会被直接覆盖。
- 作者删除的路径会报错；归档 skill 可固定版本保留。
- Windows 定时任务支持每天、每周、每月，自选时间；预览后用 `--install` 启用，可修改、查询、移除。
- 通过 `codex plugin marketplace upgrade` 和 `codex plugin add` 刷新支持的插件，并核验版本。应用托管的运行时插件由 Codex 应用更新；禁用或固定版本的插件跳过。插件没有本工具的 skill 回滚保证。
- skill 更新引擎跨平台；定时任务安装器目前支持 Windows。运行需电脑开机、用户登录及网络可用；错过时间可稍后执行，不承诺关机时运行。

### 命令示例

下面的 `<skill>` 指安装后的 skill 文件夹，`<已安装目录>` 指需要管理的具体 skill 目录。登记前确认作者和仓库内真实路径，不要直接从 SKILL.md 里的依赖库链接猜来源。

```text
python <skill>/scripts/updater.py inventory
python <skill>/scripts/updater.py register --path <已安装目录> --repo author/repo --source skills/example
python <skill>/scripts/updater.py check --plugins
python <skill>/scripts/updater.py update --plugins
python <skill>/scripts/schedule.py configure --frequency weekly --weekday fri --at 15:00
python <skill>/scripts/schedule.py configure --frequency weekly --weekday fri --at 15:00 --install
python <skill>/scripts/schedule.py status
python <skill>/scripts/schedule.py remove
python <skill>/scripts/updater.py rollback <transaction-id>
```

每天：`--frequency daily --at 09:30`。每月：`--frequency monthly --day 1 --at 18:00`（日期 1–28）。省略 `--plugins` 只更新 skills；定时任务使用 `--skills-only`。注册后执行 `update`，仅当本地与上游完全相同时建立初始基线。

本机配置、来源基线和报告保存在 `~/.codex/skill-updater/`（遵循 `CODEX_HOME`）；原目录备份在对应 skills 根目录的 `.skill-updater-backups/`。这些本地数据不会随仓库发布。更多限制和中断恢复见 [operation notes](skills/codex-skill-updater/references/operations.md)。

## English

A source-aware Codex skill for maintaining public GitHub-installed skills and refreshing supported plugins through the native Codex CLI. Includes configurable **daily, weekly or monthly** Windows scheduling, preview by default.

Install `skills/codex-skill-updater` into an agent skill root. Register each existing installation with its verified `owner/repo` and repository-relative skill path, then check or update. Initial adoption requires an exact match to upstream; subsequent edits are preserved by skipping the installation. Every skill replacement keeps a complete backup and a rollback receipt. Archived sources can be pinned. Shared assets need explicit separate registration.

Requires Python 3.11+ and Git. No Python package dependencies, model calls, telemetry or automatic uploads. The updater works on Windows/macOS/Linux; the scheduler installer currently supports Windows. Native plugin support is capability-dependent and excludes app runtime bundles, disabled plugins and pinned versions. Native plugin operations do not have skill-style rollback. Read [SKILL.md](skills/codex-skill-updater/SKILL.md) for commands and [operation notes](skills/codex-skill-updater/references/operations.md) for limitations and recovery.

### Development

```sh
python -m unittest discover -s tests -v
```

Tests exercise real Git blob export, update/backup/rollback, local edits, concurrent changes, failed persistence, native plugin verification and user-selected schedule XML. CI runs on Windows, Linux and macOS with Python 3.11 and 3.12.

### Existing approaches

[Vercel's skills CLI](https://github.com/vercel-labs/skills) already provides an open skills management ecosystem. [crawfordxx/skill-updater](https://github.com/crawfordxx/skill-updater) targets Claude Code update checks. This is an independent implementation focused on previously copied Codex skill folders, verified baselines, rollback and user-configured Windows schedules. No code from those projects is bundled.

The plugin adapter follows the [official Codex CLI implementation](https://github.com/openai/codex/blob/main/codex-rs/cli/src/plugin_cmd.rs) and detects failures rather than editing internal caches.

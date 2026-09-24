---
name: overleaf-skills
description: Operate Overleaf projects from Codex or Claude Code with session-cookie access, Git edits, project management, collaboration, compilation, and source/PDF/log downloads. Use when the user asks to set Overleaf credentials, copy, inspect or edit remote projects, manage project settings or collaborators, review comments or tracked changes, compile LaTeX, download artifacts, or inspect project history. Prefer local file tools when a project is already downloaded and only local inspection is requested.
---

# Overleaf Skills

Use `scripts/overleaf_skill.py` relative to this SKILL.md. Invoke it with Python 3.10+ (`python` on Windows, usually `python3` on macOS). Resolve the installed path from the loaded skill, not the current working directory. The examples below use `<script>` for that absolute path.

## Credentials

Keep one credential pair per assistant. `account set` replaces the saved pair and is the account switch; never print raw credentials in responses.

```sh
python <script> account set --session "overleaf_session2=..." --git-token "olp_..."
python <script> account show
python <script> account clear
```

Credentials resolve from explicit arguments, then `OVERLEAF_SESSION` / `OVERLEAF_GIT_TOKEN`, then saved state. Either a bare session value or a full Cookie header is accepted. When asked to switch credentials, request only values not already provided. The state is outside the installed skill folder, under the assistant's home in `overleaf-skills/state.json`; it is plaintext and must remain private. Installation does not include or create a credential pair.

## Project Identity

Use 24-character hexadecimal project IDs, never filesystem paths or ambiguous project names. Run `projects` when the ID is unknown. Operations target the remote server; get user authorization before writes or deletion, and verify the project and target files first.

```sh
python <script> projects
python <script> archived-projects
python <script> trashed-projects
python <script> create-project --name "My Paper"
python <script> copy-project --project-id SOURCE_PROJECT_ID --name "My Paper Copy"
python <script> import-project --source-path paper.zip --name "My Paper"
python <script> rename-project --project-id PROJECT_ID --name "New Title"
python <script> archive-project --project-id PROJECT_ID
python <script> unarchive-project --project-id PROJECT_ID
python <script> trash-project --project-id PROJECT_ID
python <script> restore-project --project-id PROJECT_ID
python <script> delete-project --project-id PROJECT_ID --confirm-name "My Paper"
```

`projects` lists only the active workspace. Archived and trashed projects have separate listings; projects in the trash appear only in `trashed-projects`. `restore-project` returns a trashed project to the workspace; `unarchive-project` returns an archived project. `delete-project` permanently deletes rather than moving to trash. Deletion requires a matching project name; `--force` is only for an independently verified target. `purge-trash --confirm-count N` permanently deletes the current trash after checking its count; inspect `trashed-projects` before using it and stop if a partial deletion is reported.

A new blank project often already contains `main.tex`; inspect it before replacement. ZIP import creates a new project, not a merge. Public template-gallery browsing is not implemented.

`copy-project` uses the session cookie and Overleaf's native project copy, preserving files, folders, compiler and main document settings without changing the source. Continue editing and compiling with the returned new `id`, not the source ID. Sharing permissions and project history are not copied. If the request times out, check `projects` before retrying to avoid duplicate copies.

## Settings And File Structure

These commands use the session cookie, without Git. `status` reports the actual main document and compiler; do not infer the main document from its filename. Read `project-settings` for the available TeX Live `imageName` identifiers before changing `--image-name`.

```sh
python <script> project-settings --project-id PROJECT_ID
python <script> project-settings --project-id PROJECT_ID --compiler xelatex --main-file paper.tex
python <script> list-entities --project-id PROJECT_ID
python <script> create-folder --project-id PROJECT_ID --file-path chapters
python <script> rename-file --project-id PROJECT_ID --file-path draft.tex --name paper.tex
python <script> move-file --project-id PROJECT_ID --file-path paper.tex --folder chapters
python <script> delete-folder --project-id PROJECT_ID --file-path scratch --recursive
```

`list-entities` provides file, document and folder IDs. Rename and move also accept folders and preserve entity IDs; `--folder .` means the project root. Parent and destination folders must already exist. Folder deletion includes all contents.

## Read And Compile

Cookie-based reads use ZIP snapshots and support UTF-8 and GB18030/GBK sources.

```sh
python <script> status --project-id PROJECT_ID
python <script> list-files --project-id PROJECT_ID --extension .tex
python <script> read-file --project-id PROJECT_ID --file-path main.tex
python <script> sections --project-id PROJECT_ID --file-path main.tex --title "Introduction"
python <script> download-source-zip --project-id PROJECT_ID --output project.zip
python <script> download-source --project-id PROJECT_ID --output-dir project-source
python <script> compile --project-id PROJECT_ID
python <script> download-pdf --project-id PROJECT_ID --output report.pdf
python <script> download-log --project-id PROJECT_ID --output compile.log
```

For requested PDF output without a suitable local TeX toolchain, use remote compilation. Respect the project's bibliography engine and template; inspect compilation logs before changing citation conventions.

To download PDF and logs from the same build, use `compile --project-id PROJECT_ID --result-file build.json`, then pass `--compile-result build.json` to both download commands. The file is scoped to the project, server and session; expired artifacts require an explicit new compilation. Without this option, each download compiles first.

## Edit And Review

File content edits and Git history require Git integration. Read the target before editing, review the intended change, and stop on a Git failure rather than making further remote writes. Project management, settings and collaboration use the session cookie instead.

```sh
python <script> create-file --project-id PROJECT_ID --file-path chapter/new.tex --content-file new.tex
python <script> rewrite-file --project-id PROJECT_ID --file-path main.tex --content-file main.tex
python <script> edit-file --project-id PROJECT_ID --file-path main.tex --old-string "teh" --new-string "the"
python <script> update-section --project-id PROJECT_ID --file-path main.tex --section-title "Introduction" --content-file body.tex
python <script> write-section --project-id PROJECT_ID --file-path main.tex --section-title "Introduction" --content-file section.tex --commit-message "Replace introduction"
python <script> upload-file --project-id PROJECT_ID --file-path figures/chart.png --source-path chart.png
python <script> delete-file --project-id PROJECT_ID --file-path scratch.tex --commit-message "Remove scratch file"
python <script> sync-project --project-id PROJECT_ID
python <script> list-history --project-id PROJECT_ID --limit 5
python <script> get-diff --project-id PROJECT_ID --from-ref HEAD~1 --to-ref HEAD
```

`create-file` requires a new path; `rewrite-file` requires an existing one. `write-file` can create or replace. `upload-file` preserves bytes. `update-section` retains the existing heading; `write-section` replaces it with supplied full section content. Exact text edits require exactly one match.

For several file edits, use `batch-files --project-id PROJECT_ID --manifest edits.json --commit-message "Update paper"`. The manifest is a JSON array, processed in order and pushed as one Git commit:

```json
[
  {"action": "write", "path": "chapter.tex", "content": "Chapter text", "overwrite": true},
  {"action": "upload", "path": "figures/chart.png", "source": "chart.png"},
  {"action": "move", "path": "old.tex", "destination": "chapters/old.tex"},
  {"action": "delete", "path": "scratch.tex"}
]
```

Paths are project-relative; upload sources are relative to the manifest unless absolute. Existing write/upload targets require `overwrite: true`; moves require a new destination. Batch operations require a clean cache. On a local write or Git failure, preserve the cache and inspect it before retrying; do not assume rollback. Use native rename/move commands when retaining document IDs or main-document references matters.

When the user requests a reversible remote write check, use `git-smoke-write --project-id PROJECT_ID`. It pushes a test file and then its removal/restoration; both commits remain in history. Do not run this remote write check merely to inspect a project.

## Collaboration

All commands below take `--project-id PROJECT_ID` and use the current session's permissions. Inspect targets before modifying access or review state. Sharing URLs grant access; disclose them only to the intended recipient. Server feature availability and account limits still apply.

| Command | Actions and arguments |
| --- | --- |
| `collaborators` | `list`, `invites`; `invite --email ADDRESS --privileges readOnly\|readAndWrite\|review`; `set --user-id ID --privileges LEVEL`; `remove --user-id ID`; `revoke` or `resend --invite-id ID` |
| `sharing` | `tokens`, `enable`, `disable` for token links; `link`, `set-link --privileges readOnly\|readAndWrite\|review\|none` for servers offering the sharing-link API |
| `comments` | `list`; `add --doc-id ID --quote "Exact source text" --content "Comment"`; `reply --thread-id ID --content "Reply"`; `edit --thread-id ID --message-id ID --content "Replacement"`; `delete-message --thread-id ID --message-id ID` |
| `comments` | `resolve`, `reopen`, `delete-thread`, each with `--doc-id ID --thread-id ID` |
| `review` | `list --doc-id ID`; `accept` or `reject --doc-id ID --expected-version N --change-id ID` (repeat `--change-id` for multiple changes) |
| `review tracking` | Read the current setting, or change it with `--enabled true\|false` and either `--user-id ID` or `--guests` |

Get document IDs from `list-entities`, thread/message IDs from `comments list`, and change IDs plus the current document version from `review list`. Review snapshots may include text marked for deletion. Re-read after edits; change IDs and offsets must come from the same version. Comments accept `--content-file`; repeated quoted text needs `--start` as a UTF-16 offset into the snapshot. A timed-out mutation may already have applied: inspect its result before retrying. Git content edits do not create tracked-change suggestions.

## Cache

Reuse project caches across commands. At the end of a project task, inspect and clean only the finished project's cache:

```sh
python <script> cache-clean --project-id PROJECT_ID --dry-run
python <script> cache-clean --project-id PROJECT_ID
```

Dirty repositories are preserved unless `--force` is explicitly authorized. Cache cleanup does not clear credentials. Run `--help` or a command's `--help` for options and timeouts. If session reads fail, verify with `projects`; if Git access fails, report the error before changing content.

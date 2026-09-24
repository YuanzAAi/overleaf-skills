---
name: overleaf-skills
description: Operate Overleaf projects from Codex or Claude Code with session-cookie reads, Git edits, project management, compilation, and source/PDF/log downloads. Use when the user asks to set Overleaf credentials, copy, inspect or edit remote projects, compile LaTeX, download artifacts, or review project history. Prefer local file tools when a project is already downloaded and only local inspection is requested.
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
python <script> create-project --name "My Paper"
python <script> copy-project --project-id SOURCE_PROJECT_ID --name "My Paper Copy"
python <script> delete-project --project-id PROJECT_ID --confirm-name "My Paper"
```

A new blank project often already contains `main.tex`; inspect it before replacement. Deletion requires a matching project name; `--force` is only for an independently verified target. Public template-gallery browsing is not implemented.

`copy-project` uses the session cookie and Overleaf's native project copy, preserving files, folders, compiler and main document settings without changing the source. Continue editing and compiling with the returned new `id`, not the source ID. Sharing permissions and project history are not copied. If the request times out, check `projects` before retrying to avoid duplicate copies.

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

## Edit And Review

Writes and history require Git integration. Read the target before editing, review the intended change, and stop on a Git failure rather than making further remote writes.

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

When the user requests a reversible remote write check, use `git-smoke-write --project-id PROJECT_ID`. It pushes a test file and then its removal/restoration; both commits remain in history. Do not run this remote write check merely to inspect a project.

## Cache

Reuse project caches across commands. At the end of a project task, inspect and clean only the finished project's cache:

```sh
python <script> cache-clean --project-id PROJECT_ID --dry-run
python <script> cache-clean --project-id PROJECT_ID
```

Dirty repositories are preserved unless `--force` is explicitly authorized. Cache cleanup does not clear credentials. Run `--help` or a command's `--help` for options and timeouts. If session reads fail, verify with `projects`; if Git access fails, report the error before changing content.

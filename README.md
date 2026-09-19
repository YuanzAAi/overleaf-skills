# Overleaf Skills

[简体中文](README.cn.md)

Read, edit and compile Overleaf projects from **Codex** or **Claude Code**. One shared Python CLI, no MCP server and no pip dependencies.

## Install

Requires **Python 3.10+** and **Git** on your PATH. Run the command for your coding agent, then restart its session.

| Coding agent | Installation directory |
| --- | --- |
| Codex | `~/.codex/skills/overleaf-skills` (`CODEX_HOME` supported) |
| Claude Code | `~/.claude/skills/overleaf-skills` (`CLAUDE_CONFIG_DIR` supported) |

**Windows / PowerShell**

```powershell
# Codex
(Invoke-WebRequest https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py).Content | python - --agent codex
# Claude Code
(Invoke-WebRequest https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py).Content | python - --agent claude
```

**macOS / Terminal**

```sh
# Codex
curl -fsSL https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py | python3 - --agent codex
# Claude Code
curl -fsSL https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py | python3 - --agent claude
```

These commands download and execute this repository's installer; review [install.py](install.py) first. Add `--update` to update an existing installation, or `--uninstall` to remove skill files. Credential state and project caches are preserved.

Both coding agents use the same script and instructions. The installer includes `agents/openai.yaml` for Codex and adds Claude Code's `allowed-tools` frontmatter only to its own installation.

## Use

Ask your coding agent:

```text
Use overleaf-skills. Set my cookie to overleaf_session2=... and Git token to olp_...
List my projects and read main.tex from the project I select.
Update the introduction, compile the project and download its PDF.
```

Use only credentials and projects you are authorized to access. The session cookie enables project listing, source reads/downloads and compilation; writes/history use Overleaf Git integration, which must be available to your account. Replace the saved pair to switch accounts.

| Workflow | Included |
| --- | --- |
| Read | Projects, files, LaTeX sections, UTF-8 and GBK sources |
| Edit | Exact text edits, section updates, file creation/replacement, binary uploads, deletion |
| Build | Compile, download PDF/logs, export source ZIP or directory |
| Review | Git sync, commit history, diffs, optional reversible write check |
| Manage | Create/delete projects, replace credentials, clean project caches |

Full commands: [SKILL.md](SKILL.md), or `python <installed-skill>/scripts/overleaf_skill.py --help` (`python3` on macOS).

## Local State

Each coding agent uses its own `<agent-home>/overleaf-skills/state.json`. It is created on first credential setup and contains **plaintext credentials**; keep it private. No credentials are bundled in this repository. `account show` masks values. Explicit arguments override `OVERLEAF_SESSION` / `OVERLEAF_GIT_TOKEN`, which override saved state.

The default cache is `<agent-home>/overleaf-skills/cache`. `OVERLEAF_SKILL_STATE_DIR` and `OVERLEAF_SKILL_CACHE_DIR` override state and cache locations. Existing users of the earlier Windows-only cache can retain it by setting `OVERLEAF_SKILL_CACHE_DIR` to its previous location. Optional `OVERLEAF_BASE_URL` and `OVERLEAF_GIT_HOST` select a different server.

## Credits

Built with reference to [overleaf-mcp-plus](https://pypi.org/project/overleaf-mcp-plus/) and [mjyoo2/OverleafMCP](https://github.com/mjyoo2/OverleafMCP). Unlike their MCP servers, this project exposes a standard-library Python CLI through coding agent skills, uses ZIP snapshots for source reads, and stores one replaceable credential pair per coding agent. It does not include the citation-verification or SyncTeX layout tools available in overleaf-mcp-plus.

## License

[MIT](LICENSE).

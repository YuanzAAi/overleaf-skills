#!/usr/bin/env python3
"""Install the shared Overleaf skill for Codex or Claude Code."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request
import zipfile


REPOSITORY = "YuanzAAi/overleaf-skills"
FILES = ("SKILL.md", "scripts/overleaf_skill.py", "install.py", "LICENSE")
MARKER = ".overleaf-skill.json"


def install(agent, update=False, uninstall=False):
    variable, folder = {"codex": ("CODEX_HOME", ".codex"), "claude": ("CLAUDE_CONFIG_DIR", ".claude")}[agent]
    home = Path(os.environ.get(variable) or Path.home() / folder).expanduser().resolve()
    skills = home / "skills"
    target = skills / "overleaf-skills"
    files = (*FILES, "agents/openai.yaml") if agent == "codex" else FILES
    if target.is_symlink() or target.resolve() != target:
        raise ValueError(f"Refusing a linked skill directory: {target}")
    if uninstall:
        if not target.exists():
            print("Overleaf skill is not installed.")
            return
        for name in (*files, MARKER):
            path = target / name
            if path.is_symlink() or path.resolve() != path:
                raise ValueError(f"Refusing a linked skill file: {path}")
        for name in (*files, MARKER):
            (target / name).unlink(missing_ok=True)
        for directory in (target / "scripts", target / "agents", target):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        print(f"Removed skill files from {target}; credential state and caches were kept.")
        return
    if target.exists() and not update:
        print(f"Already installed: {target}. Use --update to replace skill files.")
        return
    url = f"https://codeload.github.com/{REPOSITORY}/zip/refs/heads/main"
    request = urllib.request.Request(url, headers={"User-Agent": "overleaf-skills-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(5 * 1024 * 1024 + 1)
    if len(payload) > 5 * 1024 * 1024:
        raise ValueError("Skill download is unexpectedly large")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        content = {}
        for name in files:
            entry = archive.getinfo(f"overleaf-skills-main/{name}")
            if entry.file_size > 1024 * 1024:
                raise ValueError(f"Skill file is unexpectedly large: {name}")
            content[name] = archive.read(entry)
    if agent == "claude":
        text = content["SKILL.md"].decode("utf-8")
        text = text.replace("\n---\n", "\nallowed-tools: Bash, Read, Write, Edit, Grep, Glob\n---\n", 1)
        content["SKILL.md"] = text.encode("utf-8")
    compile(content["scripts/overleaf_skill.py"], "overleaf_skill.py", "exec")
    metadata = {"repository": REPOSITORY, "agent": agent,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}}
    content[MARKER] = (json.dumps(metadata, indent=2) + "\n").encode()
    for name in content:
        path = target / name
        if path.is_symlink() or path.resolve() != path:
            raise ValueError(f"Refusing a linked skill file: {path}")
    skills.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".overleaf-skills-", dir=skills) as temporary:
        staging = Path(temporary)
        for name, data in content.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        target.mkdir(exist_ok=True)
        for name in content:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / name, path)
    print(f"Installed Overleaf skill for {agent}: {target}")
    print("Restart the assistant session to discover the skill. Existing credentials were kept.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "claude"), required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--update", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    try:
        install(args.agent, args.update, args.uninstall)
    except (OSError, ValueError, KeyError, SyntaxError, zipfile.BadZipFile) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

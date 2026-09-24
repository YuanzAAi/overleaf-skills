#!/usr/bin/env python3
"""Overleaf operations for the Codex and Claude Code skill.

The script intentionally separates read and write paths:
- Web/session-cookie path for project listing, ZIP snapshots, status, read,
  section parsing, compile, logs, and PDF download.
- Git-token path only for writes and reversible smoke tests.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import html
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


OVERLEAF_BASE_URL = os.environ.get("OVERLEAF_BASE_URL", "https://www.overleaf.com").rstrip("/")
OVERLEAF_GIT_HOST = os.environ.get("OVERLEAF_GIT_HOST", "git.overleaf.com")
SESSION_COOKIE_NAME = "overleaf_session2"

SKILL_ROOT = Path(__file__).resolve().parent.parent
AGENT_HOME = SKILL_ROOT.parent.parent if SKILL_ROOT.parent.name == "skills" else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
DEFAULT_STATE_DIR = Path(os.environ.get("OVERLEAF_SKILL_STATE_DIR", AGENT_HOME / "overleaf-skills")).expanduser()
DEFAULT_STATE_PATH = DEFAULT_STATE_DIR / "state.json"
DEFAULT_CACHE_DIR = Path(os.environ.get("OVERLEAF_SKILL_CACHE_DIR", DEFAULT_STATE_DIR / "cache")).expanduser()


class SkillError(RuntimeError):
    pass


@dataclasses.dataclass
class Credentials:
    session: str | None
    git_token: str | None


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def redacted(value: str | None) -> str | None:
    if not value:
        return value
    if value.startswith("s%3A"):
        return "s%3A***"
    if value.startswith("olp_"):
        return "olp_***"
    if len(value) <= 10:
        return "***"
    return value[:4] + "***" + value[-4:]


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillError(f"Invalid JSON: {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_session_cookie(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # 同时接受 Cookie 值和完整请求头。
    if "=" not in raw and raw.startswith("s%3A"):
        return raw
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == SESSION_COOKIE_NAME:
            return value.strip()
    if raw.startswith(SESSION_COOKIE_NAME + "="):
        return raw.split("=", 1)[1].strip()
    return raw


def cookie_header(session: str) -> str:
    return f"{SESSION_COOKIE_NAME}={session}"


def load_state() -> dict[str, Any]:
    state = read_json(DEFAULT_STATE_PATH, {})
    if not isinstance(state, dict):
        raise SkillError(f"Invalid credential state: {DEFAULT_STATE_PATH}")
    return state


def save_state(state: dict[str, Any]) -> None:
    write_json(DEFAULT_STATE_PATH, state)


def resolve_credentials(args: argparse.Namespace, need_session: bool = False, need_git: bool = False) -> Credentials:
    state = load_state()
    session = normalize_session_cookie(
        getattr(args, "session", None)
        or os.environ.get("OVERLEAF_SESSION")
        or state.get("session")
    )
    git_token = (
        getattr(args, "git_token", None)
        or os.environ.get("OVERLEAF_GIT_TOKEN")
        or state.get("git_token")
    )

    if need_session and not session:
        raise SkillError("Missing Overleaf session cookie. Use account set or pass --session.")
    if need_git and not git_token:
        raise SkillError("Missing Overleaf Git token. Use account set or pass --git-token.")
    return Credentials(session=session, git_token=git_token)


def make_request(
    method: str,
    url: str,
    *,
    session: str | None = None,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 60,
) -> bytes:
    merged = {
        "User-Agent": "Mozilla/5.0 OverleafSkills/1.0",
        "Accept": "application/json, text/plain, */*",
    }
    if session:
        merged["Cookie"] = cookie_header(session)
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise SkillError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SkillError(f"Network error for {url}: {exc}") from exc


def get_text(url: str, session: str, timeout: int = 60) -> str:
    return make_request("GET", url, session=session, timeout=timeout).decode("utf-8", errors="replace")


def extract_meta_content(page_html: str, name: str) -> str | None:
    pattern = rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]*>'
    match = re.search(pattern, page_html, re.I)
    if not match:
        return None
    tag = match.group(0)
    content_match = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
    if not content_match:
        return None
    return html.unescape(content_match.group(1))


def csrf_token(project_id: str, session: str) -> str:
    page = get_text(f"{OVERLEAF_BASE_URL}/project/{project_id}", session=session, timeout=60)
    token = extract_meta_content(page, "ol-csrfToken")
    if not token:
        raise SkillError("Could not find Overleaf CSRF token; session may be expired.")
    return token


def dashboard_csrf_token(session: str) -> str:
    page = get_text(f"{OVERLEAF_BASE_URL}/", session=session, timeout=60)
    token = extract_meta_content(page, "ol-csrfToken")
    if not token:
        raise SkillError("Could not find Overleaf dashboard CSRF token; session may be expired.")
    return token


def list_projects_web(session: str) -> list[dict[str, Any]]:
    page = get_text(f"{OVERLEAF_BASE_URL}/project", session=session, timeout=60)
    blob = extract_meta_content(page, "ol-prefetchedProjectsBlob")
    if blob is None:
        blob = extract_meta_content(page, "ol-projects")
    if blob is None:
        raise SkillError("Could not find projects blob; session may be expired.")
    data = json.loads(blob)
    projects = data if isinstance(data, list) else data.get("projects", [])
    return [p for p in projects if not p.get("trashed") and not p.get("archived")]


def download_zip_bytes(project_id: str, session: str, timeout: int = 120) -> bytes:
    return make_request(
        "GET",
        f"{OVERLEAF_BASE_URL}/project/{project_id}/download/zip",
        session=session,
        timeout=timeout,
    )


def create_project_web(
    name: str, session: str, template: str | None = None, *, source_project_id: str | None = None
) -> dict[str, Any]:
    operation = "copy-project" if source_project_id else "create-project"
    token = csrf_token(source_project_id, session) if source_project_id else dashboard_csrf_token(session)
    endpoint = f"/project/{source_project_id}/clone" if source_project_id else "/project/new"
    form: dict[str, str] = {"projectName": name}
    if template:
        form["template"] = template
    body = urllib.parse.urlencode(form).encode("utf-8")
    raw = make_request(
        "POST",
        f"{OVERLEAF_BASE_URL}{endpoint}",
        session=session,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-csrf-token": token,
        },
        data=body,
        timeout=300 if source_project_id else 60,
    )
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise SkillError(f"{operation} returned a non-JSON response; check the project list before retrying.") from exc
    if not isinstance(data, dict):
        raise SkillError(f"{operation} returned an invalid response; check the project list before retrying.")
    project_id = data.get("project_id") or data.get("id")
    if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", project_id):
        raise SkillError(f"{operation} did not return a valid project ID; check the session and project list before retrying.")
    if source_project_id and project_id.lower() == source_project_id.lower():
        raise SkillError("copy-project returned the source project ID instead of a new project.")
    return {"id": project_id, "name": data.get("name") or name, "url": f"{OVERLEAF_BASE_URL}/project/{project_id}"}


def project_name_from_dashboard(project_id: str, session: str) -> str | None:
    for project in list_projects_web(session):
        if project.get("id") == project_id:
            return project.get("name")
    return None


def delete_project_web(project_id: str, session: str, *, confirm_name: str | None = None, force: bool = False) -> dict[str, Any]:
    name = project_name_from_dashboard(project_id, session)
    if not force:
        if not confirm_name:
            raise SkillError("delete-project requires --confirm-name PROJECT_NAME or --force")
        if name is None:
            raise SkillError("Project not found in the active dashboard; verify the ID before using --force")
        if confirm_name != name:
            raise SkillError(f"Project name confirmation mismatch: expected {name!r}, got {confirm_name!r}")
    token = csrf_token(project_id, session)
    make_request(
        "DELETE",
        f"{OVERLEAF_BASE_URL}/project/{project_id}",
        session=session,
        headers={"Accept": "application/json, text/plain, */*", "x-csrf-token": token},
        timeout=60,
    )
    return {"id": project_id, "name": name, "deleted": True}


def safe_zip_names(zf: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        parts = PurePosixPath(name).parts
        if any(part in ("..", "") or part.startswith(".") for part in parts):
            continue
        names.append(name)
    return sorted(names)


def zip_snapshot(project_id: str, session: str) -> tuple[list[str], bytes]:
    data = download_zip_bytes(project_id, session)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return safe_zip_names(zf), data


def choose_main_file(files: list[str]) -> str | None:
    tex = [f for f in files if f.lower().endswith(".tex")]
    if not tex:
        return None
    for exact in ("main.tex", "JNUThesis.tex", "thesis.tex"):
        for f in tex:
            if PurePosixPath(f).name == exact:
                return f
    for f in tex:
        if "main" in PurePosixPath(f).name.lower():
            return f
    return tex[0]


def read_from_zip(zip_data: bytes, file_path: str) -> str:
    normalized = str(PurePosixPath(file_path))
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        names = set(safe_zip_names(zf))
        if normalized not in names:
            raise SkillError(f"File not found in project ZIP: {file_path}")
        with zf.open(normalized) as fh:
            return decode_text(fh.read())


def extract_zip_safe(zip_data: bytes, output_dir: Path, *, overwrite: bool = False, project_id: str | None = None) -> int:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise SkillError(f"Output directory is not empty: {output_dir}. Use --overwrite to replace/merge.")
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            dest = (output_dir / PurePosixPath(member.filename).as_posix()).resolve()
            try:
                dest.relative_to(output_dir)
            except ValueError as exc:
                raise SkillError(f"Unsafe zip entry escapes output directory: {member.filename}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    if project_id:
        metadata = {
            "project_id": project_id,
            "overleaf_url": f"{OVERLEAF_BASE_URL}/project/{project_id}",
            "source": "zip",
            "downloaded_at": now_iso(),
        }
        (output_dir / ".overleaf-project.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count


def decode_text(data: bytes) -> str:
    """Decode Overleaf text files, including older Chinese GBK templates."""
    text, _ = decode_text_with_encoding(data)
    return text


def decode_text_with_encoding(data: bytes) -> tuple[str, str]:
    """Decode text and return the encoding that should be used for write-back."""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text.count("\ufffd") <= max(1, len(text) // 200):
            return text, ("utf-8" if encoding == "utf-8-sig" else encoding)
    return data.decode("utf-8", errors="replace"), "utf-8"


def read_repo_text(path: Path) -> str:
    return decode_text(path.read_bytes())


def read_repo_text_with_encoding(path: Path) -> tuple[str, str]:
    return decode_text_with_encoding(path.read_bytes())


SECTION_LEVELS = {
    "part": -1,
    "chapter": 0,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
    "subparagraph": 5,
}


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    escaped = False
    for i in range(open_index, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def parse_sections(content: str) -> list[dict[str, Any]]:
    opener = re.compile(r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?(?:\[[^\]]*\])?\{")
    sections: list[dict[str, Any]] = []
    for match in opener.finditer(content):
        open_idx = match.end() - 1
        close_idx = find_matching_brace(content, open_idx)
        if close_idx == -1:
            continue
        command = match.group(1)
        title = content[open_idx + 1 : close_idx].strip()
        sections.append(
            {
                "type": command,
                "level": SECTION_LEVELS.get(command, 99),
                "title": title,
                "index": match.start(),
                "headingEnd": close_idx + 1,
            }
        )
    for idx, section in enumerate(sections):
        current_level = section["level"]
        end = len(content)
        for next_section in sections[idx + 1 :]:
            if next_section["level"] <= current_level:
                end = next_section["index"]
                break
        section["end"] = end
        section["content"] = content[section["headingEnd"] : end].strip()
    return sections


def section_content(content: str, title: str) -> str:
    sections = parse_sections(content)
    for section in sections:
        if section["title"] == title:
            return content[section["index"] : section["end"]]
    raise SkillError(f"Section not found: {title}")


def replace_section(content: str, title: str, new_content: str) -> str:
    sections = parse_sections(content)
    for section in sections:
        if section["title"] == title:
            return content[: section["index"]] + new_content.rstrip() + "\n\n" + content[section["end"] :]
    raise SkillError(f"Section not found: {title}")


def cache_path(project_id: str, git_token: str) -> Path:
    digest = hashlib.sha256(git_token.encode("utf-8")).hexdigest()[:10]
    return DEFAULT_CACHE_DIR / f"{project_id}-{digest}"


def git_url(project_id: str, git_token: str) -> str:
    return f"https://git:{git_token}@{OVERLEAF_GIT_HOST}/{project_id}"


def sanitize_output(text: str, git_token: str | None = None) -> str:
    if git_token:
        text = text.replace(git_token, "***")
    text = re.sub(r"https://git:[^@\s]+@", "https://git:***@", text)
    text = re.sub(r"olp_[A-Za-z0-9]+", "olp_***", text)
    text = re.sub(r"s%3A[^\s\"']+", "s%3A***", text)
    return text


def run_git(args: list[str], *, cwd: Path | None = None, git_token: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillError(f"git {' '.join(args[:3])} timed out after {timeout}s") from exc


def repo_is_valid(repo: Path) -> bool:
    if not repo.exists():
        return False
    proc = run_git(["-C", str(repo), "rev-parse", "--verify", "HEAD"], timeout=20)
    return proc.returncode == 0


def remove_tree(path: Path) -> None:
    if path.exists():
        def onerror(function: Any, failed_path: str, exc_info: Any) -> None:
            os.chmod(failed_path, stat.S_IWRITE)
            function(failed_path)

        shutil.rmtree(path, onerror=onerror)


def ensure_repo(project_id: str, git_token: str, *, clone_timeout: int, pull_timeout: int) -> Path:
    repo = cache_path(project_id, git_token)
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if repo.exists() and not repo_is_valid(repo):
        remove_tree(repo)
    if repo.exists():
        proc = run_git(["-C", str(repo), "pull", "--ff-only"], git_token=git_token, timeout=pull_timeout)
        if proc.returncode != 0:
            raise SkillError("git pull failed: " + sanitize_output(proc.stderr or proc.stdout, git_token))
    else:
        url = git_url(project_id, git_token)
        proc = run_git(
            [
                "-c",
                "http.version=HTTP/1.1",
                "clone",
                "--",
                url,
                str(repo),
            ],
            git_token=git_token,
            timeout=clone_timeout,
        )
        if proc.returncode != 0:
            if repo.exists() and not repo_is_valid(repo):
                remove_tree(repo)
            raise SkillError("git clone failed: " + sanitize_output(proc.stderr or proc.stdout, git_token))
        run_git(["-C", str(repo), "config", "user.email", os.environ.get("OVERLEAF_GIT_AUTHOR_EMAIL", "overleaf-skill@local")], timeout=20)
        run_git(["-C", str(repo), "config", "user.name", os.environ.get("OVERLEAF_GIT_AUTHOR_NAME", "Overleaf Skill")], timeout=20)
    return repo


def safe_repo_path(repo: Path, file_path: str) -> Path:
    root = repo.resolve()
    target = (repo / PurePosixPath(file_path).as_posix()).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SkillError(f"Path escapes repository root: {file_path}") from exc
    return target


def git_commit_push(repo: Path, file_path: str, message: str, git_token: str, *, push_timeout: int) -> dict[str, Any]:
    add = run_git(["-C", str(repo), "add", "--", file_path], git_token=git_token, timeout=30)
    if add.returncode != 0:
        raise SkillError("git add failed: " + sanitize_output(add.stderr or add.stdout, git_token))
    commit = run_git(["-C", str(repo), "commit", "-m", message], git_token=git_token, timeout=60)
    if commit.returncode != 0:
        out = commit.stderr or commit.stdout
        if "nothing to commit" in out:
            return {"committed": False, "pushed": False, "message": "No changes to commit"}
        raise SkillError("git commit failed: " + sanitize_output(out, git_token))
    push = run_git(["-C", str(repo), "push"], git_token=git_token, timeout=push_timeout)
    if push.returncode != 0:
        raise SkillError("git push failed: " + sanitize_output(push.stderr or push.stdout, git_token))
    return {"committed": True, "pushed": True, "push": sanitize_output(push.stdout or push.stderr, git_token)}


def git_commit_push_paths(repo: Path, file_paths: list[str], message: str, git_token: str, *, push_timeout: int) -> dict[str, Any]:
    add = run_git(["-C", str(repo), "add", "--", *file_paths], git_token=git_token, timeout=30)
    if add.returncode != 0:
        raise SkillError("git add failed: " + sanitize_output(add.stderr or add.stdout, git_token))
    commit = run_git(["-C", str(repo), "commit", "-m", message], git_token=git_token, timeout=60)
    if commit.returncode != 0:
        out = commit.stderr or commit.stdout
        if "nothing to commit" in out:
            return {"committed": False, "pushed": False, "message": "No changes to commit"}
        raise SkillError("git commit failed: " + sanitize_output(out, git_token))
    push = run_git(["-C", str(repo), "push"], git_token=git_token, timeout=push_timeout)
    if push.returncode != 0:
        raise SkillError("git push failed: " + sanitize_output(push.stderr or push.stdout, git_token))
    return {"committed": True, "pushed": True, "push": sanitize_output(push.stdout or push.stderr, git_token)}


def git_dirty(repo: Path) -> str:
    proc = run_git(["-C", str(repo), "status", "--short"], timeout=30)
    if proc.returncode != 0:
        raise SkillError("git status failed: " + sanitize_output(proc.stderr or proc.stdout))
    return proc.stdout.strip()


def ensure_full_history(repo: Path, git_token: str, timeout: int = 180) -> None:
    proc = run_git(["-C", str(repo), "rev-parse", "--is-shallow-repository"], git_token=git_token, timeout=30)
    if proc.returncode != 0:
        return
    if proc.stdout.strip().lower() == "true":
        fetch = run_git(["-C", str(repo), "fetch", "--unshallow"], git_token=git_token, timeout=timeout)
        if fetch.returncode != 0:
            raise SkillError("git fetch --unshallow failed: " + sanitize_output(fetch.stderr or fetch.stdout, git_token))


def cache_dirs(project_id: str | None = None) -> list[Path]:
    if not DEFAULT_CACHE_DIR.exists():
        return []
    root = DEFAULT_CACHE_DIR.resolve()
    pattern = f"{project_id}-*" if project_id else "*"
    dirs: list[Path] = []
    for path in DEFAULT_CACHE_DIR.glob(pattern):
        if not path.is_dir():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if resolved.parent == root:
            dirs.append(path)
    return sorted(dirs)


def command_account(args: argparse.Namespace) -> None:
    if args.account_cmd == "set":
        session = normalize_session_cookie(args.session)
        git_token = args.git_token.strip()
        if not session:
            raise SkillError("Session cookie is empty")
        if not git_token:
            raise SkillError("Git token is empty")
        state = {
            "session": session,
            "git_token": git_token,
            "updated_at": now_iso(),
        }
        save_state(state)
        print_json(
            {
                "saved_to": str(DEFAULT_STATE_PATH),
                "session": redacted(session),
                "git_token": redacted(git_token),
                "updated_at": state["updated_at"],
            }
        )
        return

    if args.account_cmd == "show":
        state = load_state()
        session = normalize_session_cookie(state.get("session"))
        git_token = state.get("git_token")
        print_json(
            {
                "state_path": str(DEFAULT_STATE_PATH),
                "configured": bool(session and git_token),
                "session": redacted(session),
                "git_token": redacted(git_token),
                "updated_at": state.get("updated_at"),
            }
        )
        return

    if args.account_cmd == "clear":
        DEFAULT_STATE_PATH.unlink(missing_ok=True)
        print_json({"cleared": str(DEFAULT_STATE_PATH)})
        return

    raise SkillError(f"Unknown account command: {args.account_cmd}")


def command_projects(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    projects = list_projects_web(creds.session or "")
    print_json(
        {
            "count": len(projects),
            "projects": [
                {"id": p.get("id"), "name": p.get("name"), "archived": p.get("archived", False), "trashed": p.get("trashed", False)}
                for p in projects
            ],
        }
    )


def command_create_project(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    result = create_project_web(args.name, creds.session or "", template=args.template)
    print_json(result)


def command_copy_project(args: argparse.Namespace) -> None:
    name = args.name.strip()
    if not name:
        raise SkillError("copy-project requires a non-empty --name")
    creds = resolve_credentials(args, need_session=True)
    result = create_project_web(name, creds.session or "", source_project_id=args.project_id)
    print_json(result)


def command_delete_project(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    result = delete_project_web(args.project_id, creds.session or "", confirm_name=args.confirm_name, force=args.force)
    print_json(result)


def command_status(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    files, zip_data = zip_snapshot(args.project_id, creds.session or "")
    tex_files = [f for f in files if f.lower().endswith(".tex")]
    main_file = choose_main_file(files)
    sections: list[dict[str, Any]] = []
    if main_file:
        content = read_from_zip(zip_data, main_file)
        sections = parse_sections(content)
    print_json(
        {
            "project_id": args.project_id,
            "total_files": len(files),
            "tex_files": len(tex_files),
            "main_file": main_file,
            "sections": [
                {"type": s["type"], "level": s["level"], "title": s["title"], "index": s["index"]}
                for s in sections
            ],
            "files_preview": files[: args.preview],
        }
    )


def command_list_files(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    files, _ = zip_snapshot(args.project_id, creds.session or "")
    if args.extension:
        files = [f for f in files if f.endswith(args.extension)]
    print_json({"project_id": args.project_id, "count": len(files), "files": files})


def command_read_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    _, zip_data = zip_snapshot(args.project_id, creds.session or "")
    content = read_from_zip(zip_data, args.file_path)
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        print_json({"written": args.output, "chars": len(content)})
    else:
        sys.stdout.write(content)


def command_sections(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    _, zip_data = zip_snapshot(args.project_id, creds.session or "")
    content = read_from_zip(zip_data, args.file_path)
    sections = parse_sections(content)
    if args.title:
        sys.stdout.write(section_content(content, args.title))
    else:
        print_json(
            [
                {"type": s["type"], "level": s["level"], "title": s["title"], "index": s["index"], "end": s["end"]}
                for s in sections
            ]
        )


def compile_project(project_id: str, session: str) -> dict[str, Any]:
    csrf = csrf_token(project_id, session)
    body = json.dumps(
        {
            "check": "silent",
            "draft": False,
            "incrementalCompilesEnabled": True,
            "rootDocId": None,
            "stopOnFirstError": False,
        }
    ).encode("utf-8")
    raw = make_request(
        "POST",
        f"{OVERLEAF_BASE_URL}/project/{project_id}/compile",
        session=session,
        headers={"Content-Type": "application/json", "Accept": "application/json", "x-csrf-token": csrf},
        data=body,
        timeout=180,
    )
    return json.loads(raw.decode("utf-8", errors="replace"))


def build_output_url(url: str, clsi_server_id: str | None) -> str:
    full = url if url.startswith("http") else OVERLEAF_BASE_URL + url
    if clsi_server_id and "clsiserverid=" not in full:
        sep = "&" if "?" in full else "?"
        full = f"{full}{sep}clsiserverid={urllib.parse.quote(clsi_server_id)}"
    return full


def command_compile(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    data = compile_project(args.project_id, creds.session or "")
    print_json(
        {
            "status": data.get("status"),
            "output_files": data.get("outputFiles", []),
            "clsi_server_id": data.get("clsiServerId"),
        }
    )


def command_download_pdf(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    data = compile_project(args.project_id, creds.session or "")
    pdf_file = None
    for item in data.get("outputFiles", []):
        if item.get("path") == "output.pdf" and item.get("url"):
            pdf_file = item
            break
    if not pdf_file:
        raise SkillError(f"No output.pdf in compile result; status={data.get('status')}")
    url = build_output_url(pdf_file["url"], data.get("clsiServerId"))
    pdf = make_request("GET", url, session=creds.session, timeout=180)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pdf)
    print_json({"status": data.get("status"), "output": str(output), "bytes": len(pdf)})


def command_download_log(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    data = compile_project(args.project_id, creds.session or "")
    log_file = None
    for item in data.get("outputFiles", []):
        if item.get("path") == "output.log" and item.get("url"):
            log_file = item
            break
    if not log_file:
        raise SkillError(f"No output.log in compile result; status={data.get('status')}")
    url = build_output_url(log_file["url"], data.get("clsiServerId"))
    log_data = make_request("GET", url, session=creds.session, timeout=180)
    text = log_data.decode("utf-8", errors="replace")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print_json({"status": data.get("status"), "output": str(output), "chars": len(text)})
    else:
        sys.stdout.write(text)


def command_download_source_zip(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    zip_data = download_zip_bytes(args.project_id, creds.session or "", timeout=180)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(zip_data)
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        count = len([n for n in zf.namelist() if not n.endswith("/")])
    print_json({"project_id": args.project_id, "output": str(output), "bytes": len(zip_data), "entries": count})


def command_download_source(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_session=True)
    zip_data = download_zip_bytes(args.project_id, creds.session or "", timeout=180)
    count = extract_zip_safe(zip_data, Path(args.output_dir), overwrite=args.overwrite, project_id=args.project_id)
    print_json({"project_id": args.project_id, "output_dir": str(Path(args.output_dir).resolve()), "entries": count})


def command_create_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    content = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else args.content
    if content is None:
        raise SkillError("Provide --content or --content-file")
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    if target.exists():
        raise SkillError(f"File already exists: {args.file_path}. Use rewrite-file or write-file to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result = git_commit_push(repo, args.file_path, args.commit_message or f"Add {args.file_path}", creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), "created": True, **result})


def command_write_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    content = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else args.content
    if content is None:
        raise SkillError("Provide --content or --content-file")
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result = git_commit_push(repo, args.file_path, args.commit_message, creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), **result})


def command_rewrite_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    content = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else args.content
    if content is None:
        raise SkillError("Provide --content or --content-file")
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    if not target.exists():
        raise SkillError(f"File does not exist: {args.file_path}. Use create-file to create it.")
    target.write_text(content, encoding="utf-8")
    result = git_commit_push(repo, args.file_path, args.commit_message or f"Rewrite {args.file_path}", creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), "rewritten": True, **result})


def command_edit_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    if not target.exists():
        raise SkillError(f"File does not exist: {args.file_path}")
    current, encoding = read_repo_text_with_encoding(target)
    count = current.count(args.old_string)
    if count == 0:
        raise SkillError(f"old-string not found in {args.file_path}")
    if count > 1:
        raise SkillError(f"old-string appears {count} times in {args.file_path}; make it match exactly once.")
    target.write_text(current.replace(args.old_string, args.new_string, 1), encoding=encoding)
    result = git_commit_push(repo, args.file_path, args.commit_message or f"Edit {args.file_path}", creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), "edited": True, **result})


def command_upload_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    source = Path(args.source_path).expanduser()
    if not source.is_file():
        raise SkillError(f"Source file does not exist or is not a regular file: {source}")
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    if target.exists() and not args.overwrite:
        raise SkillError(f"Destination already exists: {args.file_path}. Use --overwrite to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    result = git_commit_push(repo, args.file_path, args.commit_message or f"Upload {args.file_path}", creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "source_path": str(source), "repo": str(repo), "bytes": target.stat().st_size, **result})


def command_delete_file(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    if not target.exists():
        if args.missing_ok:
            print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), "deleted": False, "missing": True})
            return
        raise SkillError(f"File does not exist: {args.file_path}")
    if target.is_dir():
        raise SkillError(f"Refusing to delete directory via delete-file: {args.file_path}")
    target.unlink()
    result = git_commit_push(repo, args.file_path, args.commit_message, creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), "deleted": True, **result})


def command_update_section(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    new_body = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else args.content
    if new_body is None:
        raise SkillError("Provide --content or --content-file")
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    current, encoding = read_repo_text_with_encoding(target)
    sections = parse_sections(current)
    for section in sections:
        if section["title"] == args.section_title:
            updated = current[: section["headingEnd"]] + "\n" + new_body.rstrip() + "\n\n" + current[section["end"] :]
            target.write_text(updated, encoding=encoding)
            result = git_commit_push(repo, args.file_path, args.commit_message or f"Update section {args.section_title}", creds.git_token or "", push_timeout=args.push_timeout)
            print_json({"project_id": args.project_id, "file_path": args.file_path, "section_title": args.section_title, "repo": str(repo), **result})
            return
    raise SkillError(f"Section not found: {args.section_title}")


def command_write_section(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    new_content = Path(args.content_file).read_text(encoding="utf-8") if args.content_file else args.content
    if new_content is None:
        raise SkillError("Provide --content or --content-file")
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    target = safe_repo_path(repo, args.file_path)
    current, encoding = read_repo_text_with_encoding(target)
    updated = replace_section(current, args.section_title, new_content)
    target.write_text(updated, encoding=encoding)
    result = git_commit_push(repo, args.file_path, args.commit_message, creds.git_token or "", push_timeout=args.push_timeout)
    print_json({"project_id": args.project_id, "file_path": args.file_path, "repo": str(repo), **result})


def command_sync_project(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    repo = cache_path(args.project_id, creds.git_token or "")
    if repo.exists() and repo_is_valid(repo):
        dirty = git_dirty(repo)
        if dirty and not args.force:
            print_json({"project_id": args.project_id, "repo": str(repo), "synced": False, "dirty": dirty, "message": "Dirty cache repo; use --force to pull anyway or clean local changes."})
            return
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    print_json({"project_id": args.project_id, "repo": str(repo), "synced": True})


def command_list_history(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    ensure_full_history(repo, creds.git_token or "")
    limit = max(1, min(args.limit or 20, 200))
    cmd = ["-C", str(repo), "log", f"--max-count={limit}", "--date=iso-strict", "--pretty=format:%H%x09%h%x09%ad%x09%an <%ae>%x09%s"]
    if args.since:
        cmd.append(f"--since={args.since}")
    if args.until:
        cmd.append(f"--until={args.until}")
    if args.file_path:
        cmd.extend(["--", args.file_path])
    proc = run_git(cmd, git_token=creds.git_token, timeout=60)
    if proc.returncode != 0:
        raise SkillError("git log failed: " + sanitize_output(proc.stderr or proc.stdout, creds.git_token))
    commits = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) == 5:
            commits.append({"hash": parts[0], "short": parts[1], "date": parts[2], "author": parts[3], "message": parts[4]})
    print_json({"project_id": args.project_id, "repo": str(repo), "count": len(commits), "commits": commits})


def command_get_diff(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    ensure_full_history(repo, creds.git_token or "")
    context = max(0, min(args.context_lines, 10))
    diff_args = ["-C", str(repo), "diff", f"--unified={context}", "--no-color"]
    if args.to_ref:
        diff_args.extend([args.from_ref, args.to_ref])
    else:
        diff_args.append(args.from_ref)
    if args.file_path:
        diff_args.extend(["--", args.file_path])
    proc = run_git(diff_args, git_token=creds.git_token, timeout=60)
    if proc.returncode != 0:
        raise SkillError("git diff failed: " + sanitize_output(proc.stderr or proc.stdout, creds.git_token))
    diff = proc.stdout
    truncated = len(diff) > args.max_chars
    print_json({"project_id": args.project_id, "repo": str(repo), "truncated": truncated, "diff": diff[: args.max_chars]})


def command_git_smoke_write(args: argparse.Namespace) -> None:
    creds = resolve_credentials(args, need_git=True)
    repo = ensure_repo(args.project_id, creds.git_token or "", clone_timeout=args.clone_timeout, pull_timeout=args.pull_timeout)
    test_path = args.file_path or "_codex_overleaf_skill_smoke_test.txt"
    target = safe_repo_path(repo, test_path)
    original_exists = target.exists()
    original = target.read_text(encoding="utf-8") if original_exists else None
    stamp = f"Overleaf skill smoke test\nproject={args.project_id}\ntime={now_iso()}\n"
    target.write_text(stamp, encoding="utf-8")
    create_result = git_commit_push(
        repo,
        test_path,
        args.commit_message or "Codex Overleaf skill smoke test create",
        creds.git_token or "",
        push_timeout=args.push_timeout,
    )
    if original_exists:
        target.write_text(original or "", encoding="utf-8")
        revert_msg = "Codex Overleaf skill smoke test restore"
    else:
        target.unlink(missing_ok=True)
        revert_msg = "Codex Overleaf skill smoke test remove"
    revert_result = git_commit_push(repo, test_path, revert_msg, creds.git_token or "", push_timeout=args.push_timeout)
    print_json(
        {
            "project_id": args.project_id,
            "repo": str(repo),
            "test_path": test_path,
            "content_restored": True,
            "create": create_result,
            "revert": revert_result,
            "note": "Remote file content restored via a second commit; Git history retains the smoke-test commits.",
        }
    )


def command_cache_clean(args: argparse.Namespace) -> None:
    if not args.all and not args.project_id:
        raise SkillError("Provide --project-id PROJECT_ID or --all")
    removed: list[str] = []
    skipped: list[dict[str, str]] = []
    for path in cache_dirs(None if args.all else args.project_id):
        if repo_is_valid(path):
            dirty = git_dirty(path)
            if dirty and not args.force:
                skipped.append({"path": str(path), "reason": "dirty git working tree"})
                continue
        if args.dry_run:
            skipped.append({"path": str(path), "reason": "dry-run"})
            continue
        remove_tree(path)
        removed.append(str(path))
    print_json({"cache_dir": str(DEFAULT_CACHE_DIR), "removed": removed, "skipped": skipped})


def add_common_auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", help="overleaf_session2 value or full Cookie header")
    parser.add_argument("--git-token", help="Overleaf Git Integration token")


def add_git_timeouts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--clone-timeout", type=int, default=180, help="Git clone timeout seconds")
    parser.add_argument("--pull-timeout", type=int, default=90, help="Git pull timeout seconds")
    parser.add_argument("--push-timeout", type=int, default=120, help="Git push timeout seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Overleaf skill operations")
    sub = parser.add_subparsers(dest="cmd", required=True)

    account = sub.add_parser("account", help="Replace, inspect, or clear the current credentials")
    account_sub = account.add_subparsers(dest="account_cmd", required=True)
    account_set = account_sub.add_parser("set")
    account_set.add_argument("--session", required=True)
    account_set.add_argument("--git-token", required=True)
    account_sub.add_parser("show")
    account_sub.add_parser("clear")

    projects = sub.add_parser("projects", help="List projects via Overleaf Web")
    add_common_auth(projects)

    create_project = sub.add_parser("create-project", help="Create a new blank Overleaf project via Web")
    add_common_auth(create_project)
    create_project.add_argument("--name", required=True)
    create_project.add_argument("--template", help="Optional Overleaf built-in template parameter, such as example")

    copy_project = sub.add_parser("copy-project", help="Copy an existing Overleaf project via Web")
    add_common_auth(copy_project)
    copy_project.add_argument("--project-id", required=True, help="Source project ID")
    copy_project.add_argument("--name", required=True, help="Name for the new copy")

    delete_project = sub.add_parser("delete-project", help="Delete an Overleaf project via Web")
    add_common_auth(delete_project)
    delete_project.add_argument("--project-id", required=True)
    delete_project.add_argument("--confirm-name", help="Project name that must match before deletion")
    delete_project.add_argument("--force", action="store_true", help="Delete without name confirmation")

    status = sub.add_parser("status", help="Project status via Web ZIP")
    add_common_auth(status)
    status.add_argument("--project-id", required=True)
    status.add_argument("--preview", type=int, default=20)

    list_files = sub.add_parser("list-files", help="List files via Web ZIP")
    add_common_auth(list_files)
    list_files.add_argument("--project-id", required=True)
    list_files.add_argument("--extension")

    read_file = sub.add_parser("read-file", help="Read a file via Web ZIP")
    add_common_auth(read_file)
    read_file.add_argument("--project-id", required=True)
    read_file.add_argument("--file-path", required=True)
    read_file.add_argument("--output")

    sections = sub.add_parser("sections", help="List or print LaTeX sections via Web ZIP")
    add_common_auth(sections)
    sections.add_argument("--project-id", required=True)
    sections.add_argument("--file-path", required=True)
    sections.add_argument("--title", help="If set, print that section's full source")

    compile_p = sub.add_parser("compile", help="Compile project via Overleaf Web")
    add_common_auth(compile_p)
    compile_p.add_argument("--project-id", required=True)

    pdf = sub.add_parser("download-pdf", help="Compile and download output.pdf")
    add_common_auth(pdf)
    pdf.add_argument("--project-id", required=True)
    pdf.add_argument("--output", required=True)

    log = sub.add_parser("download-log", help="Compile and download or print output.log")
    add_common_auth(log)
    log.add_argument("--project-id", required=True)
    log.add_argument("--output")

    source_zip = sub.add_parser("download-source-zip", help="Download project source ZIP via Web")
    add_common_auth(source_zip)
    source_zip.add_argument("--project-id", required=True)
    source_zip.add_argument("--output", required=True)

    source = sub.add_parser("download-source", help="Download and extract project source via Web")
    add_common_auth(source)
    source.add_argument("--project-id", required=True)
    source.add_argument("--output-dir", required=True)
    source.add_argument("--overwrite", action="store_true")

    create_file = sub.add_parser("create-file", help="Create a new text file via Git and push")
    add_common_auth(create_file)
    add_git_timeouts(create_file)
    create_file.add_argument("--project-id", required=True)
    create_file.add_argument("--file-path", required=True)
    create_file.add_argument("--content")
    create_file.add_argument("--content-file")
    create_file.add_argument("--commit-message")

    write_file = sub.add_parser("write-file", help="Write full file via Git and push")
    add_common_auth(write_file)
    add_git_timeouts(write_file)
    write_file.add_argument("--project-id", required=True)
    write_file.add_argument("--file-path", required=True)
    write_file.add_argument("--content")
    write_file.add_argument("--content-file")
    write_file.add_argument("--commit-message", required=True)

    rewrite_file = sub.add_parser("rewrite-file", help="Replace an existing text file via Git and push")
    add_common_auth(rewrite_file)
    add_git_timeouts(rewrite_file)
    rewrite_file.add_argument("--project-id", required=True)
    rewrite_file.add_argument("--file-path", required=True)
    rewrite_file.add_argument("--content")
    rewrite_file.add_argument("--content-file")
    rewrite_file.add_argument("--commit-message")

    edit_file = sub.add_parser("edit-file", help="Exact single-match text replacement via Git and push")
    add_common_auth(edit_file)
    add_git_timeouts(edit_file)
    edit_file.add_argument("--project-id", required=True)
    edit_file.add_argument("--file-path", required=True)
    edit_file.add_argument("--old-string", required=True)
    edit_file.add_argument("--new-string", required=True)
    edit_file.add_argument("--commit-message")

    upload_file = sub.add_parser("upload-file", help="Upload a local binary or text file via Git and push")
    add_common_auth(upload_file)
    add_git_timeouts(upload_file)
    upload_file.add_argument("--project-id", required=True)
    upload_file.add_argument("--file-path", required=True)
    upload_file.add_argument("--source-path", required=True)
    upload_file.add_argument("--commit-message")
    upload_file.add_argument("--overwrite", action="store_true")

    delete_file = sub.add_parser("delete-file", help="Delete one file via Git and push")
    add_common_auth(delete_file)
    add_git_timeouts(delete_file)
    delete_file.add_argument("--project-id", required=True)
    delete_file.add_argument("--file-path", required=True)
    delete_file.add_argument("--commit-message", required=True)
    delete_file.add_argument("--missing-ok", action="store_true")

    update_section = sub.add_parser("update-section", help="Replace one LaTeX section body while preserving its heading")
    add_common_auth(update_section)
    add_git_timeouts(update_section)
    update_section.add_argument("--project-id", required=True)
    update_section.add_argument("--file-path", required=True)
    update_section.add_argument("--section-title", required=True)
    update_section.add_argument("--content")
    update_section.add_argument("--content-file")
    update_section.add_argument("--commit-message")

    write_section = sub.add_parser("write-section", help="Replace one LaTeX section via Git and push")
    add_common_auth(write_section)
    add_git_timeouts(write_section)
    write_section.add_argument("--project-id", required=True)
    write_section.add_argument("--file-path", required=True)
    write_section.add_argument("--section-title", required=True)
    write_section.add_argument("--content")
    write_section.add_argument("--content-file")
    write_section.add_argument("--commit-message", required=True)

    smoke = sub.add_parser("git-smoke-write", help="Create and remove a tiny test file via Git")
    add_common_auth(smoke)
    add_git_timeouts(smoke)
    smoke.add_argument("--project-id", required=True)
    smoke.add_argument("--file-path")
    smoke.add_argument("--commit-message")

    sync_project = sub.add_parser("sync-project", help="Clone or pull the latest Overleaf Git state")
    add_common_auth(sync_project)
    add_git_timeouts(sync_project)
    sync_project.add_argument("--project-id", required=True)
    sync_project.add_argument("--force", action="store_true")

    history = sub.add_parser("list-history", help="Show Overleaf Git history")
    add_common_auth(history)
    add_git_timeouts(history)
    history.add_argument("--project-id", required=True)
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--file-path")
    history.add_argument("--since")
    history.add_argument("--until")

    diff = sub.add_parser("get-diff", help="Show a git diff between refs or working tree")
    add_common_auth(diff)
    add_git_timeouts(diff)
    diff.add_argument("--project-id", required=True)
    diff.add_argument("--from-ref", default="HEAD")
    diff.add_argument("--to-ref")
    diff.add_argument("--file-path")
    diff.add_argument("--context-lines", type=int, default=3)
    diff.add_argument("--max-chars", type=int, default=120000)

    cache_clean = sub.add_parser("cache-clean", help="Remove local overleaf-skills Git cache directories")
    cache_target = cache_clean.add_mutually_exclusive_group(required=True)
    cache_target.add_argument("--project-id")
    cache_target.add_argument("--all", action="store_true")
    cache_clean.add_argument("--dry-run", action="store_true")
    cache_clean.add_argument("--force", action="store_true", help="Remove dirty cache repos too")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project_id = getattr(args, "project_id", None)
        if project_id is not None and not re.fullmatch(r"[0-9a-fA-F]{24}", project_id):
            raise SkillError("Project ID must be 24 hexadecimal characters")
        if args.cmd == "account":
            command_account(args)
        elif args.cmd == "create-project":
            command_create_project(args)
        elif args.cmd == "copy-project":
            command_copy_project(args)
        elif args.cmd == "delete-project":
            command_delete_project(args)
        elif args.cmd == "projects":
            command_projects(args)
        elif args.cmd == "status":
            command_status(args)
        elif args.cmd == "list-files":
            command_list_files(args)
        elif args.cmd == "read-file":
            command_read_file(args)
        elif args.cmd == "sections":
            command_sections(args)
        elif args.cmd == "compile":
            command_compile(args)
        elif args.cmd == "download-pdf":
            command_download_pdf(args)
        elif args.cmd == "download-log":
            command_download_log(args)
        elif args.cmd == "download-source-zip":
            command_download_source_zip(args)
        elif args.cmd == "download-source":
            command_download_source(args)
        elif args.cmd == "create-file":
            command_create_file(args)
        elif args.cmd == "write-file":
            command_write_file(args)
        elif args.cmd == "rewrite-file":
            command_rewrite_file(args)
        elif args.cmd == "edit-file":
            command_edit_file(args)
        elif args.cmd == "upload-file":
            command_upload_file(args)
        elif args.cmd == "delete-file":
            command_delete_file(args)
        elif args.cmd == "update-section":
            command_update_section(args)
        elif args.cmd == "write-section":
            command_write_section(args)
        elif args.cmd == "git-smoke-write":
            command_git_smoke_write(args)
        elif args.cmd == "sync-project":
            command_sync_project(args)
        elif args.cmd == "list-history":
            command_list_history(args)
        elif args.cmd == "get-diff":
            command_get_diff(args)
        elif args.cmd == "cache-clean":
            command_cache_clean(args)
        else:
            parser.error(f"Unknown command: {args.cmd}")
        return 0
    except (SkillError, OSError) as exc:
        print_json({"error": sanitize_output(str(exc))})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

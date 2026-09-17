# Overleaf Skills

[English](README.md) · [MIT](LICENSE)

在 **Codex** 或 **Claude Code** 中读取、编辑和编译 Overleaf 项目。共用一套 Python CLI，不需要 MCP 服务或额外 pip 依赖。

## 安装

需要 **Python 3.10+** 和 **Git**，且命令行能够找到它们。选择对应助手安装，完成后重新打开助手会话。

| 助手 | 安装位置 |
| --- | --- |
| Codex | `~/.codex/skills/overleaf-skills`，支持 `CODEX_HOME` |
| Claude Code | `~/.claude/skills/overleaf-skills`，支持 `CLAUDE_CONFIG_DIR` |

**Windows / PowerShell**

```powershell
# Codex
(Invoke-WebRequest https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py).Content | python - --agent codex
# Claude Code
(Invoke-WebRequest https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py).Content | python - --agent claude
```

**macOS / 终端**

```sh
# Codex
curl -fsSL https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py | python3 - --agent codex
# Claude Code
curl -fsSL https://raw.githubusercontent.com/YuanzAAi/overleaf-skills/main/install.py | python3 - --agent claude
```

以上命令会下载并执行本仓库的安装程序，可以先查看 [install.py](install.py)。在命令末尾加 `--update` 更新，或加 `--uninstall` 卸载；两者都保留账号状态和项目缓存。

两种助手共用脚本和操作说明。安装器为 Codex 安装 `agents/openai.yaml`，仅在 Claude Code 的入口中加入 `allowed-tools` 字段。

## 使用

直接告诉助手：

```text
使用 overleaf-skills，把 Cookie 设为 overleaf_session2=...，Git token 设为 olp_...
列出项目，读取我选中的项目里的 main.tex。
修改引言，编译项目并下载 PDF。
```

仅使用自己有权访问的凭据和项目。Cookie 用于列出项目、读取和下载源码、编译；写入和历史功能使用 Git，需要账号已具有 Overleaf Git 集成权限。重新设置一组凭据即可换号。

| 场景 | 功能 |
| --- | --- |
| 读取 | 项目、文件、LaTeX 章节，兼容 UTF-8 和 GBK 源码 |
| 编辑 | 精确文本替换、章节更新、新建与覆盖文件、二进制上传、删除 |
| 构建 | 编译、下载 PDF 与日志、导出源码 ZIP 或文件夹 |
| 审阅 | Git 同步、历史、差异，可选的可还原写入检查 |
| 管理 | 新建与删除项目、替换凭据、清理项目缓存 |

完整命令见 [SKILL.md](SKILL.md)，或运行 `python <安装目录>/scripts/overleaf_skill.py --help`，macOS 使用 `python3`。

## 本地状态

两种助手分别使用 `<助手目录>/overleaf-skills/state.json`，首次设置凭据时创建，包含**明文凭据**，请妥善保管。仓库不包含任何账号信息，`account show` 只显示脱敏值。凭据优先级为命令参数、`OVERLEAF_SESSION` / `OVERLEAF_GIT_TOKEN`、已保存状态。

默认缓存位于 `<助手配置目录>/overleaf-skills/cache`。可用 `OVERLEAF_SKILL_STATE_DIR` 和 `OVERLEAF_SKILL_CACHE_DIR` 指定状态及缓存位置。旧版 Windows 用户如需继续复用原缓存，可将 `OVERLEAF_SKILL_CACHE_DIR` 指向原位置。`OVERLEAF_BASE_URL` 和 `OVERLEAF_GIT_HOST` 可用于其他服务器。

## 鸣谢

维护者：[YuanzAAi](https://github.com/YuanzAAi)。

本项目参考了 [overleaf-mcp-plus](https://pypi.org/project/overleaf-mcp-plus/) 和 [mjyoo2/OverleafMCP](https://github.com/mjyoo2/OverleafMCP)。与它们的 MCP 服务不同，这里通过 skill 调用标准库 Python CLI，使用 ZIP 快照读取源码，并为每种助手保存一组可替换凭据。不包含 overleaf-mcp-plus 的文献核验和 SyncTeX 页面布局分析工具。

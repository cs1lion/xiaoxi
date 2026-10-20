"""Read-only integration doctor for roster workers and MCP config.

    python scripts/doctor_integration.py
    python scripts/doctor_integration.py --worker dsh
    python scripts/doctor_integration.py --json

It checks wiring, not model quality: adapter registration, executable/args,
workspace roots, env key presence, MCP config shape, and skill discovery.
No secrets are printed; no network calls are made.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.assistant.adapters import adapter_types  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402


def resolve_executable(value: str) -> Path | None:
    text = value.strip()
    if text == "":
        return None
    if text == "python":
        venv = ROOT / ".venv" / "Scripts" / "python.exe"
        if venv.is_file():
            return venv
        found = shutil.which("python")
        return Path(found) if found else None
    if text == "node":
        found = shutil.which("node")
        return Path(found) if found else None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return candidate if candidate.exists() else None


def resolve_workspace(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path)


def check_worker(agent: dict, registered: list[str]) -> dict:
    agent_id = str(agent.get("id") or "")
    adapter = str(agent.get("adapter") or "manual")
    issues: list[str] = []
    warnings: list[str] = []
    if not agent_id:
        issues.append("缺少 id")
    if agent.get("enabled") is False:
        warnings.append("enabled=false，不会被选将")
    if adapter not in registered:
        issues.append(f"adapter {adapter!r} 未登记；可选 {registered}")
    if adapter in ("cli_subproc", "durable", "mcp"):
        executable = agent.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            issues.append("缺少 executable")
        else:
            resolved = resolve_executable(executable)
            if resolved is None:
                issues.append(f"executable 不存在或不在 PATH：{executable}")
        for arg in agent.get("args") or []:
            text = str(arg)
            if text.startswith("-") or not any(sep in text for sep in ("/", "\\")):
                continue
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            if not candidate.exists():
                warnings.append(f"args 里的路径不存在：{text}")
    if adapter == "http_api":
        endpoint = agent.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith("http"):
            issues.append("http_api 缺少 http(s) endpoint")
    env = agent.get("env") if isinstance(agent.get("env"), dict) else {}
    workspace = agent.get("workspace_root") or env.get("AQI_WORKSPACE_ROOT")
    if adapter in ("durable", "cli_subproc") and workspace:
        root = resolve_workspace(str(workspace))
        if not root.exists():
            warnings.append(f"workspace_root 不存在，首次派单时需可创建：{root}")
    approval = agent.get("approval_tools")
    if approval is not None and not isinstance(approval, str):
        issues.append("approval_tools 必须是逗号分隔字符串")
    return {"id": agent_id, "adapter": adapter, "issues": issues, "warnings": warnings,
            "workspace_root": str(workspace or "")}


def check_mcp_config() -> dict:
    path = ROOT / "config" / "mcp.json"
    if not path.exists():
        return {"path": str(path), "servers": [], "issues": [], "warnings": ["config/mcp.json 不存在"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "servers": [], "issues": [f"JSON 解析失败：{exc}"],
                "warnings": []}
    servers = data.get("mcpServers") if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return {"path": str(path), "servers": [], "issues": ["mcpServers 必须是对象"], "warnings": []}
    rows, issues, warnings = [], [], []
    for name, config in servers.items():
        if not isinstance(config, dict):
            issues.append(f"{name}: 配置不是对象")
            continue
        if config.get("enabled") is False:
            warnings.append(f"{name}: enabled=false")
        command = config.get("command")
        url = config.get("url")
        if isinstance(command, str) and command:
            if resolve_executable(command) is None:
                issues.append(f"{name}: command 不存在：{command}")
        elif isinstance(url, str) and url.startswith("http"):
            pass
        else:
            issues.append(f"{name}: 需要 command 或 http(s) url")
        rows.append({"name": name, "transport": "stdio" if command else "http",
                     "command": command if isinstance(command, str) else None,
                     "url": url if isinstance(url, str) else None})
    return {"path": str(path), "servers": rows, "issues": issues, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", default="", help="only this worker id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    roster = load_roster()
    registered = adapter_types()
    workers = [
        check_worker(agent, registered)
        for agent in (roster.get("agents") or [])
        if isinstance(agent, dict) and (not args.worker or agent.get("id") == args.worker)
    ]
    mcp = check_mcp_config()
    skills_root = ROOT / "config" / "skills"
    skills = sorted(str(path.relative_to(skills_root)) for path in skills_root.rglob("SKILL.md")) \
        if skills_root.exists() else []
    report = {
        "workers": workers,
        "mcp": mcp,
        "skills": {"root": str(skills_root), "count": len(skills), "items": skills[:50]},
        "registered_adapters": registered,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== workers ==")
        for worker in workers:
            status = "FAIL" if worker["issues"] else "OK"
            print(f"{status:4} {worker['id']:12} adapter={worker['adapter']}")
            for issue in worker["issues"]:
                print(f"     issue: {issue}")
            for warning in worker["warnings"]:
                print(f"     warn : {warning}")
        print("== mcp ==")
        print(f"config: {mcp['path']}  servers={len(mcp['servers'])}")
        for issue in mcp["issues"]:
            print(f"  issue: {issue}")
        for warning in mcp["warnings"]:
            print(f"  warn : {warning}")
        print(f"== skills ==\nroot: {skills_root}  count={len(skills)}")
    problems = [issue for worker in workers for issue in worker["issues"]] + list(mcp["issues"])
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Renew the tokens of the knowledge base servers configured in Claude Desktop.

Tokens expire after 8 hours. This script finds every server in the Claude Desktop
config that runs this repository with a KB_TOKEN, issues a new token for the same user
and Context Repo, and leaves everything else in the file untouched. A backup of the
file is written next to it first.

Usage: uv run python scripts/refresh_desktop_tokens.py [path/to/claude_desktop_config.json]
Then restart Claude Desktop (Cmd+Q, reopen).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import jwt

from kb.authz.identity import SECRET, issue_token
from kb.config import ROOT

DEFAULT = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    config = json.loads(path.read_text())
    renewed = []
    for name, server in config.get("mcpServers", {}).items():
        token = server.get("env", {}).get("KB_TOKEN")
        if not token or str(ROOT) not in server.get("args", []):
            continue
        # The old token may have expired: check its signature, not its expiry.
        claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False})
        server["env"]["KB_TOKEN"] = issue_token(claims["sub"], claims["agent"])
        renewed.append(f"{name}: {claims['sub']}")
    if not renewed:
        sys.exit(f"No knowledge base servers found in {path}")
    shutil.copy2(path, path.with_suffix(".json.bak"))
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    print("Renewed tokens (backup: " + path.with_suffix(".json.bak").name + "):")
    print("\n".join(f"  {line}" for line in renewed))
    print("Restart Claude Desktop (Cmd+Q, reopen) to use them.")


if __name__ == "__main__":
    main()

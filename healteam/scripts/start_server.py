"""Start the local API; run the Next.js frontend in a separate terminal."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the HealTeam local API")
    parser.add_argument("--root", type=Path, help="Source/resource root; defaults to HEALTEAM_ROOT")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port (default: API_PORT or 8000)")
    parser.add_argument("--reload", action="store_true", help="Reload on source changes")
    args = parser.parse_args()

    repo_root = (args.root or Path(os.getenv("HEALTEAM_ROOT") or Path(__file__).resolve().parents[1])).resolve()
    if not (repo_root / "packages" / "memory").is_dir():
        parser.error("HEALTEAM_ROOT must point to the healteam source directory")

    from dotenv import load_dotenv

    load_dotenv(repo_root / ".env", override=False)
    os.environ["HEALTEAM_ROOT"] = str(repo_root)
    try:
        port = args.port if args.port is not None else int(os.getenv("API_PORT", "8000"))
    except ValueError:
        parser.error("API_PORT must be an integer")
    if not 1 <= port <= 65535:
        parser.error("Port must be between 1 and 65535")

    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "apps" / "api"))
    from repo_bootstrap import bootstrap_local_packages

    bootstrap_local_packages(repo_root)
    import uvicorn

    print(f"HealTeam API: http://{args.host}:{port}")
    print("网页入口: http://127.0.0.1:3000（另行启动 apps/web）")
    print("按 Ctrl+C 停止服务。")
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()

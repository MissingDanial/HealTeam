"""Start HealTeam API server with frontend.

Usage: py scripts/start_server.py
Then open http://localhost:8000 in your browser.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repo_bootstrap import bootstrap_local_packages

bootstrap_local_packages(REPO_ROOT)

# Add apps/api to path so uvicorn can find the app.
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))


def main():
    import uvicorn

    print("=" * 60)
    print("HealTeam 家庭治疗 AI 模拟系统")
    print("=" * 60)
    print(f"\n服务地址: http://localhost:8000")
    print(f"API 文档: http://localhost:8000/docs")
    print(f"\n按 Ctrl+C 停止服务器\n")

    # Auto-open browser.
    webbrowser.open("http://localhost:8000")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

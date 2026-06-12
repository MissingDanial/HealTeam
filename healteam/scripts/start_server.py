"""Start HealTeam API server with frontend.

Usage: py scripts/start_server.py
Then open http://localhost:8000 in your browser.
"""

from __future__ import annotations

import importlib.util
import sys
import webbrowser
from pathlib import Path

# Register packages (same as test_mimo.py).
REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = REPO_ROOT / "packages"


def _register_package(alias: str, pkg_dir: Path) -> None:
    if not (pkg_dir / "__init__.py").exists():
        raise FileNotFoundError(f"{pkg_dir}/__init__.py missing")
    spec = importlib.util.spec_from_file_location(
        alias, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {alias} at {pkg_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)


_register_package("healteam_memory", PACKAGES_DIR / "memory")
_register_package("healteam_agents", PACKAGES_DIR / "agents")

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

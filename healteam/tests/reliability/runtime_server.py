"""Child-process fixture for real socket/crash tests; never a production route."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import uvicorn
from app.main import create_app
from app.services.training_manager import TrainingManager


def main() -> None:
    port, delay, mode, pid_file = sys.argv[1:]
    if mode == "blocked_report":
        original = TrainingManager._generate_report

        async def delayed_report(self, record):
            await asyncio.sleep(120)
            return await original(self, record)

        TrainingManager._generate_report = delayed_report
    Path(pid_file).write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    uvicorn.run(
        create_app(training_delay=float(delay)),
        host="127.0.0.1",
        port=int(port),
        log_level="warning",
        timeout_graceful_shutdown=1,
    )


if __name__ == "__main__":
    main()

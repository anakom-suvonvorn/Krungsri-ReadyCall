"""Run the public API and the customer simulator.

    uv run python -m readycall.entrypoints.api

Then open http://127.0.0.1:8000/sim for the simulator, or /docs for the API.

One of several process entrypoints (`D2`): the worker, media gateway and STT worker are
separate processes sharing the same package, so the STT box does not have to install the
web stack and vice versa.
"""

from __future__ import annotations

import uvicorn

from readycall.api.app import create_app
from readycall.config import get_settings
from readycall.console import enable_utf8


def main() -> None:
    enable_utf8()
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # structlog already owns logging (`B2` lives in that config)
    )


if __name__ == "__main__":
    main()

"""addex-api: serve the HTTP API and the Stremio addon."""

import argparse
import asyncio
import sys

import uvicorn

from addex_api.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="addex-api", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--behind-proxy", action="store_true",
        help="trust X-Forwarded-* headers (behind Caddy), so absolute URLs use the public "
        "https host",
    )
    args = parser.parse_args()

    config = uvicorn.Config(
        create_app(), host=args.host, port=args.port, loop="none",
        proxy_headers=args.behind_proxy, forwarded_allow_ips="*" if args.behind_proxy else None,
    )
    # psycopg's async mode can't run on Windows' default Proactor event loop, so the
    # loop is created here rather than by uvicorn.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()

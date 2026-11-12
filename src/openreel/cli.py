import argparse
import json
import os
from pathlib import Path

from .service import Service


def main():
    parser = argparse.ArgumentParser(description="OpenReel — video production for your AI, on your machine.")
    parser.add_argument(
        "command", choices=["studio", "mcp", "doctor", "demo", "worker"], nargs="?", default="studio"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("OPENREEL_DATA_DIR", ".openreel")),
        help="Persistent project directory. Use an absolute path in MCP client configuration.",
    )
    parser.add_argument(
        "--import-root", type=Path, help="Allow local MCP file imports only inside this directory."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--public-url",
        default=os.environ.get("OPENREEL_PUBLIC_URL"),
        help="HTTPS origin; enables OAuth and owner-key authentication.",
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("OPENREEL_RENDER_SERVER"),
        help="Remote OpenReel origin for the worker command; omit to use --data-dir.",
    )
    parser.add_argument(
        "--worker-key",
        default=os.environ.get("OPENREEL_WORKER_KEY"),
        help="Dedicated worker credential; prefer setting OPENREEL_WORKER_KEY.",
    )
    args = parser.parse_args()
    if (
        args.command == "studio"
        and args.host not in {"127.0.0.1", "localhost", "::1"}
        and not args.public_url
    ):
        parser.error(
            "A non-loopback host requires --public-url and OPENREEL_OWNER_KEY. See docs/connections.md."
        )
    if args.command == "worker":
        if args.server:
            if not args.worker_key:
                parser.error("Remote workers require OPENREEL_WORKER_KEY or --worker-key.")
            from .remote import run_worker

            run_worker(args.server, args.worker_key, args.data_dir)
            return
    service = Service(
        args.data_dir, args.import_root, render_worker=False if args.command == "worker" else None
    )
    try:
        if args.command == "worker":
            try:
                service.renderer.run_worker()
            except KeyboardInterrupt:
                pass
        elif args.command == "mcp":
            from .mcp_server import build_mcp

            build_mcp(service).run(transport="stdio")
        elif args.command == "doctor":
            print(json.dumps(service.capabilities(), indent=2))
        elif args.command == "demo":
            print(service.demo().model_dump_json(indent=2))
        else:
            import uvicorn

            from .web import create_app

            app = create_app(service, args.public_url, os.environ.get("OPENREEL_OWNER_KEY"))
            uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False)
    finally:
        service.close()


if __name__ == "__main__":
    main()

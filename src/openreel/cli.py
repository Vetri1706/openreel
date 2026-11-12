import argparse
import json
import os
from pathlib import Path

from .service import Service


def main():
    parser = argparse.ArgumentParser(description="OpenReel — video production for your AI, on your machine.")
    parser.add_argument("command", choices=["studio", "mcp", "doctor", "demo"], nargs="?", default="studio")
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
    args = parser.parse_args()
    if (
        args.command == "studio"
        and args.host not in {"127.0.0.1", "localhost", "::1"}
        and not args.public_url
    ):
        parser.error(
            "A non-loopback host requires --public-url and OPENREEL_OWNER_KEY. See docs/connections.md."
        )
    service = Service(args.data_dir, args.import_root)
    if args.command == "mcp":
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


if __name__ == "__main__":
    main()

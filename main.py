import argparse
import os
import sys


def repo_root() -> str:
    # When packaged as a frozen executable (PyInstaller), prefer the
    # directory of the executable so config files are created next to
    # the exe instead of the temporary extraction folder.
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def normalize_argv(argv: list[str] | None) -> list[str]:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return ["web"]          # 雲端預設啟動 Web UI
    if args[0] in ("ui", "server", "web"):
        return args
    if args[0].startswith("-"):
        if args[0] in ("-h", "--help"):
            return args
        return ["web", *args]
    return ["web", *args]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opendata-weather-ua")
    sub = parser.add_subparsers(dest="cmd")

    # ── 桌面 UI（保留相容） ──────────────────────────────────────
    p_ui = sub.add_parser("ui", help="Run desktop UI (Tkinter)")
    p_ui.add_argument(
        "-min",
        "--minimized",
        action="store_true",
        help="Start the desktop UI minimized to the system tray",
    )

    # ── 純 OPC UA Server ─────────────────────────────────────────
    p_srv = sub.add_parser("server", help="Run OPC UA server only")
    p_srv.add_argument("--config", default=os.path.join(repo_root(), "data", "config.json"))

    # ── Web UI（新增）─────────────────────────────────────────────
    p_web = sub.add_parser("web", help="Run Web UI (FastAPI) + OPC UA server manager")
    p_web.add_argument("--host", default="0.0.0.0", help="Web server host (default: 0.0.0.0)")
    p_web.add_argument("--port", type=int, default=8188, help="Web server port (default: 8188)")

    args = parser.parse_args(normalize_argv(argv))
    cmd = args.cmd or "web"

    if cmd == "ui":
        from ui.desktop_ui import main as desktop_main

        try:
            desktop_main(
                repo_root=repo_root(),
                start_minimized=bool(getattr(args, "minimized", False)),
            )
        except KeyboardInterrupt:
            return 0
        return 0

    if cmd == "server":
        from server.opcua_server import main as server_main

        return int(server_main(config_path=args.config) or 0)

    if cmd == "web":
        from webui.app import run as web_run

        web_run(host=args.host, port=args.port)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

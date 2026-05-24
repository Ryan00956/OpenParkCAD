from __future__ import annotations

import argparse
import json
from pathlib import Path

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.exporter_dxf import write_dxf
from openparkcad.exporter_svg import write_svg
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openparkcad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve_parser = subparsers.add_parser("solve", help="Generate a parking layout for a site JSON file.")
    solve_parser.add_argument("site", help="Path to site JSON")
    solve_parser.add_argument("--out", default="output/layout.dxf", help="DXF output path")
    solve_parser.add_argument("--preview", default="output/layout.svg", help="SVG preview path")
    solve_parser.add_argument("--report", default="output/report.json", help="JSON report path")

    args = parser.parse_args(argv)
    if args.command == "solve":
        return _solve(args)
    raise ValueError(f"Unknown command: {args.command}")


def _solve(args: argparse.Namespace) -> int:
    site_path = Path(args.site)
    data = json.loads(site_path.read_text(encoding="utf-8"))
    site = site_from_dict(data)
    layout = generate_layout(site)

    write_dxf(layout, args.out)
    write_svg(layout, args.preview)
    _write_report(layout, args.report)

    print(f"site: {site.name}")
    print(f"stalls: {layout.stall_count}")
    print(f"dxf: {args.out}")
    print(f"preview: {args.preview}")
    print(f"report: {args.report}")
    return 0


def _write_report(layout, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "site": layout.site.name,
        "stall_count": layout.stall_count,
        "aisle_count": len(layout.aisles),
        "selected_angle_degrees": layout.selected_angle_degrees,
        "attempts": [
            {"angle_degrees": attempt.angle_degrees, "stall_count": attempt.stall_count}
            for attempt in layout.attempts
        ],
        "stall": {
            "width": layout.site.stall.width,
            "length": layout.site.stall.length,
        },
        "aisle_width": layout.site.aisle_width,
        "input_diagnostics": build_input_diagnostics(layout.site),
    }
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

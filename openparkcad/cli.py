from __future__ import annotations

import argparse
import json
from pathlib import Path

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.exporter_dxf import write_dxf
from openparkcad.exporter_svg import write_svg
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from openparkcad.traffic_graph import traffic_graph_report


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
        "generation_mode": layout.generation_mode,
        "main_entrance_id": layout.main_entrance_id,
        "selected_angle_degrees": layout.selected_angle_degrees,
        "selected_heading_degrees": layout.selected_heading_degrees,
        "selected_heading_delta_degrees": layout.selected_heading_delta_degrees,
        "selected_entrance_offset": layout.selected_entrance_offset,
        "selected_branch": {
            "side": layout.selected_branch_side,
            "start_u": layout.selected_branch_start_u,
            "length": layout.selected_branch_length,
        },
        "selected_branches": layout.selected_branches,
        "score": layout.score,
        "unsupported_phase1_inputs": layout.unsupported_phase1_inputs,
        "aisles": [
            {
                "id": aisle.id,
                "role": aisle.role,
                "connected_to_entrance_id": aisle.connected_to_entrance_id,
                "parent_aisle_id": aisle.parent_aisle_id,
            }
            for aisle in layout.aisles
        ],
        "stalls": [
            {
                "id": stall.id,
                "served_by_aisle_id": stall.served_by_aisle_id,
                "aisle_side": stall.aisle_side,
            }
            for stall in layout.stalls
        ],
        "attempts": [
            {
                "entrance_id": attempt.entrance_id,
                "heading_degrees": attempt.angle_degrees,
                "heading_delta_degrees": attempt.heading_delta_degrees,
                "entrance_offset": attempt.entrance_offset,
                "branch_side": attempt.branch_side,
                "branch_start_u": attempt.branch_start_u,
                "branch_length": attempt.branch_length,
                "branch_candidates": attempt.branch_candidates,
                "graph_valid": attempt.graph_valid,
                "graph_errors": attempt.graph_errors,
                "stall_count": attempt.stall_count,
            }
            for attempt in layout.attempts
        ],
        "stall": {
            "width": layout.site.stall.width,
            "length": layout.site.stall.length,
        },
        "aisle_width": layout.site.aisle_width,
        "traffic_graph": traffic_graph_report(layout),
        "input_diagnostics": build_input_diagnostics(layout.site, layout),
    }
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

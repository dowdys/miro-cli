#!/usr/bin/env python3
"""
Miro CLI — Board management, items, connectors, and graph operations.

All 29 Miro board operations exposed as CLI subcommands.
Calls the Miro REST API v2 directly.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import api


def _run(handler, params: dict | None = None) -> dict:
    """Run an async handler and return the result dict."""
    try:
        return asyncio.run(handler(params or {}))
    except FileNotFoundError:
        return {"error": "Miro not configured. Run: miro configure"}
    except Exception as e:
        return {"error": str(e)}


def __print_json(data: dict):
    print(json.dumps(data, indent=2))


def __print_error(data: dict):
    if "error" in data:
        print(f"Error: {data['error']}", file=sys.stderr)
        sys.exit(1)


def _extract_arguments(parser):
    import argparse as _argparse
    arguments = {}
    for action in parser._actions:
        if isinstance(action, (_argparse._HelpAction, _argparse._SubParsersAction)):
            continue
        if action.option_strings:
            name = max(action.option_strings, key=len)
        else:
            name = action.dest
        info = {"help": action.help or ""}
        if isinstance(action, (_argparse._StoreTrueAction, _argparse._StoreFalseAction)):
            info["type"] = "boolean"
        elif action.type == int:
            info["type"] = "integer"
        elif action.type == float:
            info["type"] = "number"
        else:
            info["type"] = "string"
        arguments[name] = info
    return arguments


def print_schema(parser):
    import argparse as _argparse
    schema = {"name": parser.prog, "description": parser.description or "", "commands": {}}
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                schema["commands"][name] = {
                    "description": subparser.description or "",
                    "arguments": _extract_arguments(subparser),
                }
    print(json.dumps(schema, indent=2))
    raise SystemExit(0)


# =====================================================================
# Items
# =====================================================================

def cmd_list_boards(args):
    params = {}
    if args.query:
        params["query"] = args.query
    result = _run(api.handle_list_boards, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        boards = result.get("boards", [])
        print(f"Boards ({len(boards)}):\n")
        for b in boards:
            print(f"  [{b['id']}] {b['name']}")
            if b.get("description"):
                print(f"    {b['description'][:80]}")
            if b.get("viewLink"):
                print(f"    {b['viewLink']}")


def cmd_create_board(args):
    params = {"name": args.name}
    if args.description:
        params["description"] = args.description
    result = _run(api.handle_create_board, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        board = result.get("board", result)
        print(f"Created board: {board.get('name', '?')}")
        print(f"  ID: {board.get('id', '?')}")
        if board.get("viewLink"):
            print(f"  URL: {board['viewLink']}")


def cmd_get_board(args):
    params = {"board_id": args.board_id}
    result = _run(api.handle_get_board, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        board = result.get("board", result)
        print(f"Board: {board.get('name', '?')}")
        print(f"  ID: {board.get('id', '?')}")
        if board.get("description"):
            print(f"  Description: {board['description']}")
        if board.get("viewLink"):
            print(f"  URL: {board['viewLink']}")
        if board.get("createdAt"):
            print(f"  Created: {board['createdAt']}")
        if board.get("modifiedAt"):
            print(f"  Modified: {board['modifiedAt']}")


def cmd_update_board(args):
    params = {"board_id": args.board_id}
    if args.name:
        params["name"] = args.name
    if args.description is not None:
        params["description"] = args.description
    result = _run(api.handle_update_board, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        board = result.get("board", result)
        print(f"Updated board: {board.get('name', '?')} [{board.get('id', '?')}]")


def cmd_delete_board(args):
    params = {"board_id": args.board_id}
    result = _run(api.handle_delete_board, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Deleted board {args.board_id} (moved to trash)")


def cmd_get_items(args):
    params = {"board_id": args.board_id}
    if args.type:
        params["type"] = args.type
    if args.color:
        params["color"] = args.color
    if args.search:
        params["search"] = args.search
    if args.full:
        params["full"] = True
    result = _run(api.handle_get_all_items, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        items = result.get("items", [])
        print(f"Items ({result.get('count', 0)}):\n")
        for item in items:
            label = item.get("content") or item.get("title") or item.get("shape") or ""
            color = item.get("color") or item.get("fill") or ""
            pos = f"({item.get('x', '?')}, {item.get('y', '?')})" if "x" in item else ""
            color_str = f" [{color}]" if color else ""
            print(f"  [{item['id']}] {item['type']}{color_str} {pos}")
            if label:
                print(f"    {label[:100]}")
            conns = item.get("connections", [])
            if conns:
                for c in conns:
                    arrow = "->" if c["direction"] == "outgoing" else "<-"
                    print(f"    {arrow} {c['connectedItemId']}: {c.get('connectedContent', '')[:60]}")
        print()


def cmd_update_item(args):
    params = {
        "board_id": args.board_id,
        "item_id": args.item_id,
        "item_type": args.item_type,
    }
    if args.data:
        params["data"] = json.loads(args.data)
    if args.style:
        params["style"] = json.loads(args.style)
    if args.position:
        params["position"] = json.loads(args.position)
    if args.geometry:
        params["geometry"] = json.loads(args.geometry)
    if args.parent:
        params["parent"] = json.loads(args.parent)
    result = _run(api.handle_update_item, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Updated item {args.item_id}")


def cmd_delete_item(args):
    params = {"board_id": args.board_id, "item_id": args.item_id}
    result = _run(api.handle_delete_item, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Deleted item {args.item_id}")


def cmd_move_item(args):
    params = {"board_id": args.board_id, "item_id": args.item_id}
    if args.position:
        params["position"] = json.loads(args.position)
    if args.parent:
        params["parent"] = json.loads(args.parent)
    result = _run(api.handle_move_item, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Moved item {args.item_id}")


def cmd_create_sticky(args):
    params = {"board_id": args.board_id, "content": args.content}
    if args.color:
        params["color"] = args.color
    if args.x is not None:
        params["x"] = args.x
    if args.y is not None:
        params["y"] = args.y
    if args.near:
        params["near"] = args.near
    result = _run(api.handle_create_sticky_note, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        item = result.get("item", {})
        print(f"Created sticky note: {item.get('id', '?')}")


def cmd_create_shape(args):
    params = {"board_id": args.board_id}
    if args.content:
        params["content"] = args.content
    if args.shape:
        params["shape"] = args.shape
    if args.style:
        params["style"] = json.loads(args.style)
    if args.position:
        params["position"] = json.loads(args.position)
    if args.geometry:
        params["geometry"] = json.loads(args.geometry)
    result = _run(api.handle_create_shape, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        item = result.get("item", {})
        print(f"Created shape: {item.get('id', '?')}")


def cmd_bulk_create(args):
    items = json.loads(args.items)
    params = {"board_id": args.board_id, "items": items}
    result = _run(api.handle_bulk_create_items, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Bulk created {result.get('count', 0)} item(s)")
        for item in result.get("created", []):
            print(f"  [{item['id']}] {item.get('type', '')}")
        for err in result.get("errors", []):
            print(f"  ERROR index {err['index']}: {err['error']}")


def cmd_get_item_connections(args):
    params = {"board_id": args.board_id, "item_id": args.item_id}
    result = _run(api.handle_get_item_with_connections, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        item = result.get("item", {})
        label = item.get("content") or item.get("title") or ""
        print(f"Item [{item.get('id')}] {item.get('type', '')}: {label[:80]}")
        print(f"Connections ({result.get('connectionCount', 0)}):")
        for c in result.get("connections", []):
            arrow = "->" if c["direction"] == "outgoing" else "<-"
            print(f"  {arrow} [{c['connectedItemId']}] {c.get('connectedType', '')} — {c.get('connectedContent', '')[:60]}")


# =====================================================================
# Connectors
# =====================================================================

def cmd_create_connector(args):
    params = {
        "board_id": args.board_id,
        "start_item_id": args.start_item_id,
        "end_item_id": args.end_item_id,
    }
    if args.shape:
        params["shape"] = args.shape
    if args.stroke_color:
        params["stroke_color"] = args.stroke_color
    if args.stroke_width:
        params["stroke_width"] = args.stroke_width
    if args.caption:
        params["caption"] = args.caption
    result = _run(api.handle_create_connector, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        c = result.get("connector", {})
        print(f"Created connector: {c.get('id', '?')}")


def cmd_get_connectors(args):
    params = {"board_id": args.board_id}
    result = _run(api.handle_get_connectors, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        connectors = result.get("connectors", [])
        print(f"Connectors ({result.get('count', 0)}):\n")
        for c in connectors:
            start = c.get("startItem", {}).get("id", "?")
            end = c.get("endItem", {}).get("id", "?")
            shape = c.get("shape", "")
            captions = c.get("captions", [])
            cap = captions[0].get("content", "") if captions else ""
            cap_str = f" \"{cap}\"" if cap else ""
            print(f"  [{c['id']}] {start} -> {end} ({shape}){cap_str}")


def cmd_update_connector(args):
    params = {"board_id": args.board_id, "connector_id": args.connector_id}
    if args.shape:
        params["shape"] = args.shape
    if args.style:
        params["style"] = json.loads(args.style)
    if args.caption is not None:
        params["caption"] = args.caption
    result = _run(api.handle_update_connector, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Updated connector {args.connector_id}")


def cmd_delete_connector(args):
    params = {"board_id": args.board_id, "connector_id": args.connector_id}
    result = _run(api.handle_delete_connector, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Deleted connector {args.connector_id}")


# =====================================================================
# Frames
# =====================================================================

def cmd_create_frame(args):
    params = {"board_id": args.board_id}
    if args.title:
        params["title"] = args.title
    if args.position:
        params["position"] = json.loads(args.position)
    if args.geometry:
        params["geometry"] = json.loads(args.geometry)
    if args.style:
        params["style"] = json.loads(args.style)
    result = _run(api.handle_create_frame, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        frame = result.get("frame", {})
        print(f"Created frame: {frame.get('id', '?')}")


def cmd_get_frames(args):
    params = {"board_id": args.board_id}
    result = _run(api.handle_get_frames, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        frames = result.get("frames", [])
        print(f"Frames ({result.get('count', 0)}):\n")
        for f in frames:
            title = f.get("title") or f.get("content") or "(untitled)"
            print(f"  [{f['id']}] {title}")


def cmd_get_frame_items(args):
    params = {"board_id": args.board_id, "frame_id": args.frame_id}
    if args.full:
        params["full"] = True
    result = _run(api.handle_get_items_in_frame, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        items = result.get("items", [])
        print(f"Items in frame {args.frame_id} ({result.get('count', 0)}):\n")
        for item in items:
            label = ""
            if isinstance(item, dict):
                label = item.get("content") or item.get("title") or ""
                itype = item.get("type", "")
                iid = item.get("id", "?")
            else:
                itype = ""
                iid = "?"
            print(f"  [{iid}] {itype}: {label[:80]}")


# =====================================================================
# Bulk Operations
# =====================================================================

def cmd_bulk_update(args):
    updates = json.loads(args.updates)
    params = {"board_id": args.board_id, "updates": updates}
    result = _run(api.handle_bulk_update_items, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Bulk updated {result.get('count', 0)} item(s)")
        for err in result.get("errors", []):
            print(f"  ERROR index {err['index']}: {err['error']}")


def cmd_bulk_delete(args):
    item_ids = json.loads(args.item_ids)
    params = {"board_id": args.board_id, "item_ids": item_ids}
    result = _run(api.handle_bulk_delete_items, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Bulk deleted {result.get('count', 0)} item(s)")
        for err in result.get("errors", []):
            print(f"  ERROR {err['id']}: {err['error']}")


def cmd_bulk_create_connectors(args):
    connectors = json.loads(args.connectors)
    params = {"board_id": args.board_id, "connectors": connectors}
    result = _run(api.handle_bulk_create_connectors, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Bulk created {result.get('count', 0)} connector(s)")
        for err in result.get("errors", []):
            print(f"  ERROR index {err['index']}: {err['error']}")


# =====================================================================
# Advanced
# =====================================================================

def cmd_board_summary(args):
    params = {"board_id": args.board_id}
    result = _run(api.handle_board_summary, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Board Summary ({args.board_id}):")
        print(f"  Total items: {result.get('totalItems', 0)}")
        print(f"  Total connectors: {result.get('totalConnectors', 0)}")
        print(f"\n  Items by type:")
        for t, c in result.get("itemsByType", {}).items():
            print(f"    {t}: {c}")
        sticky_colors = result.get("stickyNotesByColor", {})
        if sticky_colors:
            print(f"\n  Sticky notes by color:")
            for color, c in sticky_colors.items():
                print(f"    {color}: {c}")


def cmd_copy_board(args):
    params = {"board_id": args.board_id}
    if args.title:
        params["title"] = args.title
    if args.description:
        params["description"] = args.description
    result = _run(api.handle_copy_board, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        new_board = result.get("board", {})
        print(f"Board copied. New board ID: {new_board.get('id', '?')}")


def cmd_export_graph(args):
    params = {"board_id": args.board_id}
    if args.frame_id:
        params["frame_id"] = args.frame_id
    result = _run(api.handle_export_graph, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Graph: {result.get('title', '(untitled)')}")
        print(f"  Nodes: {len(result.get('nodes', []))}")
        print(f"  Edges: {len(result.get('edges', []))}")
        print(f"\nNodes:")
        for n in result.get("nodes", []):
            group = f" [{n['group']}]" if n.get("group") else ""
            print(f"  {n['id']}: {n['label'][:60]}{group}")
        print(f"\nEdges:")
        for e in result.get("edges", []):
            label = f" \"{e['label']}\"" if e.get("label") else ""
            print(f"  {e['from']} -> {e['to']}{label}")


def cmd_board_diff(args):
    params = {"board_id": args.board_id}
    if args.frame_id:
        params["frame_id"] = args.frame_id
    result = _run(api.handle_board_diff, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        if result.get("firstSnapshot"):
            print(f"First snapshot saved. {result.get('currentState', {}).get('nodes', 0)} nodes, "
                  f"{result.get('currentState', {}).get('edges', 0)} edges.")
            print("Run again after changes to see diff.")
            return
        s = result.get("summary", {})
        if not result.get("hasChanges"):
            print("No changes since last snapshot.")
            return
        print("Changes since last snapshot:\n")
        if s.get("nodesAdded"):
            print(f"  + {s['nodesAdded']} node(s) added")
            for n in result.get("addedNodes", []):
                print(f"    [{n.get('type', '')}] {n.get('label', '')[:60]}")
        if s.get("nodesRemoved"):
            print(f"  - {s['nodesRemoved']} node(s) removed")
            for n in result.get("removedNodes", []):
                print(f"    [{n.get('type', '')}] {n.get('label', '')[:60]}")
        if s.get("nodesChanged"):
            print(f"  ~ {s['nodesChanged']} node(s) changed")
            for n in result.get("changedNodes", []):
                if "labelAfter" in n:
                    print(f"    {n.get('labelBefore', '')[:30]} -> {n.get('labelAfter', '')[:30]}")
                if "colorAfter" in n:
                    print(f"    color: {n.get('colorBefore', '')} -> {n.get('colorAfter', '')}")
        if s.get("nodesMoved"):
            print(f"  ↔ {s['nodesMoved']} node(s) moved")
            for n in result.get("movedNodes", []):
                print(f"    {n.get('label', '')[:40]} ({n.get('distance', 0)}px)")
        if s.get("edgesAdded"):
            print(f"  + {s['edgesAdded']} edge(s) added")
            for e in result.get("addedEdges", []):
                label = f' "{e["label"]}"' if e.get("label") else ""
                print(f"    {e['from'][:30]} -> {e['to'][:30]}{label}")
        if s.get("edgesRemoved"):
            print(f"  - {s['edgesRemoved']} edge(s) removed")
            for e in result.get("removedEdges", []):
                label = f' "{e["label"]}"' if e.get("label") else ""
                print(f"    {e['from'][:30]} -> {e['to'][:30]}{label}")


def cmd_import_graph(args):
    graph = json.loads(args.graph)
    params = {
        "board_id": args.board_id,
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
    }
    if args.title:
        params["title"] = args.title
    elif graph.get("title"):
        params["title"] = graph["title"]
    if args.direction:
        params["direction"] = args.direction
    if args.x != 0 or args.y != 0:
        params["offset_x"] = args.x
        params["offset_y"] = args.y
    result = _run(api.handle_import_graph, params)
    if "error" in result:
        _print_error(result)
    if args.json:
        _print_json(result)
    else:
        print(f"Imported graph:")
        print(f"  Nodes created: {result.get('nodesCreated', 0)}")
        print(f"  Connectors created: {result.get('connectorsCreated', 0)}")
        if result.get("frameId"):
            print(f"  Frame ID: {result['frameId']}")
        print(f"\nID Mapping:")
        for user_id, miro_id in result.get("idMapping", {}).items():
            print(f"  {user_id} -> {miro_id}")


def cmd_configure(args):
    """Set up Miro API token."""
    config_dir = Path.home() / ".miro-cli"
    config_path = config_dir / "config.json"

    if config_path.exists() and not args.force:
        existing = json.loads(config_path.read_text())
        token_preview = "***" + existing.get("token", "")[-4:]
        print(f"Already configured (token: {token_preview})")
        print("Run with --force to reconfigure.")
        return

    print("Get your Miro API token from: https://miro.com/app/settings/user-profile/apps")
    print("Create a new app or use an existing one, then copy the access token.\n")
    token = input("Paste your Miro API token: ").strip()

    if not token:
        print("No token provided.", file=sys.stderr)
        sys.exit(1)

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"token": token}, indent=2))
    config_path.chmod(0o600)
    print(f"\nSaved to {config_path}")

    # Quick test
    result = _run(api.handle_list_boards)
    if "error" in result:
        print(f"Warning: token test failed — {result['error']}", file=sys.stderr)
    else:
        print("Token verified — you're good to go!")


# =====================================================================
# Parser
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="miro",
        description="Miro board management — items, connectors, frames, and graph operations",
    )
    sub = parser.add_subparsers(dest="command")

    # configure
    p = sub.add_parser("configure", help="Set up your Miro API token")
    p.add_argument("--force", action="store_true", help="Reconfigure even if already set up")
    p.set_defaults(func=cmd_configure)

    # --- Items ---

    # list-boards
    p = sub.add_parser("list-boards", help="List all accessible Miro boards")
    p.add_argument("--query", help="Search boards by name")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_list_boards)

    # create-board
    p = sub.add_parser("create-board", help="Create a new Miro board")
    p.add_argument("name", help="Board name")
    p.add_argument("--description", help="Board description (purpose, project)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_create_board)

    # get-board
    p = sub.add_parser("get-board", help="Get details about a specific board")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get_board)

    # update-board
    p = sub.add_parser("update-board", help="Update a board's name or description")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--name", help="New board name")
    p.add_argument("--description", help="New board description")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_update_board)

    # delete-board
    p = sub.add_parser("delete-board", help="Delete a board (moves to trash)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_delete_board)

    # get-items
    p = sub.add_parser("get-items", help="Get all items on a board (compact mode, with connections)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--type", help="Filter by item type (sticky_note, shape, text, frame, etc.)")
    p.add_argument("--color", help="Filter by fill color")
    p.add_argument("--search", help="Case-insensitive text search in content/title")
    p.add_argument("--full", action="store_true", help="Return full item data instead of compact")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get_items)

    # update-item
    p = sub.add_parser("update-item", help="Update a board item")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("item_id", help="Item ID")
    p.add_argument("item_type", help="Item type (sticky_note, shape, text, card, frame, image, document, embed, app_card)")
    p.add_argument("--data", help="JSON data to update (e.g. '{\"content\": \"new text\"}')")
    p.add_argument("--style", help="JSON style (e.g. '{\"fillColor\": \"red\"}')")
    p.add_argument("--position", help="JSON position (e.g. '{\"x\": 100, \"y\": 200}')")
    p.add_argument("--geometry", help="JSON geometry (e.g. '{\"width\": 300, \"height\": 200}')")
    p.add_argument("--parent", help="JSON parent (e.g. '{\"id\": \"frame_id\"}')")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_update_item)

    # delete-item
    p = sub.add_parser("delete-item", help="Delete an item from a board")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("item_id", help="Item ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_delete_item)

    # move-item
    p = sub.add_parser("move-item", help="Move an item to a new position or parent")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("item_id", help="Item ID")
    p.add_argument("--position", help="JSON position (e.g. '{\"x\": 100, \"y\": 200}')")
    p.add_argument("--parent", help="JSON parent (e.g. '{\"id\": \"frame_id\"}')")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_move_item)

    # create-sticky
    p = sub.add_parser("create-sticky", help="Create a sticky note")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("content", help="Sticky note text content")
    p.add_argument("--color", choices=[
        "gray", "light_yellow", "yellow", "orange", "light_green", "green",
        "dark_green", "cyan", "light_pink", "pink", "violet", "red",
        "light_blue", "blue", "dark_blue", "black",
    ], help="Sticky note color")
    p.add_argument("--x", type=float, help="X position")
    p.add_argument("--y", type=float, help="Y position")
    p.add_argument("--near", help="Place near an item matching this text (searches board content)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_create_sticky)

    # create-shape
    p = sub.add_parser("create-shape", help="Create a shape")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--content", help="Shape text content")
    p.add_argument("--shape", help="Shape type (rectangle, round_rectangle, circle, triangle, rhombus, parallelogram, trapezoid, pentagon, hexagon, octagon, wedge_round_rectangle_callout, star, flow_chart_*, cloud, cross, can, right_arrow, left_arrow, left_right_arrow, left_brace, right_brace, heart)")
    p.add_argument("--style", help="JSON style object")
    p.add_argument("--position", help="JSON position object")
    p.add_argument("--geometry", help="JSON geometry object")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_create_shape)

    # bulk-create
    p = sub.add_parser("bulk-create", help="Bulk create items (max 20)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--items", required=True, help="JSON array of item specs [{type, data, style, position, geometry}]")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_bulk_create)

    # get-item-connections
    p = sub.add_parser("get-item-connections", help="Get a single item with all its connections")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("item_id", help="Item ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get_item_connections)

    # --- Connectors ---

    # create-connector
    p = sub.add_parser("create-connector", help="Create a connector between two items")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("start_item_id", help="Start item ID")
    p.add_argument("end_item_id", help="End item ID")
    p.add_argument("--shape", choices=["straight", "curved", "elbowed"], help="Connector shape")
    p.add_argument("--stroke-color", help="Stroke color (hex, e.g. #ff0000)")
    p.add_argument("--stroke-width", type=float, help="Stroke width")
    p.add_argument("--caption", help="Connector label text")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_create_connector)

    # get-connectors
    p = sub.add_parser("get-connectors", help="Get all connectors on a board")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get_connectors)

    # update-connector
    p = sub.add_parser("update-connector", help="Update a connector")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("connector_id", help="Connector ID")
    p.add_argument("--shape", choices=["straight", "curved", "elbowed"], help="Connector shape")
    p.add_argument("--style", help="JSON style object")
    p.add_argument("--caption", help="Connector label text (empty string to remove)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_update_connector)

    # delete-connector
    p = sub.add_parser("delete-connector", help="Delete a connector")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("connector_id", help="Connector ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_delete_connector)

    # --- Frames ---

    # create-frame
    p = sub.add_parser("create-frame", help="Create a frame on a board")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--title", help="Frame title")
    p.add_argument("--position", help="JSON position object")
    p.add_argument("--geometry", help="JSON geometry object")
    p.add_argument("--style", help="JSON style object")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_create_frame)

    # get-frames
    p = sub.add_parser("get-frames", help="Get all frames on a board")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get_frames)

    # get-frame-items
    p = sub.add_parser("get-frame-items", help="Get all items inside a frame")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("frame_id", help="Frame ID")
    p.add_argument("--full", action="store_true", help="Return full item data")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_get_frame_items)

    # --- Bulk Operations ---

    # bulk-update
    p = sub.add_parser("bulk-update", help="Bulk update items (max 20)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--updates", required=True, help="JSON array of [{id, type, data?, style?, position?, geometry?}]")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_bulk_update)

    # bulk-delete
    p = sub.add_parser("bulk-delete", help="Bulk delete items (max 20)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--item-ids", required=True, help="JSON array of item IDs")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_bulk_delete)

    # bulk-create-connectors
    p = sub.add_parser("bulk-create-connectors", help="Bulk create connectors (max 20)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--connectors", required=True, help="JSON array of [{startItemId, endItemId, shape?, caption?}]")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_bulk_create_connectors)

    # --- Advanced ---

    # board-summary
    p = sub.add_parser("board-summary", help="Get board summary (counts by type and color)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_board_summary)

    # copy-board
    p = sub.add_parser("copy-board", help="Copy a board")
    p.add_argument("board_id", help="Board ID to copy")
    p.add_argument("--title", help="New board title")
    p.add_argument("--description", help="New board description")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_copy_board)

    # export-graph
    p = sub.add_parser("export-graph", help="Export board as directed graph (nodes + edges)")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--frame-id", help="Scope export to a specific frame")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_export_graph)

    # board-diff
    p = sub.add_parser("board-diff", help="Compare board to last snapshot — shows what changed")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--frame-id", help="Scope diff to a specific frame")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_board_diff)

    # import-graph
    p = sub.add_parser("import-graph", help="Import a graph with auto-layout")
    p.add_argument("board_id", help="Board ID")
    p.add_argument("--graph", required=True, help="JSON graph {nodes: [{id, label}], edges: [{from, to, label?}]}")
    p.add_argument("--title", help="Create a frame with this title")
    p.add_argument("--direction", choices=["LR", "TB", "RL", "BT"], default="LR", help="Layout direction (default: LR)")
    p.add_argument("--x", type=float, default=0, help="X offset for graph placement on canvas")
    p.add_argument("--y", type=float, default=0, help="Y offset for graph placement on canvas")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_import_graph)

    if "--schema" in sys.argv:
        print_schema(parser)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

"""
Miro REST API client.

Talks to the Miro v2 API via Bearer token auth. Provides async handler functions
for all 28 Miro board operations.
"""

import asyncio
import json
import logging
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger("miro-cli")

_token = None

MIRO_API = "https://api.miro.com/v2"

# Type-to-API-path mapping for updates
TYPE_TO_PATH = {
    "sticky_note": "sticky_notes",
    "shape": "shapes",
    "text": "texts",
    "card": "cards",
    "frame": "frames",
    "image": "images",
    "document": "documents",
    "embed": "embeds",
    "app_card": "app_cards",
}


def _load_config():
    """Load Miro token from config file."""
    global _token
    config_path = Path.home() / ".miro-cli" / "config.json"
    if not config_path.exists():
        raise FileNotFoundError("Miro not configured. Run: miro configure")
    mode = config_path.stat().st_mode
    if mode & (stat.S_IROTH | stat.S_IRGRP):
        logger.warning(
            "Config file %s has overly permissive permissions (%o). "
            "Run: chmod 600 %s", config_path, mode & 0o777, config_path
        )
    config = json.loads(config_path.read_text())
    _token = config["token"]


async def _api(path, method="GET", body=None):
    """Make authenticated request to Miro API v2."""
    if _token is None:
        _load_config()
    url = f"{MIRO_API}{path}"
    headers = {
        "Authorization": f"Bearer {_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, method=method, headers=headers)
    if body is not None:
        req.data = json.dumps(body).encode()

    loop = asyncio.get_running_loop()
    try:
        response = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=30)
        )
        if response.status == 204:
            return {"success": True}
        return json.loads(response.read())
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()
        except Exception as exc:
            logger.debug("Non-critical error: %s", exc)
        raise RuntimeError(f"Miro API error {e.code}: {error_body[:500]}")


async def _paginated_get(path, key="data", limit_per_page=50):
    """Fetch all pages of a paginated Miro endpoint."""
    all_items = []
    cursor = None
    while True:
        sep = "&" if "?" in path else "?"
        page_path = f"{path}{sep}limit={limit_per_page}"
        if cursor:
            page_path += f"&cursor={cursor}"
        result = await _api(page_path)
        items = result.get(key, [])
        all_items.extend(items)
        cursor = result.get("cursor")
        if not cursor or not items:
            break
    return all_items


# =====================================================================
# Helpers
# =====================================================================

def _strip_html(html):
    """Strip HTML tags and decode common entities."""
    if not html:
        return ""
    text = re.sub(r'<[^>]*>', '', html)
    for old, new in [
        ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
        ('&quot;', '"'), ('&#39;', "'"), ('&#43;', '+'), ('&#61;', '='),
    ]:
        text = text.replace(old, new)
    return text.strip()


def _compact_item(item):
    """Convert a full Miro item to compact format."""
    result = {"id": item["id"], "type": item["type"]}
    data = item.get("data", {})
    if data.get("content"):
        result["content"] = _strip_html(data["content"])
    if data.get("shape"):
        result["shape"] = data["shape"]
    if data.get("title"):
        result["title"] = data["title"]
    pos = item.get("position", {})
    if pos:
        result["x"] = round(pos.get("x", 0))
        result["y"] = round(pos.get("y", 0))
    geo = item.get("geometry", {})
    if geo.get("width"):
        result["w"] = round(geo["width"])
    if geo.get("height"):
        result["h"] = round(geo["height"])
    style = item.get("style", {})
    if item["type"] == "sticky_note" and style.get("fillColor"):
        result["color"] = style["fillColor"]
    elif style.get("fillColor"):
        result["fill"] = style["fillColor"]
    return result


def _get_item_label(item):
    """Extract a display label from a Miro item."""
    data = item.get("data", {})
    content = data.get("content", "")
    title = data.get("title", "")
    label = _strip_html(content) or title or ""
    return label[:200] if label else f"({item.get('type', 'unknown')})"


async def _fetch_connectors(board_id):
    """Fetch all connectors for a board."""
    return await _paginated_get(f"/boards/{board_id}/connectors")


def _build_connection_map(connectors, items_by_id):
    """Build a map of item_id -> list of connected items with content."""
    conn_map = defaultdict(list)
    for c in connectors:
        start_id = c.get("startItem", {}).get("id")
        end_id = c.get("endItem", {}).get("id")
        if start_id and end_id:
            # Add connection info to both endpoints
            end_item = items_by_id.get(end_id, {})
            start_item = items_by_id.get(start_id, {})
            conn_map[start_id].append({
                "connectorId": c["id"],
                "direction": "outgoing",
                "connectedItemId": end_id,
                "connectedContent": _strip_html(
                    end_item.get("data", {}).get("content", "")
                ) or end_item.get("data", {}).get("title", ""),
            })
            conn_map[end_id].append({
                "connectorId": c["id"],
                "direction": "incoming",
                "connectedItemId": start_id,
                "connectedContent": _strip_html(
                    start_item.get("data", {}).get("content", "")
                ) or start_item.get("data", {}).get("title", ""),
            })
    return conn_map


# =====================================================================
# HANDLERS
# =====================================================================

# 1. list-boards
async def handle_list_boards(params):
    """List all accessible Miro boards."""
    query = params.get("query")
    url = "/boards?limit=50"
    if query:
        url += f"&query={urllib.parse.quote(query)}"
    result = await _api(url)
    boards = result.get("data", [])
    return {
        "boards": [
            {
                "id": b["id"],
                "name": b.get("name", ""),
                "description": b.get("description", ""),
                "viewLink": b.get("viewLink", ""),
                "createdAt": b.get("createdAt", ""),
                "modifiedAt": b.get("modifiedAt", ""),
            }
            for b in boards
        ],
        "count": len(boards),
    }


async def handle_create_board(params):
    """Create a new Miro board."""
    name = params.get("name", "")
    if not name:
        return {"error": "name is required"}
    body = {"name": name}
    if params.get("description"):
        body["description"] = params["description"]
    result = await _api("/boards", method="POST", body=body)
    return {
        "board": {
            "id": result.get("id", ""),
            "name": result.get("name", ""),
            "description": result.get("description", ""),
            "viewLink": result.get("viewLink", ""),
        }
    }


async def handle_get_board(params):
    """Get details about a specific board."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}
    result = await _api(f"/boards/{board_id}")
    return {
        "board": {
            "id": result.get("id", ""),
            "name": result.get("name", ""),
            "description": result.get("description", ""),
            "viewLink": result.get("viewLink", ""),
            "createdAt": result.get("createdAt", ""),
            "modifiedAt": result.get("modifiedAt", ""),
        }
    }


async def handle_update_board(params):
    """Update a board's name or description."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}
    body = {}
    if params.get("name"):
        body["name"] = params["name"]
    if "description" in params:
        body["description"] = params["description"]
    if not body:
        return {"error": "Nothing to update — provide name or description"}
    result = await _api(f"/boards/{board_id}", method="PATCH", body=body)
    return {
        "board": {
            "id": result.get("id", ""),
            "name": result.get("name", ""),
            "description": result.get("description", ""),
            "viewLink": result.get("viewLink", ""),
        }
    }


async def handle_delete_board(params):
    """Delete a board (moves to trash, recoverable for 90 days)."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}
    await _api(f"/boards/{board_id}", method="DELETE")
    return {"deleted": board_id}


# 2. get-items (get_all_items)
async def handle_get_all_items(params):
    """Get all items on a board with optional filters and connections."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}

    type_filter = params.get("type")
    color_filter = params.get("color")
    search_filter = params.get("search", "").lower()
    full_mode = params.get("full", False)

    # Fetch all items
    items = await _paginated_get(f"/boards/{board_id}/items")

    # Build items-by-id for connection lookups
    items_by_id = {item["id"]: item for item in items}

    # Fetch connectors for connection map
    connectors = await _fetch_connectors(board_id)
    conn_map = _build_connection_map(connectors, items_by_id)

    # Apply filters
    filtered = items
    if type_filter:
        filtered = [i for i in filtered if i.get("type") == type_filter]
    if color_filter:
        filtered = [
            i for i in filtered
            if i.get("style", {}).get("fillColor") == color_filter
        ]
    if search_filter:
        def _matches_search(item):
            data = item.get("data", {})
            content = _strip_html(data.get("content", "")).lower()
            title = (data.get("title") or "").lower()
            return search_filter in content or search_filter in title
        filtered = [i for i in filtered if _matches_search(i)]

    # Format output
    if full_mode:
        result_items = filtered
    else:
        result_items = [_compact_item(i) for i in filtered]

    # Attach connections
    for item in result_items:
        item_id = item["id"]
        if item_id in conn_map:
            item["connections"] = conn_map[item_id]

    return {"items": result_items, "count": len(result_items)}


# 3. update-item
async def handle_update_item(params):
    """Update a board item (sticky, shape, text, etc.)."""
    board_id = params.get("board_id", "")
    item_id = params.get("item_id", "")
    item_type = params.get("item_type", "")

    if not board_id or not item_id or not item_type:
        return {"error": "board_id, item_id, and item_type are required"}

    api_path = TYPE_TO_PATH.get(item_type)
    if not api_path:
        return {"error": f"Unknown item type: {item_type}. Valid: {list(TYPE_TO_PATH.keys())}"}

    body = {}
    if params.get("data"):
        body["data"] = params["data"]
    if params.get("style"):
        body["style"] = params["style"]
    if params.get("position"):
        body["position"] = params["position"]
    if params.get("geometry"):
        body["geometry"] = params["geometry"]
    if params.get("parent"):
        body["parent"] = params["parent"]

    result = await _api(f"/boards/{board_id}/{api_path}/{item_id}", method="PATCH", body=body)
    return {"success": True, "item": result}


# 4. delete-item
async def handle_delete_item(params):
    """Delete an item from a board."""
    board_id = params.get("board_id", "")
    item_id = params.get("item_id", "")

    if not board_id or not item_id:
        return {"error": "board_id and item_id are required"}

    await _api(f"/boards/{board_id}/items/{item_id}", method="DELETE")
    return {"success": True, "deletedItemId": item_id}


# 5. move-item
async def handle_move_item(params):
    """Move an item to a new position and/or parent."""
    board_id = params.get("board_id", "")
    item_id = params.get("item_id", "")

    if not board_id or not item_id:
        return {"error": "board_id and item_id are required"}

    body = {}
    if params.get("position"):
        body["position"] = params["position"]
    if params.get("parent"):
        body["parent"] = params["parent"]

    result = await _api(f"/boards/{board_id}/items/{item_id}", method="PATCH", body=body)
    return {"success": True, "item": result}


# 6. create-sticky
async def handle_create_sticky_note(params):
    """Create a sticky note on a board."""
    board_id = params.get("board_id", "")
    content = params.get("content", "")

    if not board_id:
        return {"error": "board_id is required"}

    body = {"data": {"content": content}}
    if params.get("color"):
        body["style"] = {"fillColor": params["color"]}

    near_text = params.get("near")
    has_explicit_pos = params.get("x") is not None or params.get("y") is not None

    if has_explicit_pos:
        # Explicit coordinates provided — use them directly
        body["position"] = {
            "x": params.get("x", 0),
            "y": params.get("y", 0),
            "origin": "center",
        }
    else:
        # Smart positioning: scan board items
        try:
            all_items = await _paginated_get(f"/boards/{board_id}/items")
            # Also get frames for absolute positions
            frames = await _paginated_get(f"/boards/{board_id}/items?type=frame")
            frame_map = {}
            for f in frames:
                frame_map[f.get("id")] = f

            if near_text and all_items:
                # Search for items matching the --near text
                near_lower = near_text.lower()
                best_match = None
                best_score = -1
                for item in all_items:
                    item_content = (
                        item.get("data", {}).get("content", "")
                        or item.get("data", {}).get("title", "")
                        or ""
                    )
                    # Strip HTML tags for matching
                    plain = re.sub(r"<[^>]+>", "", item_content).lower()
                    if near_lower in plain:
                        # Score: prefer exact match > shorter content (more specific)
                        score = 100 if plain.strip() == near_lower else 50
                        score -= len(plain) / 100  # prefer shorter/more specific
                        if score > best_score:
                            best_score = score
                            best_match = item

                if best_match:
                    # Place sticky to the right of the matched item
                    pos = best_match.get("position", {})
                    geo = best_match.get("geometry", {})
                    ix = pos.get("x", 0)
                    iy = pos.get("y", 0)
                    iw = geo.get("width", 200)
                    ih = geo.get("height", 200)

                    # If item is inside a frame, convert relative -> absolute
                    parent_id = best_match.get("parent", {}).get("id") if isinstance(best_match.get("parent"), dict) else best_match.get("parent")
                    if parent_id and parent_id in frame_map:
                        fp = frame_map[parent_id].get("position", {})
                        fg = frame_map[parent_id].get("geometry", {})
                        # Frame children are relative to frame's top-left
                        frame_tl_x = fp.get("x", 0) - fg.get("width", 0) / 2
                        frame_tl_y = fp.get("y", 0) - fg.get("height", 0) / 2
                        ix = frame_tl_x + ix
                        iy = frame_tl_y + iy

                    # Target position: to the right of the matched item
                    target_x = ix + iw / 2 + 160
                    target_y = iy
                    STICKY_SIZE = 199  # default Miro sticky note size
                    STICKY_GAP = 20

                    # Check for ALL existing items near this target and offset down
                    for item in all_items:
                        sp = item.get("position", {})
                        sg = item.get("geometry", {})
                        sx = sp.get("x", 0)
                        sy = sp.get("y", 0)
                        sw = sg.get("width", STICKY_SIZE)
                        sh = sg.get("height", STICKY_SIZE)

                        # If item is inside a frame, convert to absolute
                        item_parent = item.get("parent", {})
                        ip_id = item_parent.get("id") if isinstance(item_parent, dict) else item_parent
                        if ip_id and ip_id in frame_map:
                            ifp = frame_map[ip_id].get("position", {})
                            ifg = frame_map[ip_id].get("geometry", {})
                            sx = (ifp.get("x", 0) - ifg.get("width", 0) / 2) + sx
                            sy = (ifp.get("y", 0) - ifg.get("height", 0) / 2) + sy

                        # If any item overlaps this spot, shift down below it
                        if (abs(sx - target_x) < (sw / 2 + STICKY_SIZE / 2)
                                and abs(sy - target_y) < (sh / 2 + STICKY_SIZE / 2)):
                            target_y = sy + sh / 2 + STICKY_SIZE / 2 + STICKY_GAP

                    body["position"] = {
                        "x": target_x,
                        "y": target_y,
                        "origin": "center",
                    }
                else:
                    # No match found — fall through to general positioning
                    near_text = None

            if not near_text and not has_explicit_pos:
                # No --near or no match: place to the right of all content
                if all_items:
                    max_right = float("-inf")
                    center_y = 0
                    count = 0
                    for item in frames or all_items:
                        pos = item.get("position", {})
                        geo = item.get("geometry", {})
                        ix = pos.get("x", 0)
                        iw = geo.get("width", 200)
                        right_edge = ix + iw / 2
                        if right_edge > max_right:
                            max_right = right_edge
                            center_y = pos.get("y", 0)
                        count += 1
                    body["position"] = {
                        "x": max_right + 200,
                        "y": center_y,
                        "origin": "center",
                    }
        except Exception as exc:
            logger.debug("Smart positioning failed, using default: %s", exc)

    result = await _api(f"/boards/{board_id}/sticky_notes", method="POST", body=body)
    return {"success": True, "item": result}


# 7. create-shape
async def handle_create_shape(params):
    """Create a shape on a board."""
    board_id = params.get("board_id", "")

    if not board_id:
        return {"error": "board_id is required"}

    body = {}
    data = {}
    if params.get("content"):
        data["content"] = params["content"]
    if params.get("shape"):
        data["shape"] = params["shape"]
    if data:
        body["data"] = data
    if params.get("style"):
        body["style"] = params["style"]
    if params.get("position"):
        body["position"] = params["position"]
    if params.get("geometry"):
        body["geometry"] = params["geometry"]

    result = await _api(f"/boards/{board_id}/shapes", method="POST", body=body)
    return {"success": True, "item": result}


# 8. bulk-create
async def handle_bulk_create_items(params):
    """Bulk create items on a board (max 20)."""
    board_id = params.get("board_id", "")
    items = params.get("items", [])

    if not board_id:
        return {"error": "board_id is required"}
    if not items:
        return {"error": "items array is required"}
    if len(items) > 20:
        return {"error": "Maximum 20 items per bulk create"}

    # Miro bulk API: POST /boards/{id}/items/bulk
    # Takes an array of item specifications
    created = []
    errors = []
    for i, item_spec in enumerate(items):
        try:
            item_type = item_spec.get("type", "sticky_note")
            api_path = TYPE_TO_PATH.get(item_type)
            if not api_path:
                errors.append({"index": i, "error": f"Unknown type: {item_type}"})
                continue

            body = {}
            if item_spec.get("data"):
                body["data"] = item_spec["data"]
            if item_spec.get("style"):
                body["style"] = item_spec["style"]
            if item_spec.get("position"):
                body["position"] = item_spec["position"]
            if item_spec.get("geometry"):
                body["geometry"] = item_spec["geometry"]
            if item_spec.get("parent"):
                body["parent"] = item_spec["parent"]

            result = await _api(f"/boards/{board_id}/{api_path}", method="POST", body=body)
            created.append({"index": i, "id": result.get("id"), "type": item_type})
        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    return {"created": created, "errors": errors, "count": len(created)}


# 9. get-item-connections
async def handle_get_item_with_connections(params):
    """Get a single item with all its connections and connected item content."""
    board_id = params.get("board_id", "")
    item_id = params.get("item_id", "")

    if not board_id or not item_id:
        return {"error": "board_id and item_id are required"}

    # Fetch the item
    item = await _api(f"/boards/{board_id}/items/{item_id}")

    # Fetch all connectors
    connectors = await _fetch_connectors(board_id)

    # Find connectors involving this item
    related_connectors = [
        c for c in connectors
        if c.get("startItem", {}).get("id") == item_id
        or c.get("endItem", {}).get("id") == item_id
    ]

    # Fetch connected items
    connected_item_ids = set()
    for c in related_connectors:
        start_id = c.get("startItem", {}).get("id")
        end_id = c.get("endItem", {}).get("id")
        if start_id and start_id != item_id:
            connected_item_ids.add(start_id)
        if end_id and end_id != item_id:
            connected_item_ids.add(end_id)

    connected_items = {}
    for cid in connected_item_ids:
        try:
            ci = await _api(f"/boards/{board_id}/items/{cid}")
            connected_items[cid] = ci
        except Exception as exc:
            logger.debug("Non-critical error: %s", exc)

    connections = []
    for c in related_connectors:
        start_id = c.get("startItem", {}).get("id")
        end_id = c.get("endItem", {}).get("id")
        other_id = end_id if start_id == item_id else start_id
        direction = "outgoing" if start_id == item_id else "incoming"
        other_item = connected_items.get(other_id, {})
        connections.append({
            "connectorId": c["id"],
            "direction": direction,
            "connectedItemId": other_id,
            "connectedContent": _strip_html(
                other_item.get("data", {}).get("content", "")
            ) or other_item.get("data", {}).get("title", ""),
            "connectedType": other_item.get("type", ""),
        })

    return {
        "item": _compact_item(item),
        "connections": connections,
        "connectionCount": len(connections),
    }


# 10. create-connector
async def handle_create_connector(params):
    """Create a connector between two items."""
    board_id = params.get("board_id", "")
    start_item_id = params.get("start_item_id", "")
    end_item_id = params.get("end_item_id", "")

    if not board_id or not start_item_id or not end_item_id:
        return {"error": "board_id, start_item_id, and end_item_id are required"}

    body = {
        "startItem": {"id": start_item_id},
        "endItem": {"id": end_item_id},
    }
    if params.get("shape"):
        body["shape"] = params["shape"]
    style = {}
    if params.get("stroke_color"):
        style["strokeColor"] = params["stroke_color"]
    if params.get("stroke_width"):
        style["strokeWidth"] = str(params["stroke_width"])
    if style:
        body["style"] = style
    captions = []
    if params.get("caption"):
        captions.append({"content": params["caption"]})
        body["captions"] = captions

    result = await _api(f"/boards/{board_id}/connectors", method="POST", body=body)
    return {"success": True, "connector": result}


# 11. get-connectors
async def handle_get_connectors(params):
    """Get all connectors on a board."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}

    connectors = await _fetch_connectors(board_id)
    return {"connectors": connectors, "count": len(connectors)}


# 12. update-connector
async def handle_update_connector(params):
    """Update a connector."""
    board_id = params.get("board_id", "")
    connector_id = params.get("connector_id", "")

    if not board_id or not connector_id:
        return {"error": "board_id and connector_id are required"}

    body = {}
    if params.get("shape"):
        body["shape"] = params["shape"]
    if params.get("style"):
        body["style"] = params["style"]
    if params.get("caption") is not None:
        body["captions"] = [{"content": params["caption"]}] if params["caption"] else []

    result = await _api(
        f"/boards/{board_id}/connectors/{connector_id}", method="PATCH", body=body
    )
    return {"success": True, "connector": result}


# 13. delete-connector
async def handle_delete_connector(params):
    """Delete a connector."""
    board_id = params.get("board_id", "")
    connector_id = params.get("connector_id", "")

    if not board_id or not connector_id:
        return {"error": "board_id and connector_id are required"}

    await _api(f"/boards/{board_id}/connectors/{connector_id}", method="DELETE")
    return {"success": True, "deletedConnectorId": connector_id}


# 14. create-frame
async def handle_create_frame(params):
    """Create a frame on a board."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}

    body = {"data": {}}
    if params.get("title"):
        body["data"]["title"] = params["title"]
    if params.get("position"):
        body["position"] = params["position"]
    if params.get("geometry"):
        body["geometry"] = params["geometry"]
    if params.get("style"):
        body["style"] = params["style"]

    result = await _api(f"/boards/{board_id}/frames", method="POST", body=body)
    return {"success": True, "frame": result}


# 15. get-frames
async def handle_get_frames(params):
    """Get all frames on a board."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}

    # Fetch items filtered by type=frame
    items = await _paginated_get(f"/boards/{board_id}/items?type=frame")
    frames = [_compact_item(i) for i in items]
    return {"frames": frames, "count": len(frames)}


# 16. get-frame-items
async def handle_get_items_in_frame(params):
    """Get all items inside a specific frame."""
    board_id = params.get("board_id", "")
    frame_id = params.get("frame_id", "")

    if not board_id or not frame_id:
        return {"error": "board_id and frame_id are required"}

    items = await _paginated_get(
        f"/boards/{board_id}/items?parent_item_id={frame_id}"
    )
    full_mode = params.get("full", False)
    result_items = items if full_mode else [_compact_item(i) for i in items]
    return {"items": result_items, "count": len(result_items), "frameId": frame_id}


# 17. bulk-update
async def handle_bulk_update_items(params):
    """Bulk update items (max 20). Iterates and PATCHes each."""
    board_id = params.get("board_id", "")
    updates = params.get("updates", [])

    if not board_id:
        return {"error": "board_id is required"}
    if not updates:
        return {"error": "updates array is required"}
    if len(updates) > 20:
        return {"error": "Maximum 20 items per bulk update"}

    updated = []
    errors = []
    for i, upd in enumerate(updates):
        try:
            item_id = upd.get("id")
            item_type = upd.get("type")
            if not item_id or not item_type:
                errors.append({"index": i, "error": "id and type are required"})
                continue

            api_path = TYPE_TO_PATH.get(item_type)
            if not api_path:
                errors.append({"index": i, "error": f"Unknown type: {item_type}"})
                continue

            body = {}
            if upd.get("data"):
                body["data"] = upd["data"]
            if upd.get("style"):
                body["style"] = upd["style"]
            if upd.get("position"):
                body["position"] = upd["position"]
            if upd.get("geometry"):
                body["geometry"] = upd["geometry"]
            if upd.get("parent"):
                body["parent"] = upd["parent"]

            await _api(f"/boards/{board_id}/{api_path}/{item_id}", method="PATCH", body=body)
            updated.append({"index": i, "id": item_id})
        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    return {"updated": updated, "errors": errors, "count": len(updated)}


# 18. bulk-delete
async def handle_bulk_delete_items(params):
    """Bulk delete items (max 20)."""
    board_id = params.get("board_id", "")
    item_ids = params.get("item_ids", [])

    if not board_id:
        return {"error": "board_id is required"}
    if not item_ids:
        return {"error": "item_ids array is required"}
    if len(item_ids) > 20:
        return {"error": "Maximum 20 items per bulk delete"}

    deleted = []
    errors = []
    for item_id in item_ids:
        try:
            await _api(f"/boards/{board_id}/items/{item_id}", method="DELETE")
            deleted.append(item_id)
        except Exception as e:
            errors.append({"id": item_id, "error": str(e)})

    return {"deleted": deleted, "errors": errors, "count": len(deleted)}


# 19. bulk-create-connectors
async def handle_bulk_create_connectors(params):
    """Bulk create connectors (max 20)."""
    board_id = params.get("board_id", "")
    connectors = params.get("connectors", [])

    if not board_id:
        return {"error": "board_id is required"}
    if not connectors:
        return {"error": "connectors array is required"}
    if len(connectors) > 20:
        return {"error": "Maximum 20 connectors per bulk create"}

    created = []
    errors = []
    for i, spec in enumerate(connectors):
        try:
            body = {
                "startItem": {"id": spec["startItemId"]},
                "endItem": {"id": spec["endItemId"]},
            }
            if spec.get("shape"):
                body["shape"] = spec["shape"]
            if spec.get("style"):
                body["style"] = spec["style"]
            if spec.get("caption"):
                body["captions"] = [{"content": spec["caption"]}]

            result = await _api(f"/boards/{board_id}/connectors", method="POST", body=body)
            created.append({"index": i, "id": result.get("id")})
        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    return {"created": created, "errors": errors, "count": len(created)}


# 20. board-summary
async def handle_board_summary(params):
    """Get a summary of board contents (counts by type and sticky color)."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}

    items = await _paginated_get(f"/boards/{board_id}/items")
    connectors = await _fetch_connectors(board_id)

    type_counts = defaultdict(int)
    sticky_colors = defaultdict(int)
    for item in items:
        item_type = item.get("type", "unknown")
        type_counts[item_type] += 1
        if item_type == "sticky_note":
            color = item.get("style", {}).get("fillColor", "unknown")
            sticky_colors[color] += 1

    return {
        "boardId": board_id,
        "totalItems": len(items),
        "totalConnectors": len(connectors),
        "itemsByType": dict(type_counts),
        "stickyNotesByColor": dict(sticky_colors),
    }


# 21. copy-board
async def handle_copy_board(params):
    """Copy a board."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}

    # Miro API v2: copy via POST /boards with copyFrom field
    body = {"copyFrom": board_id}
    if params.get("title"):
        body["name"] = params["title"]
    if params.get("description"):
        body["description"] = params["description"]

    result = await _api("/boards", method="POST", body=body)
    return {"board": result}


# 22. export-graph
async def handle_export_graph(params):
    """Export board as a clean directed graph (nodes + edges)."""
    board_id = params.get("board_id", "")
    frame_id = params.get("frame_id")

    if not board_id:
        return {"error": "board_id is required"}

    # Fetch items (optionally scoped to frame)
    if frame_id:
        items = await _paginated_get(
            f"/boards/{board_id}/items?parent_item_id={frame_id}"
        )
    else:
        items = await _paginated_get(f"/boards/{board_id}/items")

    # If scoped to frame, also fetch the frame for title
    board_title = ""
    if frame_id:
        try:
            frame_item = await _api(f"/boards/{board_id}/items/{frame_id}")
            board_title = frame_item.get("data", {}).get("title", "")
        except Exception as exc:
            logger.debug("Non-critical error: %s", exc)
    else:
        try:
            board_info = await _api(f"/boards/{board_id}")
            board_title = board_info.get("name", "")
        except Exception as exc:
            logger.debug("Non-critical error: %s", exc)

    # Build item ID set for scoping edges
    item_ids = {item["id"] for item in items}

    # Fetch all frames on board for group assignment
    all_items = items if not frame_id else await _paginated_get(f"/boards/{board_id}/items")
    frames_by_id = {}
    for item in all_items:
        if item.get("type") == "frame":
            frames_by_id[item["id"]] = item.get("data", {}).get("title", item["id"])

    # Build nodes (skip frames themselves)
    nodes = []
    for item in items:
        if item.get("type") == "frame":
            continue
        node = {
            "id": item["id"],
            "label": _get_item_label(item),
        }
        # Assign group from parent frame
        parent_id = item.get("parent", {}).get("id")
        if parent_id and parent_id in frames_by_id:
            node["group"] = frames_by_id[parent_id]
        nodes.append(node)

    # Fetch connectors and build edges
    connectors = await _fetch_connectors(board_id)
    edges = []
    for c in connectors:
        start_id = c.get("startItem", {}).get("id")
        end_id = c.get("endItem", {}).get("id")
        if start_id in item_ids and end_id in item_ids:
            edge = {"from": start_id, "to": end_id}
            captions = c.get("captions", [])
            if captions and captions[0].get("content"):
                edge["label"] = _strip_html(captions[0]["content"])
            edges.append(edge)

    return {
        "title": board_title,
        "nodes": nodes,
        "edges": edges,
    }


# 23. import-graph
async def handle_import_graph(params):
    """Import a graph (nodes + edges) onto a board with auto-layout."""
    board_id = params.get("board_id", "")
    nodes = params.get("nodes", [])
    edges = params.get("edges", [])
    title = params.get("title", "")
    direction = params.get("direction", "LR")  # LR, TB, RL, BT
    offset_x = params.get("offset_x", None)
    offset_y = params.get("offset_y", None)

    if not board_id:
        return {"error": "board_id is required"}
    if not nodes:
        return {"error": "nodes array is required"}

    # Cycle-aware hierarchical rank assignment
    # Build adjacency list
    node_ids = {n["id"] for n in nodes}
    adj = defaultdict(list)
    for e in edges:
        if e["from"] in node_ids and e["to"] in node_ids:
            adj[e["from"]].append(e["to"])

    # Step 1: Detect back-edges using DFS to break cycles
    _visited = set()
    _on_stack = set()
    back_edges = set()

    # Iterative DFS to avoid RecursionError on large graphs
    for n in nodes:
        start = n["id"]
        if start in _visited:
            continue
        stack = [(start, iter(adj.get(start, [])))]
        _visited.add(start)
        _on_stack.add(start)
        while stack:
            nid, neighbors = stack[-1]
            try:
                neighbor = next(neighbors)
                if neighbor not in node_ids:
                    continue
                if neighbor in _on_stack:
                    back_edges.add((nid, neighbor))
                elif neighbor not in _visited:
                    _visited.add(neighbor)
                    _on_stack.add(neighbor)
                    stack.append((neighbor, iter(adj.get(neighbor, []))))
            except StopIteration:
                _on_stack.discard(nid)
                stack.pop()

    # Step 2: Build DAG adjacency (original edges minus back-edges)
    dag_adj = defaultdict(list)
    dag_in_degree = defaultdict(int)
    for n in nodes:
        dag_in_degree[n["id"]] = 0
    for e in edges:
        if e["from"] in node_ids and e["to"] in node_ids:
            if (e["from"], e["to"]) not in back_edges:
                dag_adj[e["from"]].append(e["to"])
                dag_in_degree[e["to"]] += 1

    # Step 3: Topological sort on DAG (Kahn's algorithm)
    queue = [nid for nid in node_ids if dag_in_degree.get(nid, 0) == 0]
    topo_order = []
    _visited_dag = set()
    while queue:
        next_queue = []
        for nid in queue:
            if nid in _visited_dag:
                continue
            _visited_dag.add(nid)
            topo_order.append(nid)
            for neighbor in dag_adj.get(nid, []):
                dag_in_degree[neighbor] -= 1
                if dag_in_degree[neighbor] <= 0 and neighbor not in _visited_dag:
                    next_queue.append(neighbor)
        queue = next_queue

    # Append any remaining nodes (shouldn't happen after cycle breaking)
    for n in nodes:
        if n["id"] not in _visited_dag:
            topo_order.append(n["id"])

    # Step 4: Longest-path rank assignment (balanced hierarchical layout)
    ranks = {nid: 0 for nid in node_ids}
    for nid in topo_order:
        for neighbor in dag_adj.get(nid, []):
            ranks[neighbor] = max(ranks[neighbor], ranks[nid] + 1)

    rank_members = defaultdict(list)
    for nid in node_ids:
        rank_members[ranks[nid]].append(nid)

    # --- Node sizing constants ---
    CHAR_WIDTH = 8        # approx pixels per character at fontSize 14
    LINE_HEIGHT = 20      # pixels per line at fontSize 14
    PADDING_H = 40        # horizontal padding inside shape
    PADDING_V = 30        # vertical padding inside shape (total top + bottom)
    SHAPE_MIN_W = 200
    SHAPE_MAX_W = 500
    SHAPE_MIN_H = 60
    RANK_GAP = 100        # whitespace between ranks (edge-to-edge)
    NODE_GAP = 40         # whitespace between nodes in same rank (edge-to-edge)
    FRAME_PADDING = 80

    # Pre-calculate per-node widths AND heights based on label content
    node_widths = {}
    node_heights = {}
    for n in nodes:
        label = n.get("label", n["id"])
        lines = label.split("\n")
        longest_line_len = max((len(line) for line in lines), default=1)

        # Width from longest line
        w = longest_line_len * CHAR_WIDTH + PADDING_H
        w = max(SHAPE_MIN_W, min(w, SHAPE_MAX_W))

        # Height from line count (account for line wrapping within width)
        usable_chars = max(1, int((w - PADDING_H) / CHAR_WIDTH))
        total_visual_lines = 0
        for line in lines:
            total_visual_lines += max(1, -(-max(1, len(line)) // usable_chars))
        h = total_visual_lines * LINE_HEIGHT + PADDING_V
        h = max(SHAPE_MIN_H, h)

        node_widths[n["id"]] = w
        node_heights[n["id"]] = h

    # Barycenter ordering to reduce edge crossings
    rev_adj = defaultdict(list)
    for e in edges:
        if e["from"] in node_ids and e["to"] in node_ids:
            rev_adj[e["to"]].append(e["from"])
    for rank in sorted(rank_members.keys()):
        if rank == 0:
            continue
        members = rank_members[rank]
        def _barycenter(nid, _rev=rev_adj, _ranks=ranks, _rm=rank_members):
            parents = [p for p in _rev.get(nid, []) if _ranks.get(p, -1) < _ranks.get(nid, 0)]
            if not parents:
                return float("inf")
            indices = []
            for p in parents:
                pr = _ranks[p]
                try:
                    indices.append(_rm[pr].index(p))
                except ValueError:
                    pass
            return sum(indices) / len(indices) if indices else float("inf")
        rank_members[rank] = sorted(members, key=_barycenter)

    # Compute layout positions using actual node dimensions + gaps
    node_positions = {}
    if direction in ("TB", "BT"):
        # Ranks stack vertically, nodes within a rank spread horizontally
        y_cursor = 0.0
        for rank in sorted(rank_members.keys()):
            members = rank_members[rank]
            max_h = max(node_heights.get(nid, SHAPE_MIN_H) for nid in members)
            total_w = sum(node_widths.get(nid, SHAPE_MIN_W) for nid in members)
            total_w += NODE_GAP * max(0, len(members) - 1)
            x_cursor = -total_w / 2.0
            for nid in members:
                w = node_widths.get(nid, SHAPE_MIN_W)
                cx = x_cursor + w / 2.0
                cy = y_cursor if direction == "TB" else -y_cursor
                node_positions[nid] = (cx, cy)
                x_cursor += w + NODE_GAP
            y_cursor += max_h + RANK_GAP
    else:
        # LR/RL: Ranks spread horizontally, nodes within a rank stack vertically
        x_cursor = 0.0
        for rank in sorted(rank_members.keys()):
            members = rank_members[rank]
            max_w = max(node_widths.get(nid, SHAPE_MIN_W) for nid in members)
            total_h = sum(node_heights.get(nid, SHAPE_MIN_H) for nid in members)
            total_h += NODE_GAP * max(0, len(members) - 1)
            y_cursor_inner = -total_h / 2.0
            for nid in members:
                h = node_heights.get(nid, SHAPE_MIN_H)
                cx = x_cursor if direction == "LR" else -x_cursor
                cy = y_cursor_inner + h / 2.0
                node_positions[nid] = (cx, cy)
                y_cursor_inner += h + NODE_GAP
            x_cursor += max_w + RANK_GAP

    # Calculate bounding box from actual node positions and sizes
    _bbox_xs = []
    _bbox_ys = []
    for nid, (px, py) in node_positions.items():
        hw = node_widths.get(nid, SHAPE_MIN_W) / 2.0
        hh = node_heights.get(nid, SHAPE_MIN_H) / 2.0
        _bbox_xs.extend([px - hw, px + hw])
        _bbox_ys.extend([py - hh, py + hh])
    graph_min_x = min(_bbox_xs)
    graph_max_x = max(_bbox_xs)
    graph_min_y = min(_bbox_ys)
    graph_max_y = max(_bbox_ys)
    graph_width = graph_max_x - graph_min_x + FRAME_PADDING * 2
    graph_height = graph_max_y - graph_min_y + FRAME_PADDING * 2

    # Auto-position: if no explicit offset, scan board and place below existing content
    if offset_x is None and offset_y is None:
        try:
            # Scan frames first (reliable absolute positions), then top-level items
            existing_frames = await _paginated_get(f"/boards/{board_id}/items?type=frame")
            existing_items = await _paginated_get(f"/boards/{board_id}/items")
            max_bottom = float("-inf")
            # Frames have absolute center positions — most reliable for stacking
            for item in existing_frames:
                pos = item.get("position", {})
                geo = item.get("geometry", {})
                iy = pos.get("y", 0)
                ih = geo.get("height", 100)
                bottom = iy + ih / 2
                if bottom > max_bottom:
                    max_bottom = bottom
            # Also check top-level items (those without a parent frame)
            for item in existing_items:
                if item.get("parent"):
                    continue  # skip frame children (relative positions)
                pos = item.get("position", {})
                geo = item.get("geometry", {})
                iy = pos.get("y", 0)
                ih = geo.get("height", 100)
                bottom = iy + ih / 2
                if bottom > max_bottom:
                    max_bottom = bottom
            if max_bottom > float("-inf"):
                # Place this graph below existing content with 200px gap
                offset_x = 0
                offset_y = max_bottom + 200 - graph_min_y + FRAME_PADDING
            else:
                offset_x = 0
                offset_y = 0
        except Exception as exc:
            logger.debug("Auto-position scan failed: %s", exc)
            offset_x = 0
            offset_y = 0
    else:
        offset_x = offset_x or 0
        offset_y = offset_y or 0

    # Apply offset to all positions
    node_positions = {nid: (x + offset_x, y + offset_y) for nid, (x, y) in node_positions.items()}

    # Create frame if title provided
    frame_id = None
    frame_tl_x = 0.0
    frame_tl_y = 0.0
    if title:
        # Calculate bounds from actual node edges
        f_min_x, f_max_x = float("inf"), float("-inf")
        f_min_y, f_max_y = float("inf"), float("-inf")
        for nid, (px, py) in node_positions.items():
            hw = node_widths.get(nid, SHAPE_MIN_W) / 2.0
            hh = node_heights.get(nid, SHAPE_MIN_H) / 2.0
            f_min_x = min(f_min_x, px - hw)
            f_max_x = max(f_max_x, px + hw)
            f_min_y = min(f_min_y, py - hh)
            f_max_y = max(f_max_y, py + hh)
        min_x = f_min_x - FRAME_PADDING
        max_x = f_max_x + FRAME_PADDING
        min_y = f_min_y - FRAME_PADDING
        max_y = f_max_y + FRAME_PADDING

        frame_tl_x = min_x
        frame_tl_y = min_y

        frame_body = {
            "data": {"title": title},
            "position": {
                "x": (min_x + max_x) / 2,
                "y": (min_y + max_y) / 2,
                "origin": "center",
            },
            "geometry": {
                "width": max_x - min_x,
                "height": max_y - min_y,
            },
        }
        frame_result = await _api(f"/boards/{board_id}/frames", method="POST", body=frame_body)
        frame_id = frame_result.get("id")

    # Create shapes for each node
    id_mapping = {}  # user_id -> miro_id

    for n in nodes:
        nid = n["id"]
        x, y = node_positions.get(nid, (0, 0))
        w = node_widths.get(nid, SHAPE_MIN_W)

        # Miro positions children relative to parent's TOP-LEFT corner
        if frame_id:
            x = x - frame_tl_x
            y = y - frame_tl_y

        h = node_heights.get(nid, SHAPE_MIN_H)
        body = {
            "data": {
                "content": n.get("label", nid),
                "shape": "round_rectangle",
            },
            "style": {
                "fillColor": "#ffffff",
                "borderColor": "#1a1a1a",
                "borderWidth": "2.0",
                "borderOpacity": "1.0",
                "textAlign": "left",
                "textAlignVertical": "top",
            },
            "position": {"x": x, "y": y, "origin": "center"},
            "geometry": {"width": w, "height": h},
        }
        if frame_id:
            body["parent"] = {"id": frame_id}

        result = await _api(f"/boards/{board_id}/shapes", method="POST", body=body)
        id_mapping[nid] = result.get("id")

    # Create connectors for edges
    connector_ids = []
    for e in edges:
        from_miro = id_mapping.get(e["from"])
        to_miro = id_mapping.get(e["to"])
        if from_miro and to_miro:
            body = {
                "startItem": {"id": from_miro},
                "endItem": {"id": to_miro},
                "shape": "curved",
            }
            if e.get("label"):
                body["captions"] = [{"content": e["label"]}]
            try:
                result = await _api(f"/boards/{board_id}/connectors", method="POST", body=body)
                connector_ids.append(result.get("id"))
            except Exception as ex:
                logger.warning(f"Failed to create connector: {ex}")

    # Calculate final bounding box of placed content
    fb_xs, fb_ys = [], []
    for nid, (px, py) in node_positions.items():
        hw = node_widths.get(nid, SHAPE_MIN_W) / 2.0
        hh = node_heights.get(nid, SHAPE_MIN_H) / 2.0
        fb_xs.extend([px - hw, px + hw])
        fb_ys.extend([py - hh, py + hh])
    bbox = {
        "x": min(fb_xs) - FRAME_PADDING,
        "y": min(fb_ys) - FRAME_PADDING,
        "width": graph_width,
        "height": graph_height,
        "bottom": max(fb_ys) + FRAME_PADDING,
    }

    return {
        "success": True,
        "idMapping": id_mapping,
        "frameId": frame_id,
        "nodesCreated": len(id_mapping),
        "connectorsCreated": len(connector_ids),
        "bounds": bbox,
    }


async def handle_board_diff(params):
    """Compare current board state to last snapshot. Shows changes since last diff."""
    board_id = params.get("board_id", "")
    if not board_id:
        return {"error": "board_id is required"}
    frame_id = params.get("frame_id")

    # 1. Fetch current items and connectors
    if frame_id:
        items = await _paginated_get(f"/boards/{board_id}/items?parent_item_id={frame_id}")
    else:
        items = await _paginated_get(f"/boards/{board_id}/items")
    connectors = await _paginated_get(f"/boards/{board_id}/connectors")

    # Build current state
    current_nodes = {}
    for item in items:
        if item.get("type") == "frame":
            continue
        label = _strip_html(item.get("data", {}).get("content", "")) or item.get("data", {}).get("title", "") or item.get("type", "")
        node = {
            "label": label,
            "type": item.get("type", ""),
            "x": round(item.get("position", {}).get("x", 0)),
            "y": round(item.get("position", {}).get("y", 0)),
        }
        fill = item.get("style", {}).get("fillColor")
        if fill:
            node["color"] = fill
        parent = item.get("parent", {}).get("id")
        if parent:
            node["parentId"] = parent
        current_nodes[item["id"]] = node

    node_ids = set(current_nodes.keys())
    current_edges = {}
    for c in connectors:
        start_id = (c.get("startItem") or {}).get("id")
        end_id = (c.get("endItem") or {}).get("id")
        if start_id and end_id and start_id in node_ids and end_id in node_ids:
            key = f"{start_id}->{end_id}"
            captions = c.get("captions") or []
            caption = _strip_html(captions[0].get("content", "")) if captions else ""
            current_edges[key] = {
                "from": start_id,
                "to": end_id,
                "label": caption,
                "connectorId": c.get("id", ""),
            }

    current = {"nodes": current_nodes, "edges": current_edges}

    # 2. Load previous snapshot
    safe_id = re.sub(r"[^a-zA-Z0-9_=-]", "_", board_id)
    snapshot_dir = Path.home() / ".miro-cli" / "snapshots"
    snapshot_file = snapshot_dir / f"{safe_id}.json"
    previous = None
    try:
        if snapshot_file.exists():
            previous = json.loads(snapshot_file.read_text())
    except Exception:
        pass

    # 3. Save current as new snapshot
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshot_file.write_text(json.dumps(current, indent=2))
        snapshot_file.chmod(0o600)
    except Exception:
        pass

    # 4. Compute diff
    if previous is None:
        type_breakdown = defaultdict(int)
        for n in current_nodes.values():
            type_breakdown[n["type"]] += 1
        return {
            "firstSnapshot": True,
            "message": "No previous snapshot — saved current state. Run again after changes to see diff.",
            "currentState": {
                "nodes": len(current_nodes),
                "edges": len(current_edges),
                "byType": dict(type_breakdown),
            },
        }

    prev_nodes = previous.get("nodes", {})
    prev_edges = previous.get("edges", {})
    prev_ids = set(prev_nodes.keys())
    curr_ids = set(current_nodes.keys())

    # New nodes
    added_nodes = []
    for nid in curr_ids - prev_ids:
        added_nodes.append({"id": nid, **current_nodes[nid]})

    # Removed nodes
    removed_nodes = []
    for nid in prev_ids - curr_ids:
        removed_nodes.append({"id": nid, **prev_nodes[nid]})

    # Changed nodes (label or color)
    changed_nodes = []
    for nid in curr_ids & prev_ids:
        prev = prev_nodes[nid]
        curr = current_nodes[nid]
        change = {}
        if prev.get("label") != curr.get("label"):
            change["labelBefore"] = prev.get("label", "")
            change["labelAfter"] = curr.get("label", "")
        if prev.get("color") != curr.get("color"):
            change["colorBefore"] = prev.get("color", "")
            change["colorAfter"] = curr.get("color", "")
        if change:
            change["id"] = nid
            change["type"] = curr.get("type", "")
            changed_nodes.append(change)

    # Moved nodes (>50px threshold)
    moved_nodes = []
    for nid in curr_ids & prev_ids:
        prev = prev_nodes[nid]
        curr = current_nodes[nid]
        dx = abs(prev.get("x", 0) - curr.get("x", 0))
        dy = abs(prev.get("y", 0) - curr.get("y", 0))
        if dx > 50 or dy > 50:
            dist = round((dx**2 + dy**2) ** 0.5)
            moved_nodes.append({
                "id": nid,
                "label": curr.get("label", ""),
                "from": {"x": prev.get("x", 0), "y": prev.get("y", 0)},
                "to": {"x": curr.get("x", 0), "y": curr.get("y", 0)},
                "distance": dist,
            })

    # New edges
    prev_edge_keys = set(prev_edges.keys())
    curr_edge_keys = set(current_edges.keys())
    added_edges = []
    for key in curr_edge_keys - prev_edge_keys:
        e = current_edges[key]
        from_label = current_nodes.get(e["from"], {}).get("label", e["from"])
        to_label = current_nodes.get(e["to"], {}).get("label", e["to"])
        added_edges.append({"from": from_label, "to": to_label, "label": e.get("label", "")})

    # Removed edges
    removed_edges = []
    for key in prev_edge_keys - curr_edge_keys:
        e = prev_edges[key]
        from_label = prev_nodes.get(e["from"], {}).get("label", e["from"])
        to_label = prev_nodes.get(e["to"], {}).get("label", e["to"])
        removed_edges.append({"from": from_label, "to": to_label, "label": e.get("label", "")})

    has_changes = bool(added_nodes or removed_nodes or changed_nodes or moved_nodes or added_edges or removed_edges)

    diff = {
        "hasChanges": has_changes,
        "summary": {
            "nodesAdded": len(added_nodes),
            "nodesRemoved": len(removed_nodes),
            "nodesChanged": len(changed_nodes),
            "nodesMoved": len(moved_nodes),
            "edgesAdded": len(added_edges),
            "edgesRemoved": len(removed_edges),
        },
    }
    if added_nodes:
        diff["addedNodes"] = added_nodes
    if removed_nodes:
        diff["removedNodes"] = removed_nodes
    if changed_nodes:
        diff["changedNodes"] = changed_nodes
    if moved_nodes:
        diff["movedNodes"] = moved_nodes
    if added_edges:
        diff["addedEdges"] = added_edges
    if removed_edges:
        diff["removedEdges"] = removed_edges

    return diff

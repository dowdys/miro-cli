# Miro CLI

Command-line tool for Miro board automation. 28 commands for boards, items, connectors, frames, and graph import/export with auto-layout.

No daemon or external dependencies — just Python 3.9+ and a Miro API token.

## Install

```bash
pip install .
```

Or install directly from the directory:

```bash
pip install /path/to/miro-cli
```

## Setup

Get a Miro API token from https://miro.com/app/settings/user-profile/apps, then:

```bash
miro configure
```

## Usage

```bash
# List your boards
miro list-boards

# Create a board
miro create-board "My Project"

# Add a sticky note near existing content
miro create-sticky BOARD_ID "TODO: fix this" --color yellow --near "login flow"

# Import a graph with auto-layout
miro import-graph BOARD_ID --graph '{"nodes": [{"id": "a", "label": "Start"}, {"id": "b", "label": "End"}], "edges": [{"from": "a", "to": "b"}]}' --title "My Flow" --direction TB

# Get all items
miro get-items BOARD_ID

# Full JSON schema of all commands
miro --schema
```

## Commands

### Boards
- `list-boards` — List all boards
- `create-board` — Create a board
- `get-board` — Get board details
- `update-board` — Update name/description
- `delete-board` — Delete (recoverable 90 days)
- `copy-board` — Duplicate a board

### Items
- `get-items` — List items (with search, type, color filters)
- `create-sticky` — Create sticky note (`--near` for smart placement)
- `create-shape` — Create shape
- `update-item` — Update any item
- `delete-item` — Delete an item
- `move-item` — Move an item
- `bulk-create` — Create up to 20 items at once
- `bulk-update` — Update up to 20 items at once
- `bulk-delete` — Delete up to 20 items at once

### Connectors
- `create-connector` — Connect two items
- `get-connectors` — List connectors
- `update-connector` — Update connector style/caption
- `delete-connector` — Delete connector
- `bulk-create-connectors` — Create up to 20 connectors
- `get-item-connections` — Get an item with all its connections

### Frames
- `create-frame` — Create a frame
- `get-frames` — List frames
- `get-frame-items` — List items inside a frame

### Advanced
- `board-summary` — Item counts by type and color
- `export-graph` — Export board as directed graph JSON
- `import-graph` — Import graph with auto-layout (TB/LR/RL/BT)
- `board-diff` — Detect changes since last snapshot

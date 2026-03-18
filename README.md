# Arc to Zen Browser Migration Tool

A complete Python-based migration tool that converts Arc browser spaces and pinned tabs into Zen browser workspaces with proper pinned tab assignment.

## Quick Start

### Prerequisites

- **Python 3.7+**
- **lz4** Python package (`pip3 install lz4`)
- **Arc Browser** (with spaces and pinned tabs you want to migrate)
- **Zen Browser** (installed and run at least once)
- **macOS** or **Windows** (current implementation)

### Installation

1. Clone this repository:
```bash
git clone https://github.com/rafcabezas/arc2zen.git
cd arc2zen
```

2. Install the lz4 dependency (required for reading/writing Zen session files):
```bash
pip3 install lz4
```

### Basic Usage

**Close both Arc and Zen browsers before running.**

```bash
# Dry run first to see what will be migrated
python3 migrate_arc_to_zen.py --dry-run

# If everything looks good, run the actual migration
python3 migrate_arc_to_zen.py

# Migrate only a specific Arc space (useful for testing or selective migration)
python3 migrate_arc_to_zen.py --arc-space "Personal" --dry-run
```

### Advanced Usage

```bash
# Verbose logging for debugging
python3 migrate_arc_to_zen.py --verbose

# Target a specific Zen profile
python3 migrate_arc_to_zen.py --zen-profile "Default"

# Reset Zen profile to clean state (backs up first, then removes migration artifacts)
python3 migrate_arc_to_zen.py --reset

# See all available options
python3 migrate_arc_to_zen.py --help
```

## What Gets Migrated

- **Arc Spaces** → **Zen Workspaces** (each Arc space becomes a Zen workspace)
- **Space Icons** → **Workspace Icons** (Unicode emojis preserved)
- **Space Colors** → **Workspace Themes** (Arc's color tints reproduced)
- **Pinned Tabs** → **Zen Pinned Tabs** (upper sidebar, with folder structure)
- **Session Tabs** → **Zen Open Tabs** (lower pane, per workspace)
- **Folder Hierarchy** → **Zen Folder Structure** (nested folders maintained)
- **Display Order** → **Zen Sidebar Order** (Arc visual ordering preserved)

## How It Works

### Step 1: Extract Arc Data
Reads `StorableSidebar.json` to extract spaces, pinned tabs, session tabs,
folders, icons, and colors. Handles both Firebase-synced and local-only Arc
installations.

### Step 2: Create Zen Containers
Creates Multi-Account Containers in `containers.json` — one per Arc space —
so each workspace can have isolated login sessions.

### Step 3: Create Zen Workspaces
Writes workspace definitions to `prefs.js` under `zen.workspaces.data` with
names, icons, themes, and container assignments.

### Step 4: Import Tabs into Session Store
Writes pinned tabs (with folder/group structure) and session tabs directly to
Zen's session files (`zen-sessions.jsonlz4` and `sessionstore.jsonlz4`).
These are what Zen reads on startup to restore your sidebar.

## Safety Features

- **Read-only Arc access** — Your Arc data is never modified
- **Automatic backups** — Zen database and session files backed up before changes
- **Dry-run mode** — Test migration without making changes (`--dry-run`)
- **Profile reset** — Clean up migration artifacts and start over (`--reset`)
- **Duplicate detection** — Re-running skips already-imported tabs

## Command Line Options

| Flag | Description |
|---|---|
| `--dry-run` | Test migration without making changes |
| `--arc-space NAME` | Migrate only a specific Arc space (case-insensitive partial match) |
| `--zen-profile NAME` | Target a specific Zen profile |
| `--verbose` | Enable detailed debug logging |
| `--reset` | Reset Zen profile to clean state (backs up first) |
| `--help` | Show all available options |

## File Structure

```
arc2zen/
├── migrate_arc_to_zen.py            # Main migration script / CLI entry point
├── src/
│   ├── arc_pinned_tab_extractor.py  # Extract Arc pinned + session tabs
│   ├── zen_session_importer.py      # Write tabs to Zen session files (jsonlz4)
│   ├── zen_workspace_importer.py    # Create workspaces in prefs.js
│   ├── zen_space_importer.py        # Create containers in containers.json
│   ├── zen_pinned_tab_importer.py   # Bookmark-level workspace assignment
│   ├── zen_bookmark_importer.py     # Fallback bookmark import
│   ├── zen_schema_analyzer.py       # Zen profile discovery
│   └── zen_workspace_mapper.py      # Arc space → Zen workspace mapping
├── CLAUDE.md                        # AI assistant context for development
├── README.md                        # This file
└── LICENSE                          # MIT License
```

## Technical Details

### Arc Browser Data
- **Location (macOS)**: `~/Library/Application Support/Arc/StorableSidebar.json`
- **Location (Windows)**: `%LOCALAPPDATA%/Packages/TheBrowserCompany.Arc_.../LocalCache/Local/Arc/StorableSidebar.json`
- Space metadata may be in `firebaseSyncState.syncData.spaceModels` or
  `sidebar.containers[1].spaces` (fallback when Firebase sync is disabled)
- Each space has `containerIDs = ['pinned', '<uuid-A>', 'unpinned', '<uuid-B>']`
  where uuid-A holds pinned tab children and uuid-B holds session tabs

### Zen Browser Data (Zen >= ~1.6)
- **Location (macOS)**: `~/Library/Application Support/zen/Profiles/<profile>/`
- **Session files**: `zen-sessions.jsonlz4` (authoritative), `sessionstore.jsonlz4`
  (Firefox compat) — mozLz4 compressed JSON with 8-byte magic header
- **Workspaces**: `prefs.js` → `zen.workspaces.data` JSON array
- **Containers**: `containers.json` — Multi-Account Containers
- **Bookmarks**: `places.sqlite` → `moz_bookmarks` + `zen_bookmarks_workspaces`

### Removed Tables (Zen >= ~1.6)
The following sqlite tables were removed in recent Zen versions and are **not**
used by the current migration:
- `zen_pins` / `zen_pins_changes`
- `zen_workspaces` / `zen_workspaces_changes`

## Troubleshooting

### "Zen profile not found"
- Make sure Zen browser has been run at least once
- Check that the profile directory exists

### "No Arc data found"
- Verify Arc browser is installed and has spaces with pinned tabs

### Zen shows blank workspace after migration
- Zen creates a default "Space" workspace on first launch — switch to your
  migrated workspaces using the sidebar icons

### Session restore errors
- Check `<profile>/sessionstore-logs/error-sessionrestore-*.txt`
- Usually caused by corrupt lz4 compression (must use `store_size=True`)

### Data Recovery
- `--reset` backs up the full profile before cleaning
- Manual backups: `zen_database_backup_*.sqlite` files in the working directory
- Your Arc data is never modified (read-only access)

## Limitations

- Arc-specific features (Boosts, Easels) don't have Zen equivalents
- Custom folder icons/colors are not preserved (Zen uses its own styling)
- Chrome extensions must be replaced with Firefox equivalents manually
- Zen may drop some tabs on first launch if it encounters unexpected session data

## Contributing

Contributions are welcome! Always test with `--dry-run --verbose` first.

1. Fork the repository
2. Create a feature branch
3. Test thoroughly (including a full reset + migrate cycle)
4. Submit a pull request

## License

MIT License — See LICENSE file for details.

**Important**: Always backup your data before running migrations. Use `--dry-run` first.

## Acknowledgments

- Arc Browser team for creating an innovative browser
- Zen Browser team for building a privacy-focused alternative
- The open source community for inspiration and tools

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that migrates Arc browser spaces and pinned tabs into Zen
browser. Reads Arc's `StorableSidebar.json`, writes into Zen's session files,
profile database, and prefs.js. Supports macOS and Windows.

## Common Commands

```bash
# Safe testing (always do this first)
python3 migrate_arc_to_zen.py --dry-run --verbose

# Single space test
python3 migrate_arc_to_zen.py --arc-space "Work" --dry-run

# Full migration (close both browsers first)
python3 migrate_arc_to_zen.py

# Test Arc data extraction standalone
python3 src/arc_pinned_tab_extractor.py
```

Always close Zen browser before running — session files will be overwritten.

## Dependencies

Python 3.7+ with `lz4` (`pip3 install lz4`) for Zen's mozLz4 session files.
All other imports are stdlib.

## Architecture

### Migration Pipeline

1. **Extract Arc data** — `arc_pinned_tab_extractor.py`
2. **Create Zen containers** — `zen_space_importer.py` writes `containers.json`
3. **Create Zen workspaces** — `zen_workspace_importer.py` writes `prefs.js`
4. **Import tabs into session store** — `zen_session_importer.py` writes
   `zen-sessions.jsonlz4`, `sessionstore.jsonlz4`, and backup files

Step 3 must happen before step 4 so workspace UUIDs are available.

### Key Components

| File | Purpose |
|---|---|
| `migrate_arc_to_zen.py` | Main orchestrator / CLI entry point |
| `src/arc_pinned_tab_extractor.py` | Reads Arc's StorableSidebar.json |
| `src/zen_session_importer.py` | Writes pinned + session tabs to Zen session files |
| `src/zen_workspace_importer.py` | Creates workspaces in prefs.js |
| `src/zen_space_importer.py` | Creates containers in containers.json |
| `src/zen_pinned_tab_importer.py` | Writes to moz_bookmarks + zen_bookmarks_workspaces |
| `src/zen_bookmark_importer.py` | Fallback bookmark import |
| `src/zen_schema_analyzer.py` | Finds Zen profile paths |

### Arc Data Structure

```
StorableSidebar.json
├── firebaseSyncState.syncData.spaceModels  (space metadata — may be empty)
├── sidebar.containers[1].spaces            (space names, icons, containerIDs)
├── sidebar.containers[1].items             (all tabs/folders as id/data pairs)
└── Each space's containerIDs = ['pinned', '<uuid-A>', 'unpinned', '<uuid-B>']
    ├── uuid-A.childrenIds → pinned tabs in display order
    └── uuid-B.childrenIds → session/open tabs in display order
```

**Important**: `firebaseSyncState.spaceModels` can be empty if Firebase sync is
disabled. The extractor falls back to `sidebar.containers[1].spaces` which
always has local space metadata.

**Pinned vs unpinned**: The `containerIDs` array uses label strings ('pinned',
'unpinned') to delimit sections. UUID containers between 'pinned' and
'unpinned' hold pinned tab children; those after 'unpinned' hold session tabs.

### Zen Data Structure (Zen >= ~1.6)

```
Zen Profile/
├── zen-sessions.jsonlz4          ← authoritative session store (spaces + tabs)
│   ├── spaces[]                  — workspace definitions (name, uuid, icon, theme)
│   ├── tabs[]                    — all tabs (pinned: true/false, zenWorkspace UUID)
│   ├── folders[]                 — pinned tab folders (id, name, workspaceId)
│   └── groups[]                  — tab groups (matching folder ids)
├── sessionstore.jsonlz4          ← Firefox compat; Zen syncs from zen-sessions
│   └── windows[0].tabs[]         — same tab data with zen* properties
├── zen-sessions-backup/
│   ├── clean.jsonlz4             — clean copy of zen-sessions
│   └── recovery.baklz4           — recovery backup
├── places.sqlite
│   ├── moz_bookmarks             — Firefox bookmarks (type 1=bookmark, 2=folder)
│   ├── moz_places                — URL store
│   └── zen_bookmarks_workspaces  — links bookmark GUIDs to workspace UUIDs
├── containers.json               — Multi-Account Containers (one per workspace)
└── prefs.js                      — zen.workspaces.data (JSON array of workspaces)
```

**Session file format**: 8-byte magic `mozLz40\0` + lz4-block-compressed JSON
with a 4-byte little-endian content size prefix (`lz4.block.compress(data,
store_size=True)`). Using `store_size=False` produces corrupt files that Zen
rejects on startup.

**Workspace UUID sources**: Zen maintains workspace UUIDs in
`zen-sessions.jsonlz4` independently from `prefs.js`. The session file is
authoritative — tabs must reference UUIDs from there, not from prefs.js.
The session importer resolves UUIDs by matching space names or container IDs
against existing session spaces.

### Removed tables (do NOT reference)

- `zen_pins` / `zen_pins_changes` — removed in Zen ~1.6
- `zen_workspaces` / `zen_workspaces_changes` — removed; now in prefs.js

## Key Pitfalls

1. **lz4 store_size**: Must use `store_size=True` when compressing. Firefox/Zen
   expects a 4-byte size prefix; without it the file is "corrupt".

2. **Zen overwrites session files on launch**: Any data written to
   `zen-sessions.jsonlz4` or `sessionstore.jsonlz4` will be replaced by Zen's
   in-memory state when the browser runs. Write to all three files
   (zen-sessions, sessionstore, zen-sessions-backup/clean) for reliability.

3. **Session restore error logs**: If Zen rejects a session file, check
   `<profile>/sessionstore-logs/error-sessionrestore-*.txt` for diagnostics.

4. **Workspace UUID mismatch**: If tabs reference UUIDs that don't exist in
   `zen-sessions.jsonlz4`'s `spaces[]`, Zen silently drops them on startup.

5. **Pinned vs unpinned container selection**: Arc's `containerIDs` array is
   `['pinned', 'uuid-A', 'unpinned', 'uuid-B']`. A container is "pinned" only
   if its index is between the 'pinned' and 'unpinned' labels. Checking
   `idx > pinned_index` alone is wrong because unpinned containers also satisfy
   that condition.

6. **Tab groupId/folder linkage**: Pinned tab folders in Zen require entries in
   both `folders[]` (with `workspaceId`) and `groups[]` (matching `id`). Tabs
   reference their folder via `groupId`.

## Development Notes

- Always close Zen before running — places.sqlite and session files will be
  locked otherwise
- Backups are written to the working directory as
  `zen_database_backup_<timestamp>.sqlite`
- The extractor (`arc_pinned_tab_extractor.py`) is read-only on Arc data
- Dry-run mode skips all writes but exercises the full extraction pipeline
- Profile path detection is handled by `zen_schema_analyzer.py` — do not
  hardcode profile names

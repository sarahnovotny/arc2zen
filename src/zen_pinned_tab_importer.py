#!/usr/bin/env python3
"""
Zen Pinned Tab Importer

Imports Arc pinned tabs into Zen browser via moz_bookmarks + moz_places +
zen_bookmarks_workspaces.

Schema change (Zen >= ~1.6): zen_pins and zen_pins_changes tables were removed.
Pinned tabs are now standard Firefox bookmarks in moz_bookmarks, with workspace
assignment tracked in zen_bookmarks_workspaces.

Changes from original:
- Replaced all zen_pins reads/writes with moz_bookmarks + moz_places
- Added zen_bookmarks_workspaces inserts for workspace assignment
- Added _get_bookmark_parent_id() helper (guid → moz_bookmarks.id)
- create_folder() / create_pinned_tab() return/accept guids, not UUIDs
- tab_exists() checks moz_places.url instead of zen_pins
- All other logic (ordering, folder hierarchy, dry-run, session cache) unchanged
"""

import sqlite3
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
import json

logger = logging.getLogger(__name__)


@dataclass
class ZenPinnedTab:
    """Represents a pinned tab in Zen."""
    uuid: str           # used as moz_bookmarks.guid (truncated to 12 chars)
    title: str
    url: str
    container_id: int
    workspace_uuid: str
    position: int
    is_essential: bool = False
    is_group: bool = False
    parent_uuid: Optional[str] = None   # moz_bookmarks.guid of parent folder
    edited_title: bool = False
    is_folder_collapsed: bool = False
    folder_icon: Optional[str] = None
    arc_tab_id: Optional[str] = None


@dataclass
class ZenFolder:
    """Represents a folder in Zen's pinned tabs."""
    uuid: str
    title: str
    container_id: int
    workspace_uuid: str
    position: int
    parent_uuid: Optional[str] = None
    is_collapsed: bool = False
    icon: Optional[str] = None


def _make_guid() -> str:
    """Generate a Firefox-style 12-char GUID."""
    return str(uuid.uuid4()).replace('-', '')[:12]


def _now_us() -> int:
    """Microseconds since epoch (Firefox bookmark timestamp format)."""
    return int(datetime.now().timestamp() * 1_000_000)


def _now_ms() -> int:
    """Milliseconds since epoch (zen_bookmarks_workspaces timestamp format)."""
    return int(datetime.now().timestamp() * 1000)


def _rev_host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc
        return ".".join(reversed(netloc.split("."))) + "."
    except Exception:
        return ""


def _url_hash(url: str) -> int:
    return hash(url.encode('utf-8')) & 0x7FFFFFFF


class ZenPinnedTabImporter:
    """Imports Arc pinned tabs into Zen via moz_bookmarks / zen_bookmarks_workspaces."""

    # Standard Firefox GUID for the "Unfiled Bookmarks" root
    UNFILED_GUID = "unfiled_____"
    # Type constants
    TYPE_BOOKMARK = 1
    TYPE_FOLDER = 2

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.places_db = zen_profile_path / "places.sqlite"
        # Track tabs imported in current session to prevent duplicates
        self.imported_in_session: set = set()

    # ------------------------------------------------------------------
    # Compatibility shim: old callers expected this; now a no-op
    # (zen_pins no longer exists)
    # ------------------------------------------------------------------
    def _ensure_arc_tab_id_column(self):
        pass  # zen_pins table removed; nothing to do

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _unfiled_id(self, conn: sqlite3.Connection) -> int:
        """Return the moz_bookmarks integer id for 'Unfiled Bookmarks'."""
        cur = conn.execute(
            "SELECT id FROM moz_bookmarks WHERE guid = ?", (self.UNFILED_GUID,)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        # Fallback: id=5 is the typical Firefox default for unfiled
        return 5

    def _get_bookmark_parent_id(self, conn: sqlite3.Connection,
                                 parent_guid: Optional[str]) -> int:
        """
        Translate a bookmark GUID (returned by create_folder) to the
        moz_bookmarks integer id needed for parent= inserts.

        Falls back to unfiled root if guid is None or not found.
        """
        if not parent_guid:
            return self._unfiled_id(conn)
        cur = conn.execute(
            "SELECT id FROM moz_bookmarks WHERE guid = ?", (parent_guid,)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        logger.warning(f"Parent guid not found: {parent_guid}, using unfiled root")
        return self._unfiled_id(conn)

    def _ensure_place(self, conn: sqlite3.Connection, url: str, title: str) -> int:
        """Return moz_places.id for url, creating the row if needed."""
        cur = conn.execute("SELECT id FROM moz_places WHERE url = ?", (url,))
        row = cur.fetchone()
        if row:
            return row[0]
        place_guid = _make_guid()
        cur = conn.execute(
            """INSERT INTO moz_places
               (url, title, rev_host, visit_count, frecency, guid, url_hash)
               VALUES (?, ?, ?, 1, 100, ?, ?)""",
            (url, title, _rev_host(url), place_guid, _url_hash(url))
        )
        return cur.lastrowid

    def _link_workspace(self, conn: sqlite3.Connection,
                        bookmark_guid: str, workspace_uuid: str):
        """Insert or replace a row in zen_bookmarks_workspaces."""
        now = _now_ms()
        conn.execute(
            """INSERT OR REPLACE INTO zen_bookmarks_workspaces
               (bookmark_guid, workspace_uuid, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (bookmark_guid, workspace_uuid, now, now)
        )

    # ------------------------------------------------------------------
    # Public API (signatures unchanged from original)
    # ------------------------------------------------------------------

    def get_workspace_uuids(self) -> Dict[int, str]:
        """
        Return {container_id: workspace_uuid} from existing zen_bookmarks_workspaces.
        (Original read from zen_pins; now reads from zen_bookmarks_workspaces.)
        """
        try:
            with sqlite3.connect(self.places_db) as conn:
                cur = conn.execute(
                    "SELECT DISTINCT workspace_uuid FROM zen_bookmarks_workspaces"
                )
                # We no longer have container_id in this table; return empty mapping
                # so callers that relied on it degrade gracefully.
                return {}
        except Exception as e:
            logger.error(f"Failed to get workspace UUIDs: {e}")
            return {}

    def create_workspace_uuid_mappings(self,
                                        container_mappings: Dict[str, int]
                                        ) -> Dict[str, str]:
        """Create temporary workspace UUIDs for each Arc space (unchanged logic)."""
        workspace_mappings = {}
        for space_name in container_mappings:
            ws_uuid = "{" + str(uuid.uuid4()) + "}"
            workspace_mappings[space_name] = ws_uuid
            logger.info(f"  📁 Creating new workspace for {space_name}: {ws_uuid}")
        return workspace_mappings

    def get_next_position(self, workspace_uuid: str) -> int:
        """
        Get a starting position for tabs in a workspace.
        (Original queried zen_pins; now queries moz_bookmarks under unfiled.)
        """
        try:
            with sqlite3.connect(self.places_db) as conn:
                unfiled = self._unfiled_id(conn)
                cur = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
                    (unfiled,)
                )
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"Failed to get next position: {e}")
            return 1

    def create_folder(self, title: str, container_id: int, workspace_uuid: str,
                      position: int, parent_uuid: Optional[str] = None) -> str:
        """
        Create a folder bookmark in moz_bookmarks and link it to the workspace.

        Returns the new folder's moz_bookmarks.guid (12-char string).
        Original returned a curly-brace UUID; callers treat the return value
        opaquely so this is compatible.
        """
        # Check if folder already exists under the same parent in this workspace
        try:
            with sqlite3.connect(self.places_db) as conn:
                parent_id = self._get_bookmark_parent_id(conn, parent_uuid)
                cur = conn.execute(
                    """SELECT mb.guid FROM moz_bookmarks mb
                       LEFT JOIN zen_bookmarks_workspaces zbw ON mb.guid = zbw.bookmark_guid
                       WHERE mb.title = ? AND mb.parent = ? AND mb.type = 2
                         AND (zbw.workspace_uuid = ? OR zbw.workspace_uuid IS NULL)""",
                    (title, parent_id, workspace_uuid)
                )
                existing = cur.fetchone()
                if existing:
                    logger.info(f"    📁 Folder '{title}' already exists, reusing")
                    return existing[0]
        except Exception as e:
            logger.warning(f"Failed to check for existing folder: {e}")

        folder_guid = _make_guid()
        now_us = _now_us()

        try:
            with sqlite3.connect(self.places_db) as conn:
                parent_id = self._get_bookmark_parent_id(conn, parent_uuid)
                cur = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
                    (parent_id,)
                )
                pos = cur.fetchone()[0]

                conn.execute(
                    """INSERT INTO moz_bookmarks
                       (type, parent, position, title, dateAdded, lastModified, guid)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (self.TYPE_FOLDER, parent_id, pos, title, now_us, now_us, folder_guid)
                )
                self._link_workspace(conn, folder_guid, workspace_uuid)
                conn.commit()
                return folder_guid

        except Exception as e:
            logger.error(f"Failed to create folder '{title}': {e}")
            return ""

    def tab_exists(self, arc_tab_id: str, title: str, url: str) -> bool:
        """Check if a URL already exists as a bookmark (dedup guard)."""
        session_key = (arc_tab_id, title, url)
        if session_key in self.imported_in_session:
            return True
        try:
            with sqlite3.connect(self.places_db) as conn:
                cur = conn.execute(
                    """SELECT COUNT(*) FROM moz_bookmarks mb
                       JOIN moz_places mp ON mb.fk = mp.id
                       WHERE mp.url = ? AND mb.type = 1""",
                    (url,)
                )
                return cur.fetchone()[0] > 0
        except Exception as e:
            logger.error(f"Failed to check if tab exists: {e}")
            return False

    def create_pinned_tab(self, tab: ZenPinnedTab) -> bool:
        """
        Create a pinned tab as a moz_bookmarks entry linked to its workspace.

        Storage: moz_places (URL) + moz_bookmarks (bookmark) +
                 zen_bookmarks_workspaces (workspace assignment).
        """
        if self.tab_exists(tab.arc_tab_id, tab.title, tab.url):
            logger.info(f"    ⚠️ Skipping duplicate tab: {tab.title}")
            return False

        bm_guid = _make_guid()
        now_us = _now_us()

        try:
            with sqlite3.connect(self.places_db) as conn:
                place_id = self._ensure_place(conn, tab.url, tab.title)
                parent_id = self._get_bookmark_parent_id(conn, tab.parent_uuid)

                cur = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
                    (parent_id,)
                )
                pos = cur.fetchone()[0]

                conn.execute(
                    """INSERT INTO moz_bookmarks
                       (type, fk, parent, position, title, dateAdded, lastModified, guid)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.TYPE_BOOKMARK, place_id, parent_id, pos,
                     tab.title, now_us, now_us, bm_guid)
                )
                self._link_workspace(conn, bm_guid, tab.workspace_uuid)
                conn.commit()

            self.imported_in_session.add((tab.arc_tab_id, tab.title, tab.url))
            return True

        except Exception as e:
            logger.error(f"Failed to create pinned tab '{tab.title}': {e}")
            return False

    def build_folder_hierarchy(self, space_name: str, pinned_tabs: List[Dict],
                               container_id: int, workspace_uuid: str) -> Dict[str, str]:
        """Build folder hierarchy and return path → guid mapping (logic unchanged)."""
        folder_uuids: Dict[str, str] = {}

        ordered_paths: List[str] = []
        seen_paths: set = set()
        for tab in pinned_tabs:
            for i in range(len(tab.get('folder_path', []))):
                path = "/".join(tab['folder_path'][:i + 1])
                if path not in seen_paths:
                    ordered_paths.append(path)
                    seen_paths.add(path)

        position = 0
        for path in ordered_paths:
            if path in folder_uuids:
                continue
            parts = path.split("/")
            folder_name = parts[-1]
            parent_path = "/".join(parts[:-1]) if len(parts) > 1 else ""
            parent_guid = folder_uuids.get(parent_path)

            folder_guid = self.create_folder(
                folder_name, container_id, workspace_uuid, position, parent_guid
            )
            if folder_guid:
                folder_uuids[path] = folder_guid
                position += 1
                logger.info(f"    📁 Created folder: {folder_name}")

        return folder_uuids

    def get_existing_folders(self, workspace_uuid: str) -> Dict[str, str]:
        """Return {title: guid} for folders already linked to this workspace."""
        existing: Dict[str, str] = {}
        try:
            with sqlite3.connect(self.places_db) as conn:
                cur = conn.execute(
                    """SELECT mb.guid, mb.title
                       FROM moz_bookmarks mb
                       JOIN zen_bookmarks_workspaces zbw ON mb.guid = zbw.bookmark_guid
                       WHERE zbw.workspace_uuid = ? AND mb.type = 2""",
                    (workspace_uuid,)
                )
                for guid, title in cur.fetchall():
                    if title:
                        existing[title] = guid
        except Exception as e:
            logger.error(f"Failed to get existing folders: {e}")
        return existing

    def create_exported_folders(self, folders: List[Dict], container_id: int,
                                 workspace_uuid: str) -> Dict[str, str]:
        """
        Create folders from exported folder data, preserving Arc order and hierarchy.
        Logic unchanged from original; only storage layer differs.
        """
        folder_uuids: Dict[str, str] = {}
        existing_folders = self.get_existing_folders(workspace_uuid)
        folder_uuids.update(existing_folders)

        sorted_folders = sorted(folders, key=lambda f: f.get('index', 0))
        folder_id_to_data = {
            f['folder_id']: f for f in sorted_folders if f.get('folder_id')
        }
        created_folders: set = set()
        position = 0

        def create_folder_with_hierarchy(folder_data):
            nonlocal position
            folder_id = folder_data.get('folder_id', '')
            folder_title = folder_data.get('title', 'Untitled Folder')

            if folder_title in existing_folders:
                folder_uuids[folder_title] = existing_folders[folder_title]
                if folder_id:
                    folder_uuids[folder_id] = existing_folders[folder_title]
                logger.info(f"    📁 Using existing folder: {folder_title}")
                return

            if folder_id in created_folders:
                return

            folder_parent_id = folder_data.get('parent_id', '')
            parent_guid = None

            if folder_parent_id and folder_parent_id in folder_uuids:
                parent_guid = folder_uuids[folder_parent_id]
            elif folder_parent_id and folder_parent_id in folder_id_to_data:
                create_folder_with_hierarchy(folder_id_to_data[folder_parent_id])
                parent_guid = folder_uuids.get(folder_parent_id)

            folder_guid = self.create_folder(
                folder_title, container_id, workspace_uuid, position, parent_guid
            )
            if folder_guid:
                if folder_id:
                    folder_uuids[folder_id] = folder_guid
                folder_uuids[folder_title] = folder_guid
                created_folders.add(folder_id)
                position += 1
                parent_name = folder_id_to_data.get(folder_parent_id, {}).get('title')
                parent_info = f" (child of {parent_name})" if parent_guid and parent_name else ""
                logger.info(f"    📁 Created folder: {folder_title}{parent_info}")

        for folder_data in sorted_folders:
            create_folder_with_hierarchy(folder_data)

        return folder_uuids

    def import_arc_pinned_tabs(self, arc_export_data: Dict,
                                container_mappings: Dict[str, int],
                                dry_run: bool = False,
                                workspace_uuid_override: Optional[Dict[str, str]] = None,
                                ) -> Dict[str, str]:
        """
        Import Arc pinned tabs as Zen bookmarks linked to workspace UUIDs.

        Returns: dict mapping space names to (temporary) workspace UUIDs.
        Logic unchanged; only storage layer differs.
        """
        try:
            logger.info("📌 Importing Arc pinned tabs into Zen pinned tab system...")
            if dry_run:
                logger.info("🧪 DRY RUN - No database changes will be made")

            if workspace_uuid_override:
                workspace_mappings = workspace_uuid_override
                logger.info("Using real workspace UUIDs from prefs.js")
            else:
                workspace_mappings = self.create_workspace_uuid_mappings(container_mappings)
            total_tabs = 0
            total_folders = 0

            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                pinned_tabs = space.get('pinned_tabs', [])
                folders = space.get('folders', [])

                container_id = container_mappings.get(space_name, 1)
                workspace_uuid = workspace_mappings.get(space_name)

                if not workspace_uuid:
                    logger.warning(f"No workspace UUID for space: {space_name}")
                    continue

                logger.info(
                    f"  📁 Processing {space_name}: {len(pinned_tabs)} tabs, "
                    f"{len(folders)} folders (preserving Arc sidebar order)"
                )

                if dry_run:
                    total_tabs += len(pinned_tabs)
                    total_folders += len(folders)
                    continue

                folder_uuids = self.create_exported_folders(
                    folders, container_id, workspace_uuid
                )
                total_folders += len(folder_uuids)

                base_position = self.get_next_position(workspace_uuid)

                for i, tab_data in enumerate(pinned_tabs):
                    folder_path = tab_data.get('folder_path', [])
                    parent_guid = None

                    if folder_path:
                        immediate_parent = folder_path[-1]
                        parent_guid = folder_uuids.get(immediate_parent)
                        if not parent_guid:
                            for fn in reversed(folder_path):
                                parent_guid = folder_uuids.get(fn)
                                if parent_guid:
                                    break

                    position = base_position + i
                    is_essential = tab_data.get('is_essential', False)
                    arc_tab_id = tab_data.get('tab_id')

                    tab = ZenPinnedTab(
                        uuid=_make_guid(),
                        title=tab_data['title'],
                        url=tab_data['url'],
                        container_id=container_id,
                        workspace_uuid=workspace_uuid,
                        position=position,
                        is_essential=is_essential,
                        parent_uuid=parent_guid,
                        arc_tab_id=arc_tab_id,
                    )

                    if self.create_pinned_tab(tab):
                        total_tabs += 1

                skipped = len(pinned_tabs) - total_tabs
                if skipped > 0:
                    logger.info(
                        f"    ✅ Imported {total_tabs} pinned tabs "
                        f"({skipped} skipped as duplicates)"
                    )
                else:
                    logger.info(f"    ✅ Imported {total_tabs} pinned tabs")

            if dry_run:
                logger.info(
                    f"🧪 Would import {total_tabs} pinned tabs "
                    f"and create {total_folders} folders"
                )
                return {}

            logger.info(
                f"✅ Successfully imported {total_tabs} pinned tabs "
                f"and {total_folders} folders"
            )
            logger.info("🔄 Restart Zen browser to see your imported pinned tabs")
            return workspace_mappings

        except Exception as e:
            logger.error(f"Failed to import Arc pinned tabs: {e}")
            return {}

    def clear_imported_pins(self, workspace_uuids: List[str]) -> bool:
        """
        Clear previously imported pins for re-import.
        (Original deleted from zen_pins; now deletes from moz_bookmarks via
        zen_bookmarks_workspaces.)
        """
        try:
            with sqlite3.connect(self.places_db) as conn:
                placeholders = ",".join(["?" for _ in workspace_uuids])
                cur = conn.execute(
                    f"""SELECT bookmark_guid FROM zen_bookmarks_workspaces
                        WHERE workspace_uuid IN ({placeholders})""",
                    workspace_uuids
                )
                guids = [row[0] for row in cur.fetchall()]

                if guids:
                    guid_ph = ",".join(["?" for _ in guids])
                    conn.execute(
                        f"""DELETE FROM zen_bookmarks_workspaces
                            WHERE workspace_uuid IN ({placeholders})""",
                        workspace_uuids
                    )
                    conn.execute(
                        f"DELETE FROM moz_bookmarks WHERE guid IN ({guid_ph})",
                        guids
                    )

                conn.commit()
                logger.info("🧹 Cleared existing imported pins")
                return True

        except Exception as e:
            logger.error(f"Failed to clear imported pins: {e}")
            return False

#!/usr/bin/env python3
"""
Zen Session Importer

Imports Arc pinned tabs into Zen browser by writing to both
zen-sessions.jsonlz4 and sessionstore.jsonlz4.

zen-sessions.jsonlz4 is Zen's authoritative session store for workspaces
and pinned tabs. On startup Zen reads it, then syncs state into the
standard Firefox sessionstore.jsonlz4. On shutdown it writes back to both.

Workspace UUIDs must come from zen-sessions.jsonlz4 (not prefs.js) since
Zen creates its own UUIDs when spaces are made in the UI.

File format: 8-byte magic b'mozLz40\0' + lz4-block-compressed JSON.
"""

import json
import logging
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import lz4.block

logger = logging.getLogger(__name__)

MOZLZ4_MAGIC = b"mozLz40\x00"


def _sync_id() -> str:
    ts = int(datetime.now().timestamp() * 1000)
    rand = random.randint(10, 99)
    return f"{ts}-{rand}"


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _read_mozlz4(path: Path) -> dict:
    with open(path, "rb") as f:
        magic = f.read(8)
        if magic != MOZLZ4_MAGIC:
            raise ValueError(f"Bad magic: {magic!r}")
        raw = lz4.block.decompress(f.read())
    return json.loads(raw)


def _write_mozlz4(path: Path, data: dict) -> None:
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    compressed = lz4.block.compress(raw, store_size=True)
    with open(path, "wb") as f:
        f.write(MOZLZ4_MAGIC)
        f.write(compressed)


def _make_pinned_tab_zen(url: str, title: str, workspace_uuid: str,
                         container_id: int) -> dict:
    """Build a pinned-tab for zen-sessions.jsonlz4."""
    entry = {
        "url": url,
        "title": title,
        "triggeringPrincipal_base64": '{"3":{}}',
    }
    return {
        "entries": [entry],
        "lastAccessed": _now_ms(),
        "pinned": True,
        "hidden": False,
        "zenWorkspace": workspace_uuid,
        "zenSyncId": _sync_id(),
        "zenEssential": False,
        "zenDefaultUserContextId": None,
        "zenPinnedIcon": None,
        "zenIsEmpty": False,
        "zenHasStaticIcon": False,
        "zenGlanceId": None,
        "zenIsGlance": False,
        "_zenPinnedInitialState": {
            "entry": dict(entry),
            "image": None,
        },
        "zenLiveFolderItemId": None,
        "searchMode": None,
        "userContextId": container_id,
        "attributes": {},
        "index": 1,
        "userTypedValue": "",
        "userTypedClear": 0,
        "image": None,
    }


def _make_pinned_tab_ss(url: str, title: str, workspace_uuid: str,
                        container_id: int) -> dict:
    """Build a pinned-tab for sessionstore.jsonlz4 (Firefox format + zen props)."""
    entry = {
        "url": url,
        "title": title,
        "triggeringPrincipal_base64": '{"3":{}}',
    }
    return {
        "entries": [entry],
        "lastAccessed": _now_ms(),
        "pinned": True,
        "hidden": False,
        "zenWorkspace": workspace_uuid,
        "zenSyncId": _sync_id(),
        "zenEssential": False,
        "zenDefaultUserContextId": None,
        "zenPinnedIcon": None,
        "zenIsEmpty": False,
        "zenHasStaticIcon": False,
        "zenGlanceId": None,
        "zenIsGlance": False,
        "_zenPinnedInitialState": {
            "entry": dict(entry),
            "image": None,
        },
        "zenLiveFolderItemId": None,
        "searchMode": None,
        "userContextId": container_id,
        "attributes": {},
        "index": 1,
        "userTypedValue": "",
        "userTypedClear": 0,
        "image": None,
    }


def _make_folder(folder_id: str, name: str, workspace_uuid: str) -> dict:
    """Build a folder entry for zen-sessions.jsonlz4."""
    return {
        "pinned": True,
        "splitViewGroup": False,
        "id": folder_id,
        "name": name,
        "collapsed": False,
        "saveOnWindowClose": True,
        "parentId": None,
        "prevSiblingInfo": None,
        "emptyTabIds": [],
        "userIcon": "",
        "workspaceId": workspace_uuid,
    }


def _make_group(folder_id: str, name: str) -> dict:
    """Build a group entry for zen-sessions.jsonlz4 / sessionstore.jsonlz4."""
    return {
        "pinned": True,
        "splitView": False,
        "id": folder_id,
        "name": name,
        "color": "zen-workspace-color",
        "collapsed": False,
        "saveOnWindowClose": True,
    }


def _make_session_tab(url: str, title: str, workspace_uuid: str,
                      container_id: int) -> dict:
    """Build an unpinned session tab entry."""
    entry = {
        "url": url,
        "title": title,
        "triggeringPrincipal_base64": '{"3":{}}',
    }
    return {
        "entries": [entry],
        "lastAccessed": _now_ms(),
        "pinned": False,
        "hidden": False,
        "zenWorkspace": workspace_uuid,
        "zenSyncId": _sync_id(),
        "zenEssential": False,
        "zenDefaultUserContextId": "true",
        "zenPinnedIcon": None,
        "zenIsEmpty": False,
        "zenHasStaticIcon": False,
        "zenGlanceId": None,
        "zenIsGlance": False,
        "zenLiveFolderItemId": None,
        "searchMode": None,
        "userContextId": container_id,
        "attributes": {},
        "index": 1,
        "userTypedValue": "",
        "userTypedClear": 0,
        "image": None,
    }


def _make_space(name: str, uuid: str, container_id: int,
                icon: Optional[str] = None) -> dict:
    """Build a space entry for zen-sessions.jsonlz4."""
    space = {
        "uuid": uuid,
        "name": name,
        "theme": {
            "type": "gradient",
            "gradientColors": [],
            "opacity": 0.5,
            "texture": 0,
        },
        "containerTabId": container_id,
        "hasCollapsedPinnedTabs": False,
    }
    if icon:
        space["icon"] = icon
    return space


class ZenSessionImporter:
    """Imports Arc pinned tabs into Zen's session files."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.zen_sessions = zen_profile_path / "zen-sessions.jsonlz4"
        self.sessionstore = zen_profile_path / "sessionstore.jsonlz4"

    def _resolve_workspace_uuids(
        self,
        arc_export_data: Dict,
        container_mappings: Dict[str, int],
        zen_session: Dict,
        workspace_icons: Optional[Dict[str, str]] = None,
    ) -> tuple:
        """
        Resolve workspace UUIDs from zen-sessions.jsonlz4 spaces.

        For each Arc space:
        - If a space with matching name exists in zen-sessions, use its UUID
        - If a space with matching containerTabId exists, use its UUID
        - Otherwise create a new space entry

        Returns (space_name_to_uuid mapping, list of new space entries to add).
        """
        existing_spaces = zen_session.get("spaces", [])

        # Build lookups
        name_to_space = {}
        container_to_space = {}
        for s in existing_spaces:
            name_to_space[s.get("name", "")] = s
            cid = s.get("containerTabId", 0)
            if cid > 0:
                container_to_space[cid] = s

        mappings: Dict[str, str] = {}
        new_spaces: List[dict] = []

        for space in arc_export_data.get("spaces", []):
            space_name = space["space_name"]
            container_id = container_mappings.get(space_name, 0)

            # Try name match first
            existing = name_to_space.get(space_name)
            if not existing and container_id > 0:
                # Try container match
                existing = container_to_space.get(container_id)

            if existing:
                uuid = existing["uuid"]
                logger.info(f"  = Using existing space: {space_name} -> {uuid}")
                mappings[space_name] = uuid
            else:
                # Create new space
                import uuid as uuid_mod
                new_uuid = "{" + str(uuid_mod.uuid4()) + "}"
                icon = (workspace_icons or {}).get(space_name)
                new_space = _make_space(space_name, new_uuid, container_id, icon)
                new_spaces.append(new_space)
                mappings[space_name] = new_uuid
                logger.info(f"  + New space: {space_name} -> {new_uuid}")

        return mappings, new_spaces

    def import_pinned_tabs(
        self,
        arc_export_data: Dict,
        container_mappings: Dict[str, int],
        workspace_mappings: Dict[str, str],  # ignored — we resolve from zen-sessions
        workspace_icons: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> bool:
        """
        Import Arc pinned tabs into zen-sessions.jsonlz4 and sessionstore.jsonlz4.

        Workspace UUIDs are resolved from zen-sessions.jsonlz4 (Zen's
        authoritative source), NOT from prefs.js.

        Returns True on success.
        """
        if self.zen_sessions.exists():
            try:
                zen_session = _read_mozlz4(self.zen_sessions)
            except Exception as e:
                logger.warning(f"Failed to read zen-sessions.jsonlz4, starting fresh: {e}")
                zen_session = {"spaces": [], "tabs": [], "folders": [],
                               "splitViewData": [], "groups": [],
                               "lastCollected": _now_ms()}
        else:
            logger.info("No zen-sessions.jsonlz4 found, creating new session file")
            zen_session = {"spaces": [], "tabs": [], "folders": [],
                           "splitViewData": [], "groups": [],
                           "lastCollected": _now_ms()}

        # Resolve workspace UUIDs from zen-sessions (not prefs.js)
        ws_mappings, new_spaces = self._resolve_workspace_uuids(
            arc_export_data, container_mappings, zen_session, workspace_icons
        )

        # Index all existing tab URLs per workspace for dedup
        existing_urls: Dict[str, set] = {}
        for tab in zen_session.get("tabs", []):
            ws = tab.get("zenWorkspace", "")
            urls = existing_urls.setdefault(ws, set())
            for entry in tab.get("entries", []):
                urls.add(entry.get("url", ""))

        new_zen_tabs = []
        new_ss_tabs = []
        new_folders = []
        new_groups = []
        total_imported = 0
        total_folders = 0

        # Index existing folder names per workspace for dedup
        existing_folder_names: Dict[str, set] = {}
        for folder in zen_session.get("folders", []):
            ws = folder.get("workspaceId", "")
            existing_folder_names.setdefault(ws, set()).add(folder.get("name", ""))

        for space in arc_export_data.get("spaces", []):
            space_name = space["space_name"]
            container_id = container_mappings.get(space_name, 0)
            workspace_uuid = ws_mappings.get(space_name)

            if not workspace_uuid:
                logger.warning(f"No workspace UUID for {space_name}, skipping")
                continue

            # Create folders for this workspace
            # Map folder_name -> folder_id for groupId assignment
            folder_name_to_id: Dict[str, str] = {}
            ws_existing_folders = existing_folder_names.get(workspace_uuid, set())

            for folder_data in space.get("folders", []):
                folder_name = folder_data.get("title", "Untitled Folder")
                if folder_name in ws_existing_folders:
                    continue
                folder_id = _sync_id()
                folder_name_to_id[folder_name] = folder_id
                new_folders.append(_make_folder(folder_id, folder_name, workspace_uuid))
                new_groups.append(_make_group(folder_id, folder_name))
                ws_existing_folders.add(folder_name)
                total_folders += 1

            ws_existing_urls = existing_urls.get(workspace_uuid, set())
            pinned_tabs = space.get("pinned_tabs", [])
            space_imported = 0

            for tab_data in pinned_tabs:
                url = tab_data.get("url", "")
                title = tab_data.get("title", "Untitled")

                if url in ws_existing_urls:
                    continue

                zen_tab = _make_pinned_tab_zen(url, title, workspace_uuid, container_id)
                ss_tab = _make_pinned_tab_ss(url, title, workspace_uuid, container_id)

                # Assign to folder if tab has a folder_path
                folder_path = tab_data.get("folder_path", [])
                if folder_path:
                    # Use the immediate parent folder name
                    folder_name = folder_path[-1]
                    group_id = folder_name_to_id.get(folder_name)
                    if group_id:
                        zen_tab["groupId"] = group_id
                        ss_tab["groupId"] = group_id

                new_zen_tabs.append(zen_tab)
                new_ss_tabs.append(ss_tab)
                ws_existing_urls.add(url)
                space_imported += 1

            skipped = len(pinned_tabs) - space_imported
            total_imported += space_imported

            # Process unpinned session tabs
            session_tabs = space.get("session_tabs", [])
            session_imported = 0
            for tab_data in session_tabs:
                url = tab_data.get("url", "")
                title = tab_data.get("title", "Untitled")
                if url in ws_existing_urls:
                    continue
                sess_tab = _make_session_tab(url, title, workspace_uuid, container_id)
                new_zen_tabs.append(sess_tab)
                new_ss_tabs.append(dict(sess_tab))  # copy for sessionstore
                ws_existing_urls.add(url)
                session_imported += 1

            skip_info = f" ({skipped} duplicates skipped)" if skipped else ""
            folder_info = f", {len(folder_name_to_id)} folders" if folder_name_to_id else ""
            session_info = f", {session_imported} session tabs" if session_imported else ""
            logger.info(f"  {space_name}: {space_imported} pinned{folder_info}{session_info}{skip_info}")

        if dry_run:
            logger.info(
                f"DRY RUN: would add {len(new_spaces)} spaces, "
                f"{total_imported} pinned tabs"
            )
            return True

        # Back up both session files
        for src, suffix in [
            (self.zen_sessions, "zen-sessions-pre-import.jsonlz4"),
            (self.sessionstore, "sessionstore-pre-import.jsonlz4"),
        ]:
            if src.exists():
                try:
                    shutil.copy2(src, self.zen_profile / suffix)
                except Exception as e:
                    logger.warning(f"Could not back up {src.name}: {e}")
        logger.info("Session backups created")

        # --- Write zen-sessions.jsonlz4 ---
        zen_session.setdefault("spaces", []).extend(new_spaces)
        zen_session.setdefault("folders", []).extend(new_folders)
        zen_session.setdefault("groups", []).extend(new_groups)

        zen_tabs = zen_session.get("tabs", [])
        pinned_end = 0
        for i, tab in enumerate(zen_tabs):
            if tab.get("pinned"):
                pinned_end = i + 1
        zen_session["tabs"] = (
            zen_tabs[:pinned_end] + new_zen_tabs + zen_tabs[pinned_end:]
        )

        try:
            _write_mozlz4(self.zen_sessions, zen_session)
            logger.info(
                f"Wrote {total_imported} pinned tabs, {total_folders} folders, "
                f"and {len(new_spaces)} new spaces to zen-sessions.jsonlz4"
            )
        except Exception as e:
            logger.error(f"Failed to write zen-sessions.jsonlz4: {e}")
            return False

        # --- Write sessionstore.jsonlz4 ---
        try:
            if self.sessionstore.exists():
                ss = _read_mozlz4(self.sessionstore)
            else:
                ss = {"version": ["sessionrestore", 1], "windows": [{"tabs": []}],
                      "selectedWindow": 0, "_closedWindows": [], "savedGroups": [],
                      "session": {}, "global": {}}
            window = ss.get("windows", [{}])[0]
            ss_tabs = window.get("tabs", [])
            pinned_end = 0
            for i, tab in enumerate(ss_tabs):
                if tab.get("pinned"):
                    pinned_end = i + 1
            window["tabs"] = (
                ss_tabs[:pinned_end] + new_ss_tabs + ss_tabs[pinned_end:]
            )
            ss.setdefault("savedGroups", []).extend(new_groups)
            _write_mozlz4(self.sessionstore, ss)
            logger.info(f"Wrote {total_imported} pinned tabs to sessionstore.jsonlz4")
        except Exception as e:
            logger.warning(f"Could not update sessionstore.jsonlz4: {e}")

        # --- Also write to zen-sessions-backup/clean.jsonlz4 ---
        try:
            backup_dir = self.zen_profile / "zen-sessions-backup"
            clean_path = backup_dir / "clean.jsonlz4"
            if clean_path.exists():
                _write_mozlz4(clean_path, zen_session)
                logger.info("Updated zen-sessions-backup/clean.jsonlz4")
        except Exception as e:
            logger.warning(f"Could not update backup/clean.jsonlz4: {e}")

        return True

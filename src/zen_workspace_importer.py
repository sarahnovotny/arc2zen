#!/usr/bin/env python3
"""
Zen Workspace Importer

Creates Zen workspaces for each Arc space and properly assigns pinned tabs.

Schema change (Zen >= ~1.6): zen_workspaces and zen_workspaces_changes tables
were removed. Workspace metadata is now stored in prefs.js under
'zen.workspaces.data'. Workspace-to-bookmark assignment uses
zen_bookmarks_workspaces in places.sqlite.

Changes from original:
- get_existing_workspaces() reads prefs.js instead of zen_workspaces table
- create_workspace() writes prefs.js instead of zen_workspaces table
- update_pinned_tabs_workspace() updates zen_bookmarks_workspaces instead of zen_pins
- clear_temporary_workspaces() removes entries from prefs.js
- _write_workspaces_to_prefs() / _read_workspaces_from_prefs() added
- All colour/icon conversion logic and other helpers unchanged
"""

import json
import re
import sqlite3
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ZenWorkspaceImporter:
    """Creates Zen workspaces and properly assigns pinned tabs."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.places_db = zen_profile_path / "places.sqlite"
        self.prefs_file = zen_profile_path / "prefs.js"

    # ------------------------------------------------------------------
    # prefs.js helpers  (new; replaces zen_workspaces table operations)
    # ------------------------------------------------------------------

    def _read_workspaces_from_prefs(self) -> List[Dict]:
        """
        Read workspace definitions from prefs.js.

        Zen stores workspaces as a JSON array under one of these pref names
        (we try each in order to handle different Zen versions):
          zen.workspaces.data
          zen.workspaces.list
        """
        if not self.prefs_file.exists():
            logger.warning(f"prefs.js not found: {self.prefs_file}")
            return []

        try:
            content = self.prefs_file.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Could not read prefs.js: {e}")
            return []

        for pref_name in ['zen.workspaces.data', 'zen.workspaces.list']:
            # Match both single-line and escaped-quote variants
            pattern = rf'user_pref\("{re.escape(pref_name)}",\s*"(.*?)"\);'
            match = re.search(pattern, content, re.DOTALL)
            if match:
                raw = match.group(1)
                # Unescape embedded quotes
                raw = raw.replace('\\"', '"').replace('\\\\', '\\')
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        logger.debug(
                            f"Read {len(data)} workspaces from prefs.js "
                            f"({pref_name})"
                        )
                        return data
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Could not parse {pref_name} from prefs.js: {e}"
                    )

        logger.debug("No workspace data found in prefs.js")
        return []

    def _write_workspaces_to_prefs(self, workspaces: List[Dict]) -> bool:
        """
        Write workspace definitions to prefs.js under zen.workspaces.data.
        Creates the pref line if not present; replaces it if it is.
        """
        pref_name = 'zen.workspaces.data'
        try:
            content = self.prefs_file.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Could not read prefs.js: {e}")
            return False

        # Escape the JSON for embedding in a JS string literal
        json_str = json.dumps(workspaces, ensure_ascii=False)
        json_escaped = json_str.replace('\\', '\\\\').replace('"', '\\"')
        new_line = f'user_pref("{pref_name}", "{json_escaped}");'

        pattern = rf'user_pref\("{re.escape(pref_name)}",\s*".*?"\);'
        if re.search(pattern, content, re.DOTALL):
            new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
        else:
            new_content = content.rstrip() + f'\n{new_line}\n'

        try:
            self.prefs_file.write_text(new_content, encoding='utf-8')
            logger.debug(f"Wrote {len(workspaces)} workspaces to prefs.js")
            return True
        except Exception as e:
            logger.error(f"Could not write prefs.js: {e}")
            return False

    # ------------------------------------------------------------------
    # Icon / colour helpers (unchanged from original)
    # ------------------------------------------------------------------

    def _map_arc_icon_to_zen(self, arc_icon: Optional[str]) -> Optional[str]:
        return arc_icon

    def _convert_rgb_to_zen_theme(self, color: Optional[dict]) -> tuple:
        if not color or 'r' not in color or 'g' not in color or 'b' not in color:
            return None, None

        r, g, b = color['r'], color['g'], color['b']
        base_r, base_g, base_b = 185, 225, 150
        scale_r, scale_g, scale_b = 72, 25, 170
        r_255 = max(0, min(255, int(base_r + r * scale_r)))
        g_255 = max(0, min(255, int(base_g + g * scale_g)))
        b_255 = max(0, min(255, int(base_b + b * scale_b)))

        theme_colors = [{
            "c": [r_255, g_255, b_255],
            "isCustom": False,
            "algorithm": "floating",
            "isPrimary": True,
            "lightness": "75",
            "position": {"x": 228, "y": 253},
            "type": "explicit-lightness",
        }]
        return "gradient", json.dumps(theme_colors)

    # ------------------------------------------------------------------
    # Workspace CRUD  (rewritten to use prefs.js)
    # ------------------------------------------------------------------

    def get_existing_workspaces(self) -> Dict[str, Dict]:
        """
        Return {uuid: {name, container_id, position}} from prefs.js.
        (Original read from zen_workspaces table.)
        """
        workspaces: Dict[str, Dict] = {}
        for ws in self._read_workspaces_from_prefs():
            ws_uuid = ws.get('uuid') or ws.get('id', '')
            if ws_uuid:
                workspaces[ws_uuid] = {
                    'name': ws.get('name', ''),
                    'container_id': ws.get('containerTabId',
                                           ws.get('container_id', 1)),
                    'position': ws.get('position', 0),
                }
        return workspaces

    def create_workspace(self, name: str, container_id: int, position: int = 1000,
                         icon: Optional[str] = None,
                         color: Optional[dict] = None) -> Optional[str]:
        """
        Create a new workspace entry in prefs.js.
        (Original inserted into zen_workspaces table.)
        Returns the new workspace UUID, or None on failure.
        """
        ws_uuid = "{" + str(uuid.uuid4()) + "}"
        zen_icon = self._map_arc_icon_to_zen(icon)
        theme_type, theme_colors_str = self._convert_rgb_to_zen_theme(color)

        workspaces = self._read_workspaces_from_prefs()

        ws_entry: Dict[str, Any] = {
            'uuid': ws_uuid,
            'name': name,
            'containerTabId': container_id,
            'position': position,
        }
        if zen_icon:
            ws_entry['icon'] = zen_icon
        if theme_type and theme_colors_str:
            ws_entry['theme'] = {
                'type': theme_type,
                'colors': json.loads(theme_colors_str),
            }

        workspaces.append(ws_entry)

        if self._write_workspaces_to_prefs(workspaces):
            icon_info = f" with icon: {zen_icon}" if zen_icon else ""
            theme_info = f" and theme: {theme_type}" if theme_type else ""
            logger.info(
                f"✅ Created workspace: {name} ({ws_uuid}){icon_info}{theme_info}"
            )
            return ws_uuid

        return None

    def update_workspace_icon_and_color(self, workspace_uuid: str,
                                         icon: Optional[str],
                                         color: Optional[dict]) -> bool:
        """Update icon and colour theme for an existing workspace in prefs.js."""
        if not icon and not color:
            return True

        zen_icon = self._map_arc_icon_to_zen(icon) if icon else None
        theme_type, theme_colors_str = (
            self._convert_rgb_to_zen_theme(color) if color else (None, None)
        )

        workspaces = self._read_workspaces_from_prefs()
        updated = False
        for ws in workspaces:
            if ws.get('uuid') == workspace_uuid:
                if zen_icon:
                    ws['icon'] = zen_icon
                if theme_type and theme_colors_str:
                    ws['theme'] = {
                        'type': theme_type,
                        'colors': json.loads(theme_colors_str),
                    }
                updated = True
                break

        if not updated:
            logger.warning(f"Workspace {workspace_uuid} not found in prefs.js")
            return False

        return self._write_workspaces_to_prefs(workspaces)

    def update_workspace_icon(self, workspace_uuid: str,
                               icon: Optional[str]) -> bool:
        return self.update_workspace_icon_and_color(workspace_uuid, icon, None)

    # ------------------------------------------------------------------
    # Pinned-tab workspace remapping
    # (Original updated zen_pins; now updates zen_bookmarks_workspaces)
    # ------------------------------------------------------------------

    def update_pinned_tabs_workspace(self, old_workspace_uuid: str,
                                      new_workspace_uuid: str) -> bool:
        """
        Remap bookmarks from a temporary workspace UUID to the final one.
        (Original updated zen_pins; now updates zen_bookmarks_workspaces.)
        """
        try:
            now = int(datetime.now().timestamp() * 1000)
            with sqlite3.connect(self.places_db) as conn:
                conn.execute(
                    """UPDATE zen_bookmarks_workspaces
                       SET workspace_uuid = ?, updated_at = ?
                       WHERE workspace_uuid = ?""",
                    (new_workspace_uuid, now, old_workspace_uuid)
                )
                conn.commit()
            logger.info(
                f"📌 Updated bookmarks from {old_workspace_uuid} "
                f"to {new_workspace_uuid}"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to update bookmarks workspace: {e}"
            )
            return False

    # ------------------------------------------------------------------
    # Active workspace  (unchanged — already used prefs.js)
    # ------------------------------------------------------------------

    def set_active_workspace(self, workspace_uuid: str) -> bool:
        """Set the active workspace in prefs.js."""
        try:
            content = self.prefs_file.read_text(encoding='utf-8')
            pattern = r'user_pref\("zen\.workspaces\.active", "[^"]*"\)'
            replacement = (
                f'user_pref("zen.workspaces.active", "{workspace_uuid}")'
            )
            if re.search(pattern, content):
                new_content = re.sub(pattern, replacement, content)
            else:
                new_content = (
                    content.rstrip()
                    + f'\nuser_pref("zen.workspaces.active", "{workspace_uuid}");\n'
                )
            self.prefs_file.write_text(new_content, encoding='utf-8')
            logger.info(f"🎯 Set active workspace to: {workspace_uuid}")
            return True
        except Exception as e:
            logger.error(f"Failed to set active workspace: {e}")
            return False

    # ------------------------------------------------------------------
    # Main import entry point
    # ------------------------------------------------------------------

    def import_arc_workspaces(self, arc_export_data: Dict,
                               container_mappings: Dict[str, int],
                               workspace_mappings: Optional[Dict[str, str]] = None,
                               dry_run: bool = False) -> bool:
        """
        Import Arc spaces as Zen workspaces (written to prefs.js).

        If workspace_mappings is provided (temporary UUIDs from the pinned-tab
        importer), remaps those UUIDs to the final ones in
        zen_bookmarks_workspaces.
        """
        try:
            logger.info("🏗️ Creating Zen workspaces for Arc spaces...")

            if dry_run:
                logger.info("🧪 DRY RUN - No changes will be made")
                return True

            existing_workspaces = self.get_existing_workspaces()
            logger.info(f"Found {len(existing_workspaces)} existing workspaces")

            final_workspace_mappings: Dict[str, str] = {}
            position = 1000

            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                space_icon = space.get('icon')
                space_color = space.get('color')
                container_id = container_mappings.get(space_name, 1)

                # Check if a workspace with this name already exists
                existing_uuid = next(
                    (uid for uid, info in existing_workspaces.items()
                     if info['name'] == space_name),
                    None
                )

                if existing_uuid:
                    logger.info(f"  ✅ Using existing workspace: {space_name}")
                    final_workspace_mappings[space_name] = existing_uuid
                    if space_icon or space_color:
                        self.update_workspace_icon_and_color(
                            existing_uuid, space_icon, space_color
                        )
                else:
                    ws_uuid = self.create_workspace(
                        space_name, container_id, position,
                        space_icon, space_color
                    )
                    if ws_uuid:
                        final_workspace_mappings[space_name] = ws_uuid
                        position += 100
                    else:
                        logger.warning(
                            f"  ❌ Failed to create workspace for: {space_name}"
                        )

            # Remap temporary workspace UUIDs → final UUIDs in
            # zen_bookmarks_workspaces
            if workspace_mappings:
                for space_name, temp_uuid in workspace_mappings.items():
                    final_uuid = final_workspace_mappings.get(space_name)
                    if final_uuid and temp_uuid != final_uuid:
                        logger.info(
                            f"  📌 Remapping {temp_uuid} → {final_uuid} "
                            f"({space_name})"
                        )
                        self.update_pinned_tabs_workspace(temp_uuid, final_uuid)
            else:
                # Fallback: find any orphaned temp UUIDs in zen_bookmarks_workspaces
                try:
                    with sqlite3.connect(self.places_db) as conn:
                        known = set(final_workspace_mappings.values())
                        known |= set(existing_workspaces.keys())
                        cur = conn.execute(
                            "SELECT DISTINCT workspace_uuid "
                            "FROM zen_bookmarks_workspaces"
                        )
                        for (temp_uuid,) in cur.fetchall():
                            if temp_uuid not in known:
                                # Try to infer the space by name match
                                for space_name, final_uuid in (
                                    final_workspace_mappings.items()
                                ):
                                    if temp_uuid != final_uuid:
                                        self.update_pinned_tabs_workspace(
                                            temp_uuid, final_uuid
                                        )
                                        break
                except Exception as e:
                    logger.warning(f"Fallback UUID remap failed: {e}")

            # Set first workspace as active
            if final_workspace_mappings:
                first_uuid = next(iter(final_workspace_mappings.values()))
                self.set_active_workspace(first_uuid)

            logger.info(
                f"✅ Successfully created/found "
                f"{len(final_workspace_mappings)} workspaces"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to import Arc workspaces: {e}")
            return False

    # ------------------------------------------------------------------
    # Cleanup helper  (updated to remove from prefs.js)
    # ------------------------------------------------------------------

    def clear_temporary_workspaces(self) -> bool:
        """
        Remove import-generated workspaces from prefs.js.
        (Original deleted from zen_workspaces table.)
        """
        try:
            workspaces = self._read_workspaces_from_prefs()
            filtered = [
                ws for ws in workspaces
                if not (
                    ws.get('name', '').startswith('Arc Import')
                    or ws.get('name', '').startswith('Temporary')
                )
            ]
            removed = len(workspaces) - len(filtered)
            if removed:
                self._write_workspaces_to_prefs(filtered)
                logger.info(f"🧹 Cleared {removed} temporary workspaces")
            return True
        except Exception as e:
            logger.error(f"Failed to clear temporary workspaces: {e}")
            return False

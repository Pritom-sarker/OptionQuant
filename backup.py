"""
Backup & Restore — bundles Tab 1/3/6 settings (app_settings.json) and the
complete trade database (tab3_trades.db) into a single zip file.

Export ships both files exactly as they are on disk, unchanged — no
row-by-row JSON serialization of the database. That's deliberate: import
writes those same bytes straight back to disk, so importing a backup and
immediately exporting again reproduces byte-identical files. A row-by-row
JSON round trip would risk subtly different output (float formatting,
column ordering, NULL handling) even when the data is "the same" — copying
the raw files sidesteps that entirely.
"""
from __future__ import annotations
import io
import os
import zipfile

import config
import engine_state
import trade_db

SETTINGS_ARCNAME = "app_settings.json"
DB_ARCNAME = "tab3_trades.db"


_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)   # zipfile.write() embeds each entry's mtime by default, which
                                            # would make two exports of identical content differ in bytes
                                            # if they happen to straddle a timestamp tick — writing with a
                                            # fixed date instead makes the zip depend only on file content.


def _write_entry(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname, date_time=_FIXED_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def export_backup() -> bytes:
    """Zip of the live settings file + the live SQLite DB file, exactly as
    they are on disk right now. Byte-for-byte deterministic given the same
    file contents — see _FIXED_ZIP_DATE."""
    engine_state.save_settings()   # make sure the on-disk file reflects current in-memory settings first
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(engine_state.SETTINGS_PATH):
            with open(engine_state.SETTINGS_PATH, "rb") as f:
                _write_entry(zf, SETTINGS_ARCNAME, f.read())
        if os.path.exists(config.TAB3_DB_PATH):
            with open(config.TAB3_DB_PATH, "rb") as f:
                _write_entry(zf, DB_ARCNAME, f.read())
    return buf.getvalue()


def import_backup(zip_bytes: bytes) -> None:
    """
    Overwrites the live settings file and DB file with whatever's in the
    zip (either one is optional — a backup missing a piece just leaves that
    piece untouched), then reloads engine_state.state's in-memory settings
    from the freshly-written file so the already-running process picks up
    the change without a restart. Also clears state.tab3_slots — the
    in-memory active positions would otherwise reference row ids from the
    database that just got replaced wholesale.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        if DB_ARCNAME in names:
            with open(config.TAB3_DB_PATH, "wb") as f:
                f.write(zf.read(DB_ARCNAME))
        if SETTINGS_ARCNAME in names:
            with open(engine_state.SETTINGS_PATH, "wb") as f:
                f.write(zf.read(SETTINGS_ARCNAME))

    with engine_state.state.lock:
        engine_state.load_settings_from_disk(engine_state.state)
        engine_state.state.tab3_slots = []

    trade_db.get_connection().close()   # touch the new file once now so a broken zip fails loudly here, not on the next tick

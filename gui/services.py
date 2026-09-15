"""Thin, UI-independent adapters around the existing WeChatMsg modules."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


ProgressCallback = Callable[[float, str], None]


class GuiServiceError(RuntimeError):
    """An error safe to present directly to a desktop user."""


@dataclass(frozen=True)
class AccountInfo:
    wxid: str
    nickname: str
    source_dir: str
    key: str
    version: str = ""
    pid: int = 0
    errcode: int = 0

    @property
    def display_name(self) -> str:
        suffix = f" — WeChat {self.version}" if self.version else ""
        return f"{self.nickname or self.wxid} ({self.wxid}){suffix}"

    @property
    def key_found(self) -> bool:
        return bool(self.key)

    @property
    def diagnostic_summary(self) -> str:
        key_status = "FOUND" if self.key_found else "NOT FOUND"
        fallback = "" if self.key_found else " (per-database Config.Cipher fallback available on Prepare)"
        return "\n".join(
            (
                f"Internal wxid: {self.wxid or 'unknown'}",
                f"Nickname: {self.nickname or 'not detected'}",
                f"WeChat version: {self.version or 'unknown'}",
                f"PID: {self.pid or 'unknown'}",
                f"Data folder: {self.source_dir or 'not detected'}",
                f"Legacy database key: {key_status}{fallback}",
            )
        )


@dataclass(frozen=True)
class PreparedDatabase:
    db_dir: str
    wxid: str = ""


def _adapt_accounts(detected) -> list[AccountInfo]:
    return [
        AccountInfo(
            wxid=getattr(item, "wxid", "") or "",
            nickname=getattr(item, "nick_name", "") or "",
            source_dir=getattr(item, "wx_dir", "") or "",
            key=getattr(item, "key", "") or "",
            version=str(getattr(item, "version", "") or ""),
            pid=int(getattr(item, "pid", 0) or 0),
            errcode=int(getattr(item, "errcode", 0) or 0),
        )
        for item in detected
        if item is not None
    ]


def detect_accounts(info_provider=None) -> list[AccountInfo]:
    """Detect running WeChat 4.x accounts without persisting their keys."""
    if info_provider is None:
        try:
            from wxManager.decrypt import get_info_v4
        except Exception as exc:
            raise GuiServiceError(
                "WeChat 4.x detection is available only on Windows with the project dependencies installed."
            ) from exc
        info_provider = get_info_v4

    try:
        detected = info_provider()
    except Exception as exc:
        raise GuiServiceError(
            "WeChat account detection failed before a usable account could be returned."
        ) from exc
    if not detected:
        raise GuiServiceError("No WeChat 4.x account was detected. Please start and sign in to WeChat first.")

    return _adapt_accounts(detected)


def detect_accounts_detailed() -> tuple[list[AccountInfo], list[str]]:
    """Detect accounts and return copyable, key-safe process diagnostics for the GUI."""
    try:
        from wxManager.decrypt import get_info_v4_diagnostics
    except Exception as exc:
        raise GuiServiceError(
            "WeChat 4.x detection is available only on Windows with the project dependencies installed."
        ) from exc

    try:
        detected, diagnostics = get_info_v4_diagnostics()
    except Exception as exc:
        raise GuiServiceError(
            f"WeChat account detection failed unexpectedly ({type(exc).__name__})."
        ) from exc

    accounts = _adapt_accounts(detected)
    if not accounts:
        details = "\n".join(diagnostics[-12:]) if diagnostics else "No process diagnostics were produced."
        raise GuiServiceError(
            "No usable WeChat 4.x account was detected.\n\nDetection details:\n" + details
        )
    return accounts, diagnostics


def prepare_database(
    account: AccountInfo,
    workspace: str,
    progress: ProgressCallback | None = None,
    decryptor=None,
    xor_key_provider=None,
    per_db_key_provider=None,
) -> PreparedDatabase:
    """Decrypt into a new workspace; the original database is never opened for writing.

    Older WeChat 4.x builds use the legacy master-key path.  When that key is not
    available (notably 4.1.13+), a read-only Config.Cipher scan obtains and
    validates per-database raw keys in memory.  Key material is never saved to
    ``info.json`` or anywhere else by this service.
    """
    if not account.source_dir or not Path(account.source_dir).is_dir():
        raise GuiServiceError("The detected WeChat database directory no longer exists.")
    if not workspace:
        raise GuiServiceError("Choose a working directory for the prepared database.")

    destination_root = Path(workspace).expanduser().resolve() / (account.wxid or "wechat_account")
    db_dir = destination_root / "db_storage"
    source_root = Path(account.source_dir).resolve()
    if destination_root == source_root or source_root in destination_root.parents:
        raise GuiServiceError("Choose a working directory outside the original WeChat data directory.")

    if decryptor is None:
        from wxManager.decrypt.decrypt_v4 import decrypt_db_files
        decryptor = decrypt_db_files
    if xor_key_provider is None:
        from wxManager.decrypt.decrypt_dat import get_decode_code_v4
        xor_key_provider = get_decode_code_v4

    key_material = account.key
    key_mode = "legacy master key"
    if not key_material:
        if not account.pid:
            raise GuiServiceError(
                "The account was detected but its Weixin.exe PID is unavailable, so the 4.1.13+ key scan cannot run."
            )
        if per_db_key_provider is None:
            from wxManager.decrypt.keyscan_v413 import scan_and_match_keys
            per_db_key_provider = scan_and_match_keys
        if progress:
            progress(0.05, "Legacy key unavailable; using the WeChat 4.1.13+ Config.Cipher key scan…")
        try:
            key_material = per_db_key_provider(account.pid, account.source_dir, progress)
        except Exception as exc:
            raise GuiServiceError(
                "The WeChat 4.1.13+ per-database key scan failed. Keep WeChat logged in and try running this app as administrator."
            ) from exc
        if not key_material:
            raise GuiServiceError(
                "The account and data folder were detected, but no valid per-database keys could be matched. "
                "This WeChat build may use a newer Config.Cipher layout."
            )
        key_mode = f"per-database Config.Cipher keys ({len(key_material)} matched)"

    if progress:
        progress(0.66 if not account.key else 0.05, f"Preparing a read-only database copy using {key_mode}…")

    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        xor_key = xor_key_provider(account.source_dir)
        stats = decryptor(key_material, src_dir=account.source_dir, dest_dir=str(destination_root))
        if not db_dir.is_dir():
            raise RuntimeError("The expected db_storage directory was not created")

        # These are the minimum pieces needed to show contacts and conversations.
        required = [db_dir / "contact" / "contact.db"]
        if not all(path.is_file() for path in required):
            raise RuntimeError("The contact database could not be prepared")

        info = {
            "username": account.wxid,
            "nickname": account.nickname,
            "wx_dir": account.source_dir,
            "xor_key": xor_key,
        }
        with (db_dir / "info.json").open("w", encoding="utf-8") as stream:
            json.dump(info, stream, ensure_ascii=False, indent=4)
    except GuiServiceError:
        raise
    except Exception as exc:
        raise GuiServiceError(
            "Database preparation failed after key discovery. The original WeChat data was not changed."
        ) from exc

    if progress:
        if isinstance(stats, dict) and stats.get("unmatched"):
            progress(
                1.0,
                f"Database prepared; {len(stats['unmatched'])} nonessential encrypted DB(s) had no matched key.",
            )
        else:
            progress(1.0, "Database is ready.")
    return PreparedDatabase(str(db_dir), account.wxid)


def use_prepared_database(db_dir: str) -> PreparedDatabase:
    path = Path(db_dir).expanduser().resolve()
    if not path.is_dir() or not (path / "info.json").is_file():
        raise GuiServiceError("Select a prepared db_storage directory containing info.json.")
    return PreparedDatabase(str(path))


def open_database(prepared: PreparedDatabase, connection_factory=None):
    if connection_factory is None:
        from wxManager import DatabaseConnection
        connection_factory = DatabaseConnection
    try:
        connection = connection_factory(prepared.db_dir, 4)
        database = getattr(connection, "database_interface", None)
        if database is None:
            database = connection.get_interface()
        if database is None:
            raise RuntimeError("Database initialization returned no interface")
        return database
    except Exception as exc:
        raise GuiServiceError("The prepared database could not be opened.") from exc


def load_contacts(database) -> list:
    try:
        return list(database.get_contacts())
    except Exception as exc:
        raise GuiServiceError("Contacts could not be loaded from the prepared database.") from exc


def filter_contacts(contacts: Iterable, query: str) -> list:
    needle = query.strip().casefold()
    if not needle:
        return list(contacts)
    fields = ("nickname", "remark", "wxid", "alias")
    return [
        contact for contact in contacts
        if any(needle in str(getattr(contact, field, "") or "").casefold() for field in fields)
    ]


def validate_date_range(start_date: str, end_date: str) -> list[str]:
    try:
        start = datetime.strptime(start_date.strip(), "%Y-%m-%d")
        end = datetime.strptime(end_date.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise GuiServiceError("Enter dates in YYYY-MM-DD format.") from exc
    if start > end:
        raise GuiServiceError("The start date must not be later than the end date.")
    return [f"{start:%Y-%m-%d} 00:00:00", f"{end:%Y-%m-%d} 23:59:59"]


def export_contact(
    database,
    contact,
    formats: Iterable[str],
    output_dir: str,
    start_date: str,
    end_date: str,
    progress: ProgressCallback | None = None,
    exporter_classes: dict[str, type] | None = None,
) -> str:
    selected = [value.upper() for value in formats]
    if not contact:
        raise GuiServiceError("No contact is selected.")
    if not selected:
        raise GuiServiceError("Choose HTML, DOCX, or both.")
    if not output_dir:
        raise GuiServiceError("Choose an output directory.")
    time_range = validate_date_range(start_date, end_date)
    Path(output_dir).expanduser().mkdir(parents=True, exist_ok=True)

    if exporter_classes is None:
        from exporter import DocxExporter, HtmlExporter
        from exporter.config import FileType
        exporter_classes = {
            "HTML": (HtmlExporter, FileType.HTML),
            "DOCX": (DocxExporter, FileType.DOCX),
        }
    else:
        exporter_classes = {key: (value, key) for key, value in exporter_classes.items()}

    unknown = set(selected) - set(exporter_classes)
    if unknown:
        raise GuiServiceError(f"Unsupported export format: {', '.join(sorted(unknown))}")

    try:
        for index, name in enumerate(selected):
            exporter_class, file_type = exporter_classes[name]
            base = index / len(selected)
            span = 1 / len(selected)

            def on_progress(value, label=name, base=base, span=span):
                if progress:
                    progress(base + max(0.0, min(float(value), 1.0)) * span, f"Exporting {label}…")

            exporter = exporter_class(
                database,
                contact,
                output_dir=str(Path(output_dir).expanduser().resolve()),
                type_=file_type,
                message_types=None,
                time_range=time_range,
                group_members=None,
                progress_callback=on_progress,
                finish_callback=lambda _result: None,
            )
            exporter.start()
    except Exception as exc:
        raise GuiServiceError("Export failed. No original WeChat data was changed.") from exc

    if progress:
        progress(1.0, "Export completed.")
    return str(Path(output_dir).expanduser().resolve())


def open_folder(path: str) -> None:
    if os.name != "nt":
        raise GuiServiceError("Opening folders from the application is supported on Windows.")
    try:
        os.startfile(str(Path(path).resolve()))  # type: ignore[attr-defined]
    except OSError as exc:
        raise GuiServiceError("The output folder could not be opened.") from exc

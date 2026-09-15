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

    @property
    def display_name(self) -> str:
        return f"{self.nickname or self.wxid} ({self.wxid})"


@dataclass(frozen=True)
class PreparedDatabase:
    db_dir: str
    wxid: str = ""


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
            "WeChat account detection failed. Make sure WeChat 4.x is running, then try again."
        ) from exc
    if not detected:
        raise GuiServiceError("No WeChat 4.x account was detected. Please start and sign in to WeChat first.")

    accounts = [
        AccountInfo(
            wxid=getattr(item, "wxid", "") or "",
            nickname=getattr(item, "nick_name", "") or "",
            source_dir=getattr(item, "wx_dir", "") or "",
            key=getattr(item, "key", "") or "",
        )
        for item in detected
    ]
    return accounts


def prepare_database(
    account: AccountInfo,
    workspace: str,
    progress: ProgressCallback | None = None,
    decryptor=None,
    xor_key_provider=None,
) -> PreparedDatabase:
    """Decrypt into a new workspace; the original database is never opened for writing."""
    if not account.key:
        raise GuiServiceError("No database key was found. Restart WeChat and try detection again.")
    if not account.source_dir or not Path(account.source_dir).is_dir():
        raise GuiServiceError("The detected WeChat database directory no longer exists.")
    if not workspace:
        raise GuiServiceError("Choose a working directory for the prepared database.")

    destination_root = Path(workspace).expanduser().resolve() / (account.wxid or "wechat_account")
    db_dir = destination_root / "db_storage"
    source_root = Path(account.source_dir).resolve()
    if destination_root == source_root or source_root in destination_root.parents:
        raise GuiServiceError("Choose a working directory outside the original WeChat data directory.")
    if progress:
        progress(0.05, "Preparing a read-only copy of the WeChat database…")

    if decryptor is None:
        from wxManager.decrypt.decrypt_v4 import decrypt_db_files
        decryptor = decrypt_db_files
    if xor_key_provider is None:
        from wxManager.decrypt.decrypt_dat import get_decode_code_v4
        xor_key_provider = get_decode_code_v4

    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        xor_key = xor_key_provider(account.source_dir)
        decryptor(account.key, src_dir=account.source_dir, dest_dir=str(destination_root))
        if not db_dir.is_dir():
            raise RuntimeError("The expected db_storage directory was not created")
        info = {
            "username": account.wxid,
            "nickname": account.nickname,
            "wx_dir": account.source_dir,
            "xor_key": xor_key,
        }
        with (db_dir / "info.json").open("w", encoding="utf-8") as stream:
            json.dump(info, stream, ensure_ascii=False, indent=4)
    except Exception as exc:
        raise GuiServiceError("Database preparation failed. Check the working directory and try again.") from exc

    if progress:
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

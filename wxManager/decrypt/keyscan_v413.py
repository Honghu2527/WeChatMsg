"""Read-only per-database key discovery for recent WeChat 4.1.x builds.

WeChat 4.1.13+ no longer reliably exposes the single legacy master key used by
this project.  Recent WCDB builds keep per-database raw SQLCipher keys in
``com.Tencent.WCDB.Config.Cipher`` objects inside Weixin.exe.  This module scans
that process memory read-only, validates candidates against local database page
1 HMACs, and returns keys only in memory.

The Config.Cipher layout/decoding strategy is independently adapted from the
Apache-2.0 project ``fanyuantaier/wechatauto-replica``.  No key material is
logged or persisted by this module.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import re
import struct
from ctypes import wintypes
from pathlib import Path
from typing import Callable

PAGE_SIZE = 4096
RESERVE_SIZE = 80  # IV(16) + HMAC-SHA512(64)
CONFIG_CIPHER_NAME = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_XOR_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
HEX_LITERAL_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100

ProgressCallback = Callable[[float, str], None]


class _MBI(ctypes.Structure):
    # Windows x64 MEMORY_BASIC_INFORMATION layout.  The explicit alignment
    # DWORDs keep RegionSize at the correct offset when Python is 64-bit.
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]


def _probable_key(value: bytes) -> bool:
    return (
        len(value) == 32
        and len(set(value)) >= 15
        and value not in {b"\x00" * 32, b"\xff" * 32}
    )


def verify_raw_key(key: bytes, page1: bytes, salt: bytes | None = None) -> bool:
    """Validate a raw SQLCipher-4 key against the first encrypted DB page."""
    if len(key) != 32 or len(page1) < PAGE_SIZE:
        return False
    if salt is None:
        salt = page1[:16]
    if len(salt) != 16:
        return False

    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=32)
    payload = page1[16 : PAGE_SIZE - RESERVE_SIZE + 16]
    expected = page1[PAGE_SIZE - 64 : PAGE_SIZE]
    digest = hmac.new(mac_key, payload, hashlib.sha512)
    digest.update(struct.pack("<I", 1))
    return hmac.compare_digest(digest.digest(), expected)


def _kernel32():
    if os.name != "nt":
        raise RuntimeError("WeChat process-memory scanning is available only on Windows.")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(_MBI),
        ctypes.c_size_t,
    ]
    dll.VirtualQueryEx.restype = ctypes.c_size_t
    dll.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    return dll


def _make_reader(kernel32, handle):
    def read(address: int, size: int) -> bytes | None:
        if size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        read_count = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(read_count),
        )
        if not ok or not read_count.value:
            return None
        return buffer.raw[: read_count.value]

    return read


def _find_bytes(kernel32, handle, read, needle: bytes) -> list[int]:
    hits: list[int] = []
    address = 0
    while True:
        mbi = _MBI()
        result = kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if result == 0:
            break

        base = int(mbi.BaseAddress or 0)
        region_size = int(mbi.RegionSize or 0)
        protection = int(mbi.Protect or 0)
        # The 0xE6 mask covers the normal readable PAGE_* protection flags used
        # by Weixin.exe; PAGE_GUARD regions are intentionally skipped.
        if (
            mbi.State == MEM_COMMIT
            and (protection & 0xFF) & 0xE6
            and not (protection & PAGE_GUARD)
            and 0 < region_size < 0x10000000
        ):
            data = read(base, region_size)
            if data:
                pos = 0
                while True:
                    pos = data.find(needle, pos)
                    if pos < 0:
                        break
                    hits.append(base + pos)
                    pos += 1

        next_address = base + region_size
        if next_address <= address:
            break
        address = next_address
    return hits


def _candidate_material_for_pid(pid: int) -> set[tuple[bytes, bytes | None]]:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return set()

    candidates: set[tuple[bytes, bytes | None]] = set()
    seen_keys: set[bytes] = set()
    try:
        read = _make_reader(kernel32, handle)
        name_addresses = _find_bytes(kernel32, handle, read, CONFIG_CIPHER_NAME)
        pointer_pairs = [
            struct.pack("<Q", address) + struct.pack("<Q", len(CONFIG_CIPHER_NAME))
            for address in name_addresses
        ]

        for pair in pointer_pairs:
            for pair_address in _find_bytes(kernel32, handle, read, pair):
                node = read(pair_address - 0x10, 0x50)
                if not node or len(node) < 0x40:
                    continue
                if struct.unpack_from("<Q", node, 0x10)[0] not in name_addresses:
                    continue
                if struct.unpack_from("<Q", node, 0x18)[0] != len(CONFIG_CIPHER_NAME):
                    continue

                config_ptr = struct.unpack_from("<Q", node, 0x28)[0]
                if not (0x10000 <= config_ptr < 0x800000000000):
                    continue
                obj = read(config_ptr + 0x88, 0x28)
                if not obj or len(obj) < 0x18:
                    continue

                data_ptr = struct.unpack_from("<Q", obj, 0x8)[0]
                data_len = struct.unpack_from("<Q", obj, 0x10)[0]
                if not (0 < data_len <= 1024 and 0x10000 <= data_ptr < 0x800000000000):
                    continue
                blob = read(data_ptr, int(data_len))
                if not blob or len(blob) != data_len:
                    continue

                decoded = bytes(
                    value ^ CONFIG_XOR_MASK[index % len(CONFIG_XOR_MASK)]
                    for index, value in enumerate(blob)
                )
                for match in HEX_LITERAL_RE.finditer(decoded):
                    run = match.group(1).decode("ascii").lower()
                    starts = [0]
                    if len(run) > 96:
                        starts += list(range(0, len(run) - 63, 32))
                        starts.append(len(run) - 64)
                    for start in dict.fromkeys(starts):
                        if start + 64 > len(run):
                            continue
                        key = bytes.fromhex(run[start : start + 64])
                        if key in seen_keys or not _probable_key(key):
                            continue
                        seen_keys.add(key)
                        candidates.add((key, None))
                        if start + 96 <= len(run):
                            explicit_salt = bytes.fromhex(run[start + 64 : start + 96])
                            candidates.add((key, explicit_salt))
    finally:
        kernel32.CloseHandle(handle)
    return candidates


def _database_files(account_dir: str) -> list[tuple[str, Path]]:
    account_root = Path(account_dir).resolve()
    storage = account_root / "db_storage"
    if not storage.is_dir():
        return []

    result: list[tuple[str, Path]] = []
    for path in storage.rglob("*.db"):
        if not path.is_file():
            continue
        relative_to_storage = path.relative_to(storage)
        if relative_to_storage.parts and relative_to_storage.parts[0].lower() == "migrate":
            continue
        relative = path.relative_to(account_root).as_posix()
        result.append((relative, path))
    return result


def _match_candidates(
    candidates: set[tuple[bytes, bytes | None]], db_files: list[tuple[str, Path]]
) -> dict[str, bytes]:
    matched: dict[str, bytes] = {}
    pages: dict[str, bytes] = {}
    for relative, path in db_files:
        try:
            with path.open("rb") as stream:
                pages[relative] = stream.read(PAGE_SIZE)
        except OSError:
            continue

    for key, explicit_salt in candidates:
        for relative, page1 in pages.items():
            if relative in matched:
                continue
            if verify_raw_key(key, page1, explicit_salt):
                matched[relative] = key + (explicit_salt or b"")
                break
    return matched


def _other_weixin_pids(preferred_pid: int) -> list[int]:
    pids: list[int] = []
    try:
        import psutil

        for process in psutil.process_iter(["name", "pid"]):
            try:
                if (process.info.get("name") or "").lower() == "weixin.exe":
                    pid = int(process.info.get("pid") or 0)
                    if pid and pid != preferred_pid:
                        pids.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return pids


def scan_and_match_keys(
    pid: int,
    account_dir: str,
    progress: ProgressCallback | None = None,
) -> dict[str, bytes]:
    """Return an in-memory map ``relative_db_path -> raw key bytes``.

    The detected main PID is tried first.  If it yields no usable keys, other
    Weixin.exe processes are scanned as a fallback.  Nothing is written to the
    WeChat directory and the returned key map is never persisted by this module.
    """
    db_files = _database_files(account_dir)
    if not db_files:
        raise RuntimeError("No .db files were found under the detected db_storage directory.")

    if progress:
        progress(0.10, f"Found {len(db_files)} local databases; scanning WeChat memory for 4.1.13+ keys…")

    pids = [pid] if pid else []
    pids.extend(_other_weixin_pids(pid))
    all_candidates: set[tuple[bytes, bytes | None]] = set()
    matched: dict[str, bytes] = {}

    for index, process_pid in enumerate(dict.fromkeys(pids)):
        if progress:
            progress(
                0.15 + 0.45 * (index / max(1, len(pids))),
                f"Scanning Weixin.exe PID {process_pid} for Config.Cipher keys…",
            )
        try:
            all_candidates |= _candidate_material_for_pid(process_pid)
        except Exception:
            continue
        matched = _match_candidates(all_candidates, db_files)
        if matched:
            # One main process normally contains the complete active-account key set.
            # Keep scanning only when very few DBs matched, because recent WeChat can
            # split state across processes.
            if len(matched) >= max(3, len(db_files) // 3):
                break

    if progress:
        progress(
            0.62,
            f"Validated {len(matched)} of {len(db_files)} database keys from {len(all_candidates)} candidates.",
        )
    return matched

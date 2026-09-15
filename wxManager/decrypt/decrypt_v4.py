import hashlib
import hmac
import os
import shutil
import struct
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Hash import SHA512

# Constants
IV_SIZE = 16
HMAC_SHA256_SIZE = 64
KEY_SIZE = 32
AES_BLOCK_SIZE = 16
ROUND_COUNT = 256000
PAGE_SIZE = 4096
SALT_SIZE = 16
RESERVE_SIZE = 80
SQLITE_HEADER = b"SQLite format 3"


def _verify_raw_page(raw_key, page, page_number, salt):
    if len(page) != PAGE_SIZE or len(raw_key) != 32 or len(salt) != 16:
        return False
    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", raw_key, mac_salt, 2, dklen=32)
    offset = SALT_SIZE if page_number == 1 else 0
    payload = page[offset : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    expected = page[PAGE_SIZE - HMAC_SHA256_SIZE : PAGE_SIZE]
    digest = hmac.new(mac_key, payload, hashlib.sha512)
    digest.update(struct.pack("<I", page_number))
    return hmac.compare_digest(digest.digest(), expected)


def _decrypt_raw_db_file(raw_material: bytes, in_db_path, out_db_path):
    """Decrypt a DB using a raw per-database SQLCipher key.

    ``raw_material`` is either 32 bytes (raw AES key; salt is DB page-1 prefix)
    or 48 bytes (32-byte key + explicit 16-byte salt for plaintext-header mode).
    """
    if len(raw_material) not in (32, 48):
        return False
    raw_key = raw_material[:32]
    explicit_salt = raw_material[32:] if len(raw_material) == 48 else None

    try:
        with open(in_db_path, "rb") as source:
            first_page = source.read(PAGE_SIZE)
            if len(first_page) != PAGE_SIZE:
                return False
            salt = explicit_salt or first_page[:SALT_SIZE]
            if not _verify_raw_page(raw_key, first_page, 1, salt):
                return False

            os.makedirs(os.path.dirname(out_db_path), exist_ok=True)
            with open(out_db_path, "wb") as target:
                page_number = 1
                page = first_page
                while page:
                    if len(page) != PAGE_SIZE:
                        return False
                    if not _verify_raw_page(raw_key, page, page_number, salt):
                        return False

                    iv = page[PAGE_SIZE - RESERVE_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
                    offset = SALT_SIZE if page_number == 1 else 0
                    encrypted = page[offset : PAGE_SIZE - RESERVE_SIZE]
                    decrypted = AES.new(raw_key, AES.MODE_CBC, iv).decrypt(encrypted)

                    if page_number == 1:
                        prefix = page[:SALT_SIZE] if explicit_salt else SQLITE_HEADER + b"\x00"
                        target.write(prefix)
                    target.write(decrypted)
                    target.write(b"\x00" * RESERVE_SIZE)

                    page_number += 1
                    page = source.read(PAGE_SIZE)
    except OSError:
        return False
    return True


def _decrypt_legacy_db_file(pkey, in_db_path, out_db_path):
    """Original WeChatMsg master-passphrase decryption path for older builds."""
    if not os.path.exists(in_db_path):
        print(f"【!!!】{in_db_path} does not exist.")
        return False

    with open(in_db_path, 'rb') as f_in, open(out_db_path, 'wb') as f_out:
        salt = f_in.read(SALT_SIZE)
        if not salt:
            print("File is empty or corrupted.")
            return False

        mac_salt = bytes(x ^ 0x3a for x in salt)
        passphrase = bytes.fromhex(pkey)
        key = PBKDF2(passphrase, salt, dkLen=KEY_SIZE, count=ROUND_COUNT, hmac_hash_module=SHA512)
        mac_key = PBKDF2(key, mac_salt, dkLen=KEY_SIZE, count=2, hmac_hash_module=SHA512)

        f_out.write(SQLITE_HEADER)
        f_out.write(b'\x00')
        reserve = IV_SIZE + HMAC_SHA256_SIZE
        reserve = ((reserve + AES_BLOCK_SIZE - 1) // AES_BLOCK_SIZE) * AES_BLOCK_SIZE

        cur_page = 0
        while True:
            if cur_page == 0:
                page = f_in.read(PAGE_SIZE - SALT_SIZE)
                if not page:
                    break
                page = salt + page
            else:
                page = f_in.read(PAGE_SIZE)
            if not page:
                break
            offset = SALT_SIZE if cur_page == 0 else 0
            end = len(page)

            if all(x == 0 for x in page):
                f_out.write(page)
                break

            mac = hmac.new(mac_key, page[offset:end - reserve + IV_SIZE], hashlib.sha512)
            mac.update(struct.pack('<I', cur_page + 1))
            hash_mac = mac.digest()
            hash_mac_start_offset = end - reserve + IV_SIZE
            if hash_mac != page[hash_mac_start_offset:hash_mac_start_offset + len(hash_mac)]:
                return False

            iv = page[end - reserve:end - reserve + IV_SIZE]
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted_data = cipher.decrypt(page[offset:end - reserve])
            f_out.write(decrypted_data)
            f_out.write(page[end - reserve:end])
            cur_page += 1

    return True


def decrypt_db_file_v4(key_material, in_db_path, out_db_path):
    if isinstance(key_material, (bytes, bytearray)):
        return _decrypt_raw_db_file(bytes(key_material), in_db_path, out_db_path)
    return _decrypt_legacy_db_file(key_material, in_db_path, out_db_path)


def decode_wrapper(task):
    return decrypt_db_file_v4(*task)


def _normalise_relative(path: str) -> str:
    return Path(path).as_posix().lstrip("./")


def decrypt_db_files(key, src_dir: str, dest_dir: str):
    """Decrypt all DBs into a separate destination.

    ``key`` may be the legacy master-key hex string or, for WeChat 4.1.13+, a
    mapping of relative DB paths to 32/48-byte raw per-database key material.
    The source tree is read-only.
    """
    if not os.path.exists(src_dir):
        raise FileNotFoundError(f"Source folder does not exist: {src_dir}")

    os.makedirs(dest_dir, exist_ok=True)
    per_database = isinstance(key, dict)
    key_map = {_normalise_relative(name): value for name, value in key.items()} if per_database else None
    decrypt_tasks = []
    skipped_encrypted = []
    plaintext_copied = 0

    for root, _dirs, files in os.walk(src_dir):
        for filename in files:
            if not filename.endswith(".db"):
                continue
            src_file_path = os.path.join(root, filename)
            relative_dir = os.path.relpath(root, src_dir)
            dest_sub_dir = os.path.join(dest_dir, relative_dir)
            dest_file_path = os.path.join(dest_sub_dir, filename)
            os.makedirs(dest_sub_dir, exist_ok=True)

            selected_key = key
            if per_database:
                relative_file = _normalise_relative(os.path.relpath(src_file_path, src_dir))
                selected_key = key_map.get(relative_file)
                if selected_key is None:
                    try:
                        with open(src_file_path, "rb") as stream:
                            header = stream.read(16)
                    except OSError:
                        header = b""
                    if header == SQLITE_HEADER + b"\x00":
                        shutil.copy2(src_file_path, dest_file_path)
                        plaintext_copied += 1
                    else:
                        skipped_encrypted.append(relative_file)
                    continue

            decrypt_tasks.append((selected_key, src_file_path, dest_file_path))

    failures = []
    if decrypt_tasks:
        workers = min(16, max(1, len(decrypt_tasks)))
        with ProcessPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(decode_wrapper, decrypt_tasks))
        failures = [task[1] for task, ok in zip(decrypt_tasks, results) if not ok]

    if failures:
        raise RuntimeError(f"Failed to decrypt {len(failures)} database file(s).")
    if per_database and not decrypt_tasks:
        raise RuntimeError("No encrypted database could be matched to a valid key.")

    return {
        "decrypted": len(decrypt_tasks),
        "plaintext_copied": plaintext_copied,
        "unmatched": skipped_encrypted,
    }

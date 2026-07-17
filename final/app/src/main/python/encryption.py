"""
encryption.py — AES-256-GCM encryption for local backups/exports.

The key comes from SecureKeyManager.kt on the Kotlin side: a random 256-bit
key generated once, stored ONLY in Keystore-wrapped form (EncryptedFile),
and decrypted in-memory each app start. MainActivity.kt passes the raw hex
key into start_server() as an extra argument - it's never written to disk
in plaintext by either side.

WHAT THIS PROTECTS:
  Data at rest on this device - a phone backup, a copied file off the SD
  card, or someone browsing storage without the Keystore-bound key. Used
  for encrypted backups of contacts.db and any file you explicitly export.

WHAT THIS DOES NOT DO:
  Nothing here sends data anywhere. It doesn't protect against someone
  with root/adb on an unlocked, already-decrypted device - no purely
  in-app-storage encryption scheme can promise that.
"""

import base64
import io
import json
import logging
import os
import zipfile

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

_KEY = None  # raw 32 bytes, set via init()


def init(key_hex):
    global _KEY
    if not key_hex:
        logging.warning("encryption: no key provided - encrypt/decrypt calls will fail until set")
        return
    _KEY = bytes.fromhex(key_hex)
    logging.info("encryption: key loaded (%d bytes)", len(_KEY))


def available():
    return _KEY is not None


def encrypt_bytes(plaintext: bytes) -> dict:
    if not _KEY:
        raise RuntimeError("encryption key not initialized - see SecureKeyManager.kt")
    nonce = get_random_bytes(12)
    cipher = AES.new(_KEY, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "tag": base64.b64encode(tag).decode(),
    }


def decrypt_bytes(payload: dict) -> bytes:
    if not _KEY:
        raise RuntimeError("encryption key not initialized - see SecureKeyManager.kt")
    nonce = base64.b64decode(payload["nonce"])
    ciphertext = base64.b64decode(payload["ciphertext"])
    tag = base64.b64decode(payload["tag"])
    cipher = AES.new(_KEY, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag)


def encrypt_file(src_path, dest_path=None):
    """Encrypts a file in place (or to dest_path if given), writing a JSON
    envelope of {nonce, ciphertext, tag}, all base64. Returns the output path."""
    dest_path = dest_path or (src_path + ".enc")
    with open(src_path, "rb") as f:
        data = f.read()
    payload = encrypt_bytes(data)
    with open(dest_path, "w") as f:
        json.dump(payload, f)
    logging.info("encryption: encrypted %s -> %s (%d bytes)", src_path, dest_path, len(data))
    return dest_path


def decrypt_file(src_path, dest_path=None):
    if dest_path is None:
        dest_path = src_path[:-4] if src_path.endswith(".enc") else src_path + ".dec"
    with open(src_path) as f:
        payload = json.load(f)
    data = decrypt_bytes(payload)
    with open(dest_path, "wb") as f:
        f.write(data)
    logging.info("encryption: decrypted %s -> %s (%d bytes)", src_path, dest_path, len(data))
    return dest_path


def encrypted_backup(db_path, backups_dir):
    """Copies a SQLite DB's current bytes into an encrypted, timestamped
    backup file. Used by the scheduled job below and callable on demand."""
    import time
    os.makedirs(backups_dir, exist_ok=True)
    if not os.path.exists(db_path):
        return {"error": f"no such file: {db_path}"}
    name = os.path.basename(db_path)
    ts = int(time.time())
    dest = os.path.join(backups_dir, f"{name}.{ts}.enc")
    encrypt_file(db_path, dest)
    return {"backup": dest}


def encrypted_full_backup(files_dir, backups_dir):
    """Bundles config.json (which covers every setting AND the live
    theme - see theme.py, all its keys are just config keys) plus the
    entire scripts/ folder into one zip, then encrypts that zip. This
    closes the gap where a reinstall or app-data wipe silently lost
    every saved script and every theme customization, since neither was
    covered by the single-DB encrypted_backup() above. Contacts/usage
    DBs are intentionally NOT duplicated in here - keep using
    encrypted_backup() for those, this is specifically the "everything
    that isn't a database" bundle."""
    import time
    os.makedirs(backups_dir, exist_ok=True)

    buf = io.BytesIO()
    included = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        config_path = os.path.join(files_dir, "config.json")
        if os.path.exists(config_path):
            zf.write(config_path, arcname="config.json")
            included.append("config.json")

        scripts_dir = os.path.join(files_dir, "scripts")
        if os.path.isdir(scripts_dir):
            for fname in sorted(os.listdir(scripts_dir)):
                if fname.endswith(".py"):
                    zf.write(os.path.join(scripts_dir, fname), arcname=f"scripts/{fname}")
                    included.append(f"scripts/{fname}")

    if not included:
        return {"error": "nothing to back up yet (no config.json or scripts/ found)"}

    ts = int(time.time())
    dest = os.path.join(backups_dir, f"full_backup.{ts}.zip.enc")
    payload = encrypt_bytes(buf.getvalue())
    with open(dest, "w") as f:
        json.dump(payload, f)
    logging.info("encryption: full backup -> %s (%d files)", dest, len(included))
    return {"backup": dest, "included": included}


def restore_full_backup(backup_path, files_dir):
    """Decrypts a full_backup.*.zip.enc and extracts it into
    files_dir/restored_backups/<timestamp>/ - deliberately NEVER
    overwrites the live config.json or scripts/ directly, same
    never-silently-overwrite rule the single-file restore route follows.
    Review what's in there and copy back what you actually want via the
    Scripts page or File Explorer."""
    import time
    if not os.path.exists(backup_path):
        return {"error": f"no such backup: {backup_path}"}
    with open(backup_path) as f:
        payload = json.load(f)
    data = decrypt_bytes(payload)

    dest_dir = os.path.join(files_dir, "restored_backups", str(int(time.time())))
    os.makedirs(dest_dir, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_dir)
        extracted = zf.namelist()
    logging.info("encryption: restored full backup %s -> %s (%d files)", backup_path, dest_dir, len(extracted))
    return {"restored_to": dest_dir, "files": extracted}

"""Read-only verification of what was actually written to Bilibili.

Decrypts the B站 cookies from Chrome's cookie store (DPAPI + AES-GCM), then
queries the live B站 favourite-folder API. Strictly read-only: it never calls
create_folder or resource/deal.

Chrome holds an exclusive lock on the Cookies DB, so we copy it to temp first.
"""
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Crypto.Cipher import AES

USER_DATA = os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data")
PROFILE = os.environ.get("CHROME_PROFILE", "Default")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(blob: bytes) -> bytes:
    buf_in = DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                              ctypes.POINTER(ctypes.c_char)))
    buf_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(buf_in), None, None, None, None, 0, ctypes.byref(buf_out)
    ):
        raise RuntimeError("CryptUnprotectData failed")
    return ctypes.string_at(buf_out.pbData, buf_out.cbData)


def get_master_key() -> bytes:
    ls = os.path.join(USER_DATA, "Local State")
    with open(ls, "r", encoding="utf-8") as f:
        state = json.load(f)
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    assert enc[:5] == b"DPAPI", "unexpected key prefix"
    return dpapi_unprotect(enc[5:])


def decrypt_value(enc: bytes, key: bytes) -> str:
    # Chrome v10/v11: "v10" + 12-byte nonce + ciphertext + 16-byte tag
    if enc[:3] in (b"v10", b"v11"):
        nonce, payload = enc[3:15], enc[15:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(payload[:-16], payload[-16:]).decode("utf-8", "replace")
    # legacy DPAPI-only
    return dpapi_unprotect(enc).decode("utf-8", "replace")


def load_bili_cookies() -> dict:
    src = os.path.join(USER_DATA, PROFILE, "Network", "Cookies")
    if not os.path.exists(src):
        src = os.path.join(USER_DATA, PROFILE, "Cookies")
    tmp = os.path.join(tempfile.gettempdir(), "dsh-cookies-copy.db")

    # Chrome holds the DB open; a plain copy fails with WinError 32.
    # Copy the bytes through a shared-read handle instead, then open the copy
    # read-only so SQLite never tries to write to it.
    copied = False
    try:
        shutil.copy2(src, tmp)
        copied = True
    except PermissionError:
        import ctypes
        from ctypes import wintypes
        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
        OPEN_EXISTING = 3
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        h = k32.CreateFileW(src, GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None)
        if h == wintypes.HANDLE(-1).value or h is None:
            raise RuntimeError(f"CreateFileW failed: {ctypes.get_last_error()}")
        buf = bytearray()
        chunk = ctypes.create_string_buffer(1 << 20)
        read = wintypes.DWORD(0)
        while True:
            ok = k32.ReadFile(h, chunk, len(chunk), ctypes.byref(read), None)
            if not ok:
                break
            if read.value == 0:
                break
            buf += chunk.raw[:read.value]
        k32.CloseHandle(h)
        with open(tmp, "wb") as f:
            f.write(buf)
        copied = True
        print(f"  (used shared-read handle, {len(buf)} bytes)")
    if not copied:
        raise RuntimeError("could not copy Cookies DB")

    key = get_master_key()
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE '%bilibili%'"
    ).fetchall()
    con.close()

    out = {}
    for host, name, enc in rows:
        if name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
            try:
                out[name] = decrypt_value(enc, key)
            except Exception as e:  # noqa: BLE001
                print(f"  ! decrypt {name} failed: {e}")
    return out


cookies = load_bili_cookies()
print("=== 从 Chrome 解出的 B站 cookie ===")
for k, v in cookies.items():
    shown = v if k in ("DedeUserID",) else (v[:6] + "..." + v[-4:] if len(v) > 12 else "***")
    print(f"  {k:<20} {shown}")

if "SESSDATA" not in cookies:
    print("\nFATAL: 没解出 SESSDATA，无法查询")
    sys.exit(2)

cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

try:
    import requests
except ImportError:
    print("requests missing")
    sys.exit(2)

s = requests.Session()
s.trust_env = False  # 直连，绕过系统代理（B站 TLS 会被代理干扰）
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
})

uid = cookies.get("DedeUserID", "")
print(f"\nuid = {uid}")

# ---- 1) 收藏夹列表（只读）----
r = s.get("https://api.bilibili.com/x/v3/fav/folder/created/list-all",
          params={"up_mid": uid}, timeout=20)
data = r.json()
print(f"\n=== 收藏夹列表 (code={data.get('code')}) ===")
folders = (data.get("data") or {}).get("list") or []
for f in sorted(folders, key=lambda x: -x.get("media_count", 0)):
    priv = "私密" if (f.get("attr", 0) & 1) else "公开"
    print(f"  {f['title']:<22} {f.get('media_count',0):>4} 条  [{priv}]  id={f['id']}")

print(f"\n共 {len(folders)} 个收藏夹，总视频 {sum(f.get('media_count',0) for f in folders)} 条")

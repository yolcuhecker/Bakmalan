#!/usr/bin/env python3
"""
LİDRAZE OTP FEEDER BOT v2.4 (Özel Paket / Stok Yönetim Sürümü)
- /start → direkt STOK EKLE paneli (admin)
- Token/Admin boş kontrolü
- 6sn topla, kategori başına ayrı TXT
- Ülkeli ve platform detaylı manuel/otomatik paket yönetimi

Cr: @fbxnext
"""

import json
import os
import re
import sqlite3
import sys
import time
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

# ============================================================
# ⚠️ BURAYI DOLDUR ⚠️
# ============================================================
BOT_TOKEN = "BURAYA_BOT_TOKENI_YAZ"              # ← @BotFather'dan token
ADMIN_IDS = [123456789]                          # ← [Telegram ID'n] formatında
DB_PATH = os.getenv("FEEDER_DB_PATH", "lidraze_feeder.sqlite3")

# ============================================================
# SABİTLER
# ============================================================
BOT_NAME = "LİDRAZE FEEDER"
SCAN_INTERVAL = 6
API_TIMEOUT = 10
MAX_SEEN = 30000

APIS = [
    "http://194.238.28.236:8000/container",
    "http://62.169.28.71/client.php?token=pbqDNx2Mt1Q2ggnrvBz2tEM-aSsx099a&limit=100",
]

CATEGORY_FILES = {
    "whatsapp": "whatsapp.txt", "telegram": "telegram.txt", "tiktok": "tiktok.txt",
    "imo": "imo.txt", "snapchat": "snapchat.txt", "facebook": "facebook.txt",
    "instagram": "instagram.txt", "google": "google.txt", "discord": "discord.txt",
    "twitter": "twitter.txt", "paypal": "paypal.txt", "other": "other.txt",
}

CATEGORY_LABELS = {
    "whatsapp": "🟢 WhatsApp", "telegram": "✈️ Telegram", "tiktok": "🎵 TikTok",
    "imo": "💬 IMO", "snapchat": "👻 Snapchat", "facebook": "🔵 Facebook",
    "instagram": "📸 Instagram", "google": "🔎 Google", "discord": "🎮 Discord",
    "twitter": "🐦 Twitter/X", "paypal": "💳 PayPal", "other": "📦 Diğer",
}

PLATFORM_MAP = {
    "whatsapp": "whatsapp", "wa business": "whatsapp", "wa": "whatsapp",
    "telegram": "telegram", "tg": "telegram",
    "tiktok": "tiktok", "tik tok": "tiktok",
    "imo": "imo",
    "snapchat": "snapchat", "snap": "snapchat",
    "facebook": "facebook", "fb": "facebook", "meta": "facebook",
    "instagram": "instagram", "insta": "instagram", "ig": "instagram",
    "google": "google", "gmail": "google",
    "discord": "discord",
    "twitter": "twitter", " x ": "twitter",
    "paypal": "paypal",
}


def set_low_priority():
    try:
        os.nice(19)
    except Exception:
        pass
    try:
        os.system("ionice -c 3 -p %d 2>/dev/null" % os.getpid())
    except Exception:
        pass


def log_err(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# TELEGRAM API
# ============================================================
def tg_call(method: str, params: dict, timeout: int = 30, verbose: bool = False) -> Optional[dict]:
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        try:
            r = json.loads(body)
        except Exception:
            if verbose:
                log_err(f"{method} bad json: {body[:200]}")
            return None
        if verbose and not r.get("ok"):
            log_err(f"{method} API error: {r.get('description', '?')}")
        return r
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        if verbose:
            log_err(f"{method} HTTP {e.code}: {err_body[:200]}")
        return None
    except Exception as e:
        if verbose:
            log_err(f"{method} exc: {e}")
        return None


def tg_send(chat_id, text: str, parse_mode: str = "HTML", markup: Optional[dict] = None) -> bool:
    params = {"chat_id": chat_id, "text": text[:4000], "parse_mode": parse_mode}
    if markup:
        params["reply_markup"] = json.dumps(markup)
    r = tg_call("sendMessage", params)
    return bool(r and r.get("ok"))


def tg_send_file(chat_id, filename: str, content: bytes, caption: str) -> bool:
    try:
        boundary = "----lidrazeBoundary"
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
        parts = [
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="chat_id"', b"",
            str(chat_id).encode(),
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="caption"', b"",
            caption.encode("utf-8"),
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="parse_mode"', b"",
            b"HTML",
            f"--{boundary}".encode(),
            f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode(),
            b"Content-Type: text/plain", b"",
            content,
            f"--{boundary}--".encode(),
            b"",
        ]
        body = b"\r\n".join(parts)
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            r = json.loads(raw)
            return bool(r.get("ok"))
        except Exception:
            return False
    except Exception as e:
        log_err(f"send_file: {e}")
        return False


def tg_answer_callback(cb_id: str, text: str = "", alert: bool = False):
    tg_call("answerCallbackQuery", {
        "callback_query_id": cb_id, "text": text, "show_alert": alert
    })


def tg_edit(chat_id, message_id: int, text: str, markup: Optional[dict] = None) -> bool:
    params = {
        "chat_id": chat_id, "message_id": message_id,
        "text": text[:4000], "parse_mode": "HTML"
    }
    if markup:
        params["reply_markup"] = json.dumps(markup)
    r = tg_call("editMessageText", params)
    return bool(r and r.get("ok"))


def tg_get_updates(offset: int, timeout: int = 20) -> list:
    r = tg_call("getUpdates", {
        "offset": offset, "timeout": timeout, "limit": 20,
        "allowed_updates": json.dumps(["message", "callback_query"])
    }, timeout=timeout + 5)
    if not r or not r.get("ok"):
        return []
    return r.get("result", [])


def tg_get_file(file_id: str) -> Optional[str]:
    r = tg_call("getFile", {"file_id": file_id})
    if r and r.get("ok"):
        return r["result"].get("file_path")
    return None


def tg_download_file(file_path: str) -> Optional[bytes]:
    try:
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


# ============================================================
# DB
# ============================================================
_db_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, cached_statements=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size=-2000")
    conn.execute("PRAGMA mmap_size=0")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            if r["name"] == column:
                return True
    except Exception:
        pass
    return False


def init_db():
    global _db_initialized
    if _db_initialized:
        return
    try:
        with _connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS seen (
                number TEXT PRIMARY KEY,
                platform TEXT,
                category TEXT,
                first_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                number TEXT NOT NULL,
                country TEXT,
                platform TEXT,
                added_at TEXT NOT NULL,
                added_by INTEGER,
                used INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY CHECK (id=1),
                total_seen INTEGER DEFAULT 0,
                total_sent INTEGER DEFAULT 0,
                total_batches INTEGER DEFAULT 0,
                started_at TEXT
            );
            INSERT OR IGNORE INTO stats(id, total_seen, total_sent, total_batches, started_at)
            VALUES(1, 0, 0, 0, datetime('now'));
            """)

            if not _column_exists(conn, "stats", "total_batches"):
                try:
                    conn.execute("ALTER TABLE stats ADD COLUMN total_batches INTEGER DEFAULT 0")
                except Exception:
                    pass
            for col, ddl in [
                ("total_seen", "INTEGER DEFAULT 0"),
                ("total_sent", "INTEGER DEFAULT 0"),
                ("started_at", "TEXT"),
            ]:
                if not _column_exists(conn, "stats", col):
                    try:
                        conn.execute(f"ALTER TABLE stats ADD COLUMN {col} {ddl}")
                    except Exception:
                        pass

            conn.execute("UPDATE stats SET total_batches=0 WHERE total_batches IS NULL")
        _db_initialized = True
    except Exception as e:
        log_err(f"init_db: {e}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ============================================================
# SEEN
# ============================================================
_seen_set: set = set()


def load_seen():
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT number FROM seen").fetchall()
        _seen_set.clear()
        for r in rows:
            _seen_set.add(r["number"])
        log_err(f"Seen: {len(_seen_set)}")
    except Exception as e:
        log_err(f"load_seen: {e}")


def add_seen_bulk(items: list) -> int:
    new_items = []
    for number, platform, category in items:
        if number in _seen_set:
            continue
        _seen_set.add(number)
        new_items.append((number, platform, category, now_iso()))
    if not new_items:
        return 0
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen(number, platform, category, first_seen) VALUES(?,?,?,?)",
                new_items
            )
            conn.execute("UPDATE stats SET total_seen = total_seen + ? WHERE id=1", (len(new_items),))
    except Exception as e:
        log_err(f"add_seen: {e}")
    return len(new_items)


def inc_batch(sent: int = 0):
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE stats SET total_sent = total_sent + ?, total_batches = total_batches + 1 WHERE id=1",
                (sent,)
            )
    except Exception as e:
        log_err(f"inc_batch: {e}")


def cleanup_seen():
    try:
        with _connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
            if total <= MAX_SEEN:
                return
            delete_count = total - MAX_SEEN + 3000
            conn.execute(
                "DELETE FROM seen WHERE number IN (SELECT number FROM seen ORDER BY first_seen ASC LIMIT ?)",
                (delete_count,)
            )
        load_seen()
    except Exception:
        pass


def get_stats():
    try:
        with _connect() as conn:
            return conn.execute("SELECT * FROM stats WHERE id=1").fetchone()
    except Exception:
        return None


def get_platform_stats():
    try:
        with _connect() as conn:
            return conn.execute(
                "SELECT category, COUNT(*) as c FROM seen GROUP BY category ORDER BY c DESC"
            ).fetchall()
    except Exception:
        return []


def get_recent(limit: int = 15):
    try:
        with _connect() as conn:
            return conn.execute(
                "SELECT number, platform, category, first_seen FROM seen ORDER BY first_seen DESC LIMIT ?",
                (limit,)
            ).fetchall()
    except Exception:
        return []


def reset_seen():
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM seen")
            conn.execute("UPDATE stats SET total_seen=0, total_sent=0, total_batches=0 WHERE id=1")
        _seen_set.clear()
    except Exception as e:
        log_err(f"reset: {e}")


# ============================================================
# STOK
# ============================================================
def parse_stock_txt(text: str) -> list:
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        number = normalize_number(parts[0])
        if not number or len(number) < 7:
            continue
        items.append((
            number,
            parts[1] if len(parts) > 1 else "",
            parts[2] if len(parts) > 2 else ""
        ))
    return items


def save_stock(category: str, items: list, added_by: int) -> int:
    added = 0
    try:
        with _connect() as conn:
            for number, country, platform in items:
                exists = conn.execute(
                    "SELECT 1 FROM stock WHERE category=? AND number=? AND used=0",
                    (category, number)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO stock(category, number, country, platform, added_at, added_by, used)
                       VALUES(?,?,?,?,?,?,0)""",
                    (category, number, country, platform, now_iso(), added_by)
                )
                added += 1
    except Exception as e:
        log_err(f"save_stock: {e}")
    return added


def stock_count(cat: Optional[str] = None) -> int:
    try:
        with _connect() as conn:
            if cat:
                return conn.execute(
                    "SELECT COUNT(*) FROM stock WHERE category=? AND used=0", (cat,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM stock WHERE used=0").fetchone()[0]
    except Exception:
        return 0


# ============================================================
# API
# ============================================================
def fetch_api_no_cache(url: str) -> Optional[list]:
    try:
        req = urllib.request.Request(url, headers={
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
        })
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            raw = resp.read()
            enc = resp.headers.get("Content-Encoding", "").lower()
        if "gzip" in enc:
            import gzip
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8", errors="ignore")
        try:
            data = json.loads(text)
        except Exception:
            return None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "result", "items", "list"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return None
    except Exception:
        return None


def normalize_number(n: str) -> str:
    return re.sub(r"\D", "", n or "")


def parse_entry(entry) -> Optional[dict]:
    try:
        if isinstance(entry, dict):
            number = entry.get("numara") or entry.get("number") or entry.get("phone", "")
            if not number:
                return None
            return {
                "platform": str(entry.get("platform") or entry.get("service") or "").strip()[:30],
                "number": normalize_number(str(number)),
                "country": str(entry.get("ülke") or entry.get("country", "")).strip()[:20],
            }
        if isinstance(entry, list) and len(entry) >= 2:
            number = str(entry[1]) if len(entry) > 1 else ""
            if not number:
                return None
            return {
                "platform": str(entry[0]).strip()[:30] if len(entry) > 0 else "",
                "number": normalize_number(number),
                "country": str(entry[4]).strip()[:20] if len(entry) > 4 else "",
            }
    except Exception:
        pass
    return None


def platform_to_category(platform: str) -> str:
    p = (platform or "").lower().strip()
    if not p:
        return "other"
    cat = PLATFORM_MAP.get(p)
    if cat:
        return cat
    for keyword, cat in PLATFORM_MAP.items():
        if keyword in p:
            return cat
    return "other"


# ============================================================
# DURUM
# ============================================================
feeder_state = {
    "running": True,
    "total_sent": 0,
    "last_scan": None,
    "scan_count": 0,
    "ready": False,
    "last_batch_count": 0,
}

admin_state = {}


# ============================================================
# KLAVYELER
# ============================================================
def stock_main_kb() -> dict:
    """Admin /start → direkt bu menü"""
    return {"inline_keyboard": [
        [{"text": "➕ STOK EKLE", "callback_data": "add_stock"}],
        [{"text": "📊 STOK DURUMU", "callback_data": "stock_status"}],
        [{"text": "📡 Tarama: " + ("🟢" if feeder_state["running"] else "🔴"),
          "callback_data": "toggle_scan"}],
        [{"text": "📈 İstatistik", "callback_data": "stats"},
         {"text": "📁 Son", "callback_data": "recent"}],
        [{"text": "🧹 Sıfırla", "callback_data": "clear_cache"}],
    ]}


def categories_kb(prefix: str, back: str = "home") -> dict:
    cats = list(CATEGORY_LABELS.items())
    rows = []
    for i in range(0, len(cats), 2):
        row = []
        for cat_key, cat_label in cats[i:i+2]:
            row.append({"text": cat_label, "callback_data": f"{prefix}:{cat_key}"})
        rows.append(row)
    rows.append([{"text": "⬅️ GERİ", "callback_data": back}])
    return {"inline_keyboard": rows}


def back_kb(target: str = "home") -> dict:
    return {"inline_keyboard": [[{"text": "⬅️ ANA MENÜ", "callback_data": target}]]}


# ============================================================
# ADMIN MESAJ ROUTER
# ============================================================
def handle_start(msg: dict):
    """
    /start → admin ise direkt STOK EKLE paneli
    """
    uid = msg["from"]["id"]
    chat_id = msg["chat"]["id"]

    if not is_admin(uid):
        tg_send(chat_id, "🚫 Sadece admin.")
        return

    txt = (
        f"👋 <b>{BOT_NAME}</b>\n\n"
        f"📦 <b>STOK & PAKET YÖNETİMİ</b>\n\n"
        f"👇 Ülke/Paket eklemek için <b>➕ STOK EKLE</b>'ye bas"
    )
    tg_send(chat_id, txt, markup=stock_main_kb())


def handle_add_stock(uid: int, chat_id: int, msg_id: int):
    """Kategori seçimi"""
    if not is_admin(uid):
        return
    tg_edit(chat_id, msg_id,
            "📁 <b>HANGİ KATEGORİYE PAKET EKLENECEK?</b>\n\nKategori seç:",
            markup=categories_kb("addcat", back="home"))


def handle_addcat(uid: int, chat_id: int, msg_id: int, category: str):
    """Kategori seçildi → TXT bekle"""
    if not is_admin(uid) or category not in CATEGORY_LABELS:
        return

    admin_state[uid] = {"action": "stock_load", "category": category}
    tg_edit(chat_id, msg_id,
            f"📥 <b>{CATEGORY_LABELS[category]} - Paket Yükleme</b>\n\n"
            f"<b>Format (her satır için ülke ve platform ekleyebilirsin):</b>\n"
            f"<code>905511111111|Türkiye|WhatsApp</code>\n"
            f"<code>14155552671|Amerika|WhatsApp</code>\n\n"
            f"📎 <b>TXT dosyası</b> gönder veya yukarıdaki formatta <b>metin</b> yapıştır.\n\n"
            f"/iptal - İptal Et",
            markup=back_kb("home"))


def handle_stock_status(uid: int, chat_id: int, msg_id: int):
    if not is_admin(uid):
        return
    txt = "📊 <b>MEVCUT STOK / PAKET DURUMU</b>\n\n"
    total = 0
    try:
        with _connect() as conn:
            for cat_key, cat_label in CATEGORY_LABELS.items():
                c = conn.execute(
                    "SELECT COUNT(*) FROM stock WHERE category=? AND used=0", (cat_key,)
                ).fetchone()[0]
                total += c
                emoji = "✅" if c > 0 else "❌"
                txt += f"{cat_label}: {emoji} {c}\n"
    except Exception:
        pass
    txt += f"\n📦 <b>Toplam Stok: {total}</b>"
    tg_edit(chat_id, msg_id, txt, markup=back_kb("home"))


def handle_stats(uid: int, chat_id: int):
    if not is_admin(uid):
        return
    s = get_stats()
    platforms = get_platform_stats()
    txt = "📊 <b>İSTATİSTİK</b>\n\n"
    if s:
        try:
            txt += f"🆔 Benzersiz: {s['total_seen']}\n"
            txt += f"📤 TXT: {s['total_batches']}\n\n"
        except Exception:
            pass
    for p in platforms[:15]:
        txt += f"• {p['category'] or 'other'}: {p['c']}\n"
    tg_send(chat_id, txt, markup=stock_main_kb())


def handle_recent(uid: int, chat_id: int):
    if not is_admin(uid):
        return
    rows = get_recent(15)
    if not rows:
        txt = "📭"
    else:
        txt = "📁 <b>SON 15</b>\n\n"
        for r in rows:
            txt += f"<code>{r['number']}</code> → {r['category'] or 'other'}\n"
    tg_send(chat_id, txt, markup=stock_main_kb())


# ============================================================
# STATE MESSAGE (stok yükleme)
# ============================================================
def handle_state_message(msg: dict) -> bool:
    uid = msg["from"]["id"]
    chat_id = msg["chat"]["id"]
    state = admin_state.get(uid)
    if not state or not is_admin(uid):
        return False

    txt = (msg.get("text") or "").strip()

    if txt.lower() in ("/iptal", "/cancel"):
        admin_state.pop(uid, None)
        tg_send(chat_id, "❌ İptal edildi.", markup=stock_main_kb())
        return True

    action = state.get("action")

    if action == "stock_load":
        category = state.get("category")
        if not category:
            admin_state.pop(uid, None)
            return True

        content = ""
        if "document" in msg:
            fp = tg_get_file(msg["document"]["file_id"])
            if fp:
                data = tg_download_file(fp)
                if data:
                    content = data.decode("utf-8", errors="ignore")
            if not content:
                tg_send(chat_id, "❌ Dosya okunamadı.", markup=back_kb("home"))
                return True
        elif txt:
            content = txt
        else:
            tg_send(chat_id, "❌ TXT dosyası veya metin gönder.", markup=back_kb("home"))
            return True

        items = parse_stock_txt(content)
        if not items:
            tg_send(chat_id,
                    "❌ Geçerli numara/paket bulunamadı.\n\nFormat:\n<code>905511111111|Ülke|Platform</code>",
                    markup=back_kb("home"))
            return True

        added = save_stock(category, items, uid)
        admin_state.pop(uid, None)
        tg_send(chat_id,
                f"✅ <b>Paketler Başarıyla Eklendi!</b>\n\n"
                f"📁 {CATEGORY_LABELS[category]}\n"
                f"➕ Eklenen Adet: <b>{added}</b>\n"
                f"📊 Kategorideki Toplam Stok: <b>{stock_count(category)}</b>\n"
                f"📦 Genel Toplam Stok: <b>{stock_count()}</b>",
                markup=stock_main_kb())
        return True

    return False


# ============================================================
# MESSAGE HANDLER
# ============================================================
def handle_message(msg: dict):
    try:
        uid = msg["from"]["id"]
        text = (msg.get("text") or "").strip()
        chat_id = msg["chat"]["id"]

        log_err(f"MSG uid={uid} text={text[:30]}")

        if handle_state_message(msg):
            return

        if not text.startswith("/"):
            return

        cmd = text.split()[0].split("@")[0].lower()

        if cmd == "/start":
            handle_start(msg)
        elif cmd == "/status":
            if not is_admin(uid):
                return
            st = "🟢" if feeder_state["running"] else "🔴"
            tg_send(chat_id,
                    f"📡 <b>DURUM</b>\n\nTarama: {st}\n"
                    f"Scan: {feeder_state['scan_count']}\n"
                    f"Son batch: {feeder_state['last_batch_count']}\n"
                    f"Seen: {len(_seen_set)}\n"
                    f"Stok: {stock_count()}",
                    markup=stock_main_kb())
        elif cmd == "/iptal":
            admin_state.pop(uid, None)
            tg_send(chat_id, "❌ İptal.", markup=stock_main_kb())

    except Exception as e:
        log_err(f"msg: {e}")


# ============================================================
# CALLBACK HANDLER
# ============================================================
def handle_callback(cb: dict):
    try:
        uid = cb["from"]["id"]
        cb_id = cb["id"]
        data = cb.get("data", "")
        msg = cb.get("message", {})
        chat_id = msg.get("chat", {}).get("id", 0)
        msg_id = msg.get("message_id", 0)

        if not is_admin(uid):
            tg_answer_callback(cb_id, "Yetkisiz", alert=True)
            return

        if data == "home":
            tg_edit(chat_id, msg_id,
                    f"👋 <b>{BOT_NAME}</b>\n\n📦 <b>STOK & PAKET YÖNETİMİ</b>",
                    markup=stock_main_kb())
            tg_answer_callback(cb_id)

        elif data == "add_stock":
            handle_add_stock(uid, chat_id, msg_id)
            tg_answer_callback(cb_id)

        elif data.startswith("addcat:"):
            cat = data.split(":", 1)[1]
            handle_addcat(uid, chat_id, msg_id, cat)
            tg_answer_callback(cb_id)

        elif data == "stock_status":
            handle_stock_status(uid, chat_id, msg_id)
            tg_answer_callback(cb_id)

        elif data == "toggle_scan":
            feeder_state["running"] = not feeder_state["running"]
            tg_answer_callback(cb_id,
                              "🟢" if feeder_state["running"] else "🔴",
                              alert=True)
            try:
                tg_edit(chat_id, msg_id,
                        f"👋 <b>{BOT_NAME}</b>\n\n📦 <b>STOK & PAKET YÖNETİMİ</b>",
                        markup=stock_main_kb())
            except Exception:
                pass

        elif data == "stats":
            handle_stats(uid, chat_id)
            tg_answer_callback(cb_id)

        elif data == "recent":
            handle_recent(uid, chat_id)
            tg_answer_callback(cb_id)

        elif data == "clear_cache":
            reset_seen()
            tg_answer_callback(cb_id, "✅ Sıfırlandı", alert=True)

    except Exception as e:
        log_err(f"cb: {e}")


# ============================================================
# SCAN (6sn)
# ============================================================
def do_scan(send: bool = True) -> int:
    if not feeder_state["running"]:
        return 0

    all_items = []
    for url in APIS:
        data = fetch_api_no_cache(url)
        if not data:
            continue
        for entry in data:
            try:
                p = parse_entry(entry)
                if not p or not p["number"] or len(p["number"]) < 7:
                    continue
                cat = platform_to_category(p["platform"])
                all_items.append((p["number"], p["platform"], p["country"], cat))
            except Exception:
                continue

    feeder_state["scan_count"] += 1
    feeder_state["last_scan"] = now_iso()

    if not all_items:
        return 0

    new_items = []
    seen_in_batch = set()
    for number, platform, country, cat in all_items:
        if number in _seen_set:
            continue
        if number in seen_in_batch:
            continue
        seen_in_batch.add(number)
        new_items.append((number, platform, country, cat))

    if not new_items:
        return 0

    bulk = [(n, p, c) for n, p, co, c in new_items]
    added = add_seen_bulk(bulk)
    if added == 0:
        return 0

    log_err(f"New: {added}")
    feeder_state["last_batch_count"] = added
    feeder_state["total_sent"] += added

    if not send:
        return added

    by_category: dict = {}
    for number, platform, country, cat in new_items:
        by_category.setdefault(cat, []).append({
            "number": number, "platform": platform, "country": country,
        })

    log_err(f"Kategoriler: {[(c, len(v)) for c, v in by_category.items()]}")

    files_sent = 0
    for category, items in by_category.items():
        try:
            filename = CATEGORY_FILES.get(category, f"{category}.txt")
            lines = []
            for it in items:
                parts = [it["number"]]
                if it["country"]:
                    parts.append(it["country"])
                if it["platform"]:
                    parts.append(it["platform"])
                lines.append("|".join(parts))
            content = "\n".join(lines).encode("utf-8")

            caption = (
                f"📁 <b>{filename}</b>\n\n"
                f"📊 Yeni: <b>{len(items)}</b>\n"
                f"🕐 {now_iso()[11:19]}\n\n"
                f"<i>İlk 5:</i>\n"
                + "\n".join(f"<code>{i['number']}</code>" for i in items[:5])
            )

            for admin_id in ADMIN_IDS:
                ok = tg_send_file(admin_id, filename, content, caption)
                if ok:
                    files_sent += 1
        except Exception as e:
            log_err(f"cat {category}: {e}")

    if files_sent > 0:
        inc_batch(files_sent)

    return added


def initial_download() -> int:
    for aid in ADMIN_IDS:
        tg_send(aid,
                f"📥 <b>Başlangıç İndirme</b>\n\n🔗 {len(APIS)} API taranıyor...")

    all_items = []
    for api_idx, url in enumerate(APIS, 1):
        data = fetch_api_no_cache(url)
        if not data:
            for aid in ADMIN_IDS:
                tg_send(aid, f"⚠️ API {api_idx}: boş")
            continue
        count = 0
        for entry in data:
            try:
                p = parse_entry(entry)
                if not p or not p["number"] or len(p["number"]) < 7:
                    continue
                cat = platform_to_category(p["platform"])
                all_items.append((p["number"], p["platform"], cat))
                count += 1
            except Exception:
                continue
        for aid in ADMIN_IDS:
            tg_send(aid, f"✅ API {api_idx}: {count}")

    if all_items:
        unique = {}
        for n, p, c in all_items:
            if n not in unique:
                unique[n] = (n, p, c)
        items_list = list(unique.values())
        added = add_seen_bulk(items_list)

        cat_counts = {}
        for n, p, c in items_list:
            cat_counts[c] = cat_counts.get(c, 0) + 1
        cats_txt = "\n".join(
            f"  • {cat}: {cnt}"
            for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1])[:12]
        )

        for aid in ADMIN_IDS:
            tg_send(aid,
                    f"✅ <b>İndirme tamamlandı</b>\n\n"
                    f"💾 Kaydedildi: <b>{added}</b>\n"
                    f"🟢 Sistem aktif...\n\n"
                    f"<b>Kategoriler:</b>\n{cats_txt}")

    feeder_state["ready"] = True
    return len(all_items)


def scanner_loop():
    log_err(f"Scanner start ({SCAN_INTERVAL}s)")
    try:
        initial_download()
    except Exception as e:
        log_err(f"init: {e}")

    while True:
        try:
            do_scan(send=True)
            if feeder_state["scan_count"] % 100 == 0 and len(_seen_set) > MAX_SEEN:
                cleanup_seen()
        except Exception as e:
            log_err(f"scan: {e}")
        time.sleep(SCAN_INTERVAL)


# ============================================================
# POLLING
# ============================================================
def polling_loop():
    log_err("Polling start")
    offset = 0
    while True:
        try:
            updates = tg_get_updates(offset, timeout=20)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    if "message" in u:
                        handle_message(u["message"])
                    elif "callback_query" in u:
                        handle_callback(u["callback_query"])
                except Exception as e:
                    log_err(f"upd: {e}")
        except Exception as e:
            log_err(f"poll: {e}")
            time.sleep(3)


# ============================================================
# MAIN
# ============================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "BURAYA_BOT_TOKENI_YAZ":
        log_err("=" * 60)
        log_err("❌ BOT_TOKEN BOŞ!")
        log_err("=" * 60)
        sys.exit(1)

    if not ADMIN_IDS or ADMIN_IDS == [123456789]:
        log_err("=" * 60)
        log_err("❌ ADMIN_IDS BOŞ!")
        log_err("=" * 60)
        sys.exit(1)

    set_low_priority()
    init_db()
    log_err(f"{BOT_NAME} v2.4 start ({SCAN_INTERVAL}s)")

    me = tg_call("getMe", {}, verbose=True)
    if me and me.get("ok"):
        log_err(f"✅ Bot: @{me['result']['username']}")
    else:
        log_err("❌ Bot token geçersiz veya bağlantı yok!")
        sys.exit(1)

    for aid in ADMIN_IDS:
        ok = tg_send(aid, "🟢 Paket yönetim ve feeder sistemi başlatıldı. /start → Stok Ekle paneli.")
        log_err(f"Admin {aid}: {'OK' if ok else 'FAILED'}")

    load_seen()

    threading.Thread(target=scanner_loop, daemon=True).start()
    polling_loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_err("Kapatıldı")
    except Exception as e:
        log_err(f"Fatal: {e}")
        sys.exit(1)

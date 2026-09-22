"""Sync all Telegram group members into the local database using YOUR
own Telegram account (Telethon UserBot).

Why: the Bot API cannot list group members. But since you are inside the
group (as a normal member or admin), your personal Telegram account CAN
enumerate the members that are visible to you.

Usage:
    1) pip install telethon
    2) put TB_API_ID, TB_API_HASH, TB_PHONE in your .env (from my.telegram.org)
    3) first run logs in interactively (code + password); session is saved:
           python userbot_sync.py
    4) later runs reuse the saved session automatically.

After syncing, open the dashboard: every member now has a record.
From the member page you can kick them manually with the existing buttons.

Note:
    - Requires your account to actually be in the group and able to see the
      member list (in some groups "hide members" is enabled - then you must
      become an admin first).
    - Kick buttons on the dashboard use the BOT, so the bot itself must be an
      admin with "Ban users" permission in the group.
"""

import argparse
import json
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl import types as tl

import config
import db

db.init_db()


def _name(user) -> str:
    parts = [p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p]
    return (" ".join(parts)).strip() or user.username or str(user.id)


async def list_groups(client: TelegramClient) -> None:
    rows = []
    async for dialog in client.iter_dialogs(limit=None):
        entity = dialog.entity
        is_group = isinstance(entity, tl.Chat) or isinstance(entity, tl.Channel)
        if not is_group:
            continue
        is_channel = isinstance(entity, tl.Channel)
        kind = (
            "قناة خاصة"
            if is_channel and not getattr(entity, "megagroup", False)
            else "مجموعة/قناة"
        )
        rows.append(
            {
                "id": dialog.id,
                "title": getattr(entity, "title", "بدون عنوان"),
                "members": getattr(entity, "participants_count", 0),
                "type": kind,
                "public": bool(getattr(entity, "username", None)),
            }
        )
    for row in sorted(rows, key=lambda r: -(r.get("members") or 0)):
        print(
            (
                f"{row['type']} | {row['title']} | {row['members']} عضو "
                f"| {row['id']}"
            )
            + (" | عام @" + "?" if row["public"] else " | خاص")
        )
        print(json.dumps({"id": row["id"], "title": row["title"]}, ensure_ascii=False))
    print(f"[dialogs] found {len(rows)} groups/channels.")


async def sync(client: TelegramClient, chat_id: int, dry_run: bool) -> int:
    group = await client.get_entity(chat_id)
    count = 0
    seen: list[int] = []
    async for user in client.iter_participants(group, aggressive=False):
        if getattr(user, "bot", False) or getattr(user, "deleted", False):
            continue
        if isinstance(user, tl.User):
            user_id = user.id
            seen.append(user_id)
            if dry_run:
                print(f"[dry-run] would add/update: {user_id} | {_name(user)} | @{user.username or ''}")
                count += 1
                continue
            db.upsert_member(
                user_id,
                telegramName=_name(user),
                telegramUsername=user.username,
                channelId=chat_id,
                inGroup=1,
                lastCheckedAt=db.now_iso(),
            )
            count += 1
    if dry_run:
        print(f"[dry-run] total participants (non-bot): {count}")
        return count

    missing = 0
    for member in db.get_members():
        if member.get("inGroup") == 1 and member["telegramUserId"] not in seen:
            if member.get("channelId") not in (None, chat_id):
                continue
            db.upsert_member(member["telegramUserId"], inGroup=0, lastCheckedAt=db.now_iso())
            missing += 1
    print(f"[sync] group={chat_id}: updated/added {count} participants, marked {missing} who left as outside.")
    return count


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="list members without writing db")
    parser.add_argument("--code", help="Telegram login code (for non-interactive run)")
    parser.add_argument("--password", help="2FA password (for non-interactive run)")
    parser.add_argument("--chat", type=int, help="group id to pull from (default: CHANNEL_ID)")
    parser.add_argument("--list-groups", action="store_true", help="list groups of this account")
    parser.add_argument("--check-session", action="store_true", help="only report whether current session is logged in")
    parser.add_argument("--send-code", action="store_true", help="send a fresh login code to TB_PHONE")
    parser.add_argument("--session", help="session file name override (default: TB_SESSION_NAME)")
    args = parser.parse_args()

    if not config.TB_API_ID or not config.TB_API_HASH:
        print("Missing TB_API_ID / TB_API_HASH in .env (from https://my.telegram.org/apps)")
        sys.exit(1)

    session_name = args.session or config.TB_SESSION_NAME

    if args.check_session:
        client = TelegramClient(
            str(config.TB_SESSION_DIR / session_name),
            config.TB_API_ID,
            config.TB_API_HASH,
            proxy=None,
        )
        await client.connect()
        try:
            ok = await client.is_user_authorized()
            print("authorized=" + ("1" if ok else "0"))
            if ok:
                me = await client.get_me()
                print("user=" + _name(me) + " (ID " + str(me.id) + ")")
        except Exception as exc:
            print("authorized=0")
            print("error=" + str(exc))
        finally:
            await client.disconnect()
        sys.exit(0 if ok else 2)

    if args.send_code:
        if not config.TB_PHONE:
            print("code_send_error=Missing TB_PHONE in .env")
            sys.exit(1)
        client = TelegramClient(
            str(config.TB_SESSION_DIR / session_name),
            config.TB_API_ID,
            config.TB_API_HASH,
            proxy=None,
        )
        await client.connect()
        try:
            res = await client.send_code_request(config.TB_PHONE)
            print("code_sent=" + config.TB_PHONE)
            print("phone_code_hash=" + res.phone_code_hash)
            t = getattr(res, "type", None)
            mode = type(t).__name__ if t is not None else "?"
            channel = {
                "CodeTypeApp": "داخل تطبيق تلغرام (إشعار على رقم الهاتف)",
                "CodeTypeSms": "رسالة SMS",
                "CodeTypeCall": "اتصال هاتفي",
                "CodeTypeMissedCall": "مكالمة فائتة",
                "CodeTypeFragmentSms": "SMS عبر Fragment",
                "CodeTypeFlashCall": "مكالمة فلاش",
            }.get(mode, mode)
            print("code_channel=" + channel)
            code_ok = True
        except Exception as exc:
            print("code_send_error=" + str(exc))
            code_ok = False
        finally:
            await client.disconnect()
        sys.exit(0 if code_ok else 1)

    def code_callback() -> str:
        if args.code:
            return args.code
        return input("أدخل كود التفعيل الذي وصلك على تلغرام: ").strip()

    client = TelegramClient(
        str(config.TB_SESSION_DIR / session_name),
        config.TB_API_ID,
        config.TB_API_HASH,
        proxy=None,
    )
    try:
        await client.start(
            phone=config.TB_PHONE or None,
            password=args.password or None,
            code_callback=code_callback,
        )
    except ValueError as exc:
        if "password" not in str(exc).lower():
            raise
        pw = args.password or input(
            "الحساب محمي بكلمة مرور (2FA): أدخل كلمة مرور الحساب: "
        ).strip()
        await client.start(
            phone=config.TB_PHONE or None,
            password=pw or None,
            code_callback=code_callback,
        )

    me = await client.get_me()
    if me:
        print(
            f"[session] الحساب النشط للجلسة: {_name(me)} (ID {me.id})"
        )
    if config.TB_PHONE and me:
        expect = config.TB_PHONE.replace(" ", "").lstrip("+")
        actual = (getattr(me, "phone", "") or "").replace(" ", "").lstrip("+")
        if actual and actual != expect:
            await client.disconnect()
            print(
                "خطأ: هذه الجلسة مسجلة بحساب مختلف عن رقم الهاتف المحفوظ!\n"
                f"  الجلسة '{session_name}' تابعة لـ: {_name(me)} ({getattr(me, 'phone', '') or 'بدون رقم'})\n"
                f"  الهاتف المحفوظ في الإعدادات: +{expect}\n"
                "الحل: من لوحة التحكم → إعدادات البوت والتحقق → قسم سحب الأسماء،\n"
                f"  غيّر «اسم الجلسة» لاسم جديد (مثال: {session_name}2) ثم احفظ.\n"
                "  بعدها سجّل الدخول مرة واحدة من CMD:\n"
                f"    python userbot_sync.py --session {session_name}2\n"
                "  ثم ارجع للوحة واضغط «سحب أعضاء المجموعة»."
            )
            sys.exit(2)

    try:
        if args.list_groups:
            await list_groups(client)
        else:
            chat_id = args.chat or config.CHANNEL_ID
            await sync(client, chat_id, dry_run=args.dry_run)
    finally:
        await client.disconnect()
    print("[sync] done.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
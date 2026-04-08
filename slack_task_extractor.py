#!/usr/bin/env python3
"""Slackメッセージからタスクを抽出するスクリプト。

Slack Web APIを使用してチャンネルのメッセージを取得し、
タスクとして認識できる内容を抽出・整形して出力する。

環境変数:
    SLACK_BOT_TOKEN: Slack Bot User OAuth Token (xoxb-...)
    SLACK_USER_TOKEN: Slack User OAuth Token (xoxp-...) ※Bot Tokenがない場合に使用

使い方:
    python slack_task_extractor.py                          # 参加チャンネルの直近メッセージを取得
    python slack_task_extractor.py --channels general,random # 特定チャンネルのみ
    python slack_task_extractor.py --days 3                  # 直近3日分
    python slack_task_extractor.py --mentions-only           # 自分宛メンションのみ
    python slack_task_extractor.py --dm                      # DMも含める
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print(
        "エラー: slack_sdk がインストールされていません。\n"
        "  pip install slack_sdk\n"
        "を実行してください。",
        file=sys.stderr,
    )
    sys.exit(1)


def get_client() -> WebClient:
    """Slack APIクライアントを初期化する。"""
    token = os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_USER_TOKEN")
    if not token:
        print(
            "エラー: SLACK_BOT_TOKEN または SLACK_USER_TOKEN 環境変数を設定してください。\n"
            "\n"
            "Slack APIトークンの取得方法:\n"
            "  1. https://api.slack.com/apps にアクセス\n"
            "  2. 「Create New App」→「From scratch」でアプリを作成\n"
            "  3. 「OAuth & Permissions」で以下のスコープを追加:\n"
            "     Bot Token Scopes:\n"
            "       - channels:history  (パブリックチャンネルの履歴)\n"
            "       - channels:read     (チャンネル一覧)\n"
            "       - groups:history    (プライベートチャンネルの履歴)\n"
            "       - groups:read       (プライベートチャンネル一覧)\n"
            "       - im:history        (DMの履歴)\n"
            "       - im:read           (DM一覧)\n"
            "       - users:read        (ユーザー情報)\n"
            "  4. 「Install to Workspace」でインストール\n"
            "  5. 表示される Bot User OAuth Token をコピー\n"
            "  6. export SLACK_BOT_TOKEN='xoxb-...' で設定",
            file=sys.stderr,
        )
        sys.exit(1)
    return WebClient(token=token)


def get_user_map(client: WebClient) -> dict[str, str]:
    """ユーザーID → 表示名のマッピングを取得する。"""
    user_map = {}
    try:
        cursor = None
        while True:
            resp = client.users_list(cursor=cursor, limit=200)
            for member in resp["members"]:
                display = (
                    member.get("profile", {}).get("display_name")
                    or member.get("profile", {}).get("real_name")
                    or member.get("name", member["id"])
                )
                user_map[member["id"]] = display
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        print(f"警告: ユーザー一覧の取得に失敗しました: {e}", file=sys.stderr)
    return user_map


def resolve_mentions(text: str, user_map: dict[str, str]) -> str:
    """メッセージ内の <@U...> をユーザー名に置換する。"""
    import re

    def replace_mention(m: "re.Match[str]") -> str:
        uid = m.group(1)
        return f"@{user_map.get(uid, uid)}"

    return re.sub(r"<@(U[A-Z0-9]+)>", replace_mention, text)


def get_my_user_id(client: WebClient) -> str:
    """認証済みユーザーの ID を返す。"""
    try:
        resp = client.auth_test()
        return resp["user_id"]
    except SlackApiError:
        return ""


def list_channels(
    client: WebClient, *, include_dm: bool = False
) -> list[dict]:
    """参加中のチャンネル一覧を取得する。"""
    channels: list[dict] = []

    # パブリック + プライベートチャンネル
    types = "public_channel,private_channel"
    if include_dm:
        types += ",im,mpim"

    try:
        cursor = None
        while True:
            resp = client.conversations_list(
                types=types,
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
            for ch in resp["channels"]:
                if ch.get("is_member", False) or ch.get("is_im", False):
                    channels.append(ch)
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        print(f"警告: チャンネル一覧の取得に失敗しました: {e}", file=sys.stderr)

    return channels


def fetch_messages(
    client: WebClient,
    channel_id: str,
    *,
    oldest: float,
    limit: int = 200,
) -> list[dict]:
    """チャンネルからメッセージを取得する。"""
    messages: list[dict] = []
    try:
        cursor = None
        while True:
            resp = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest),
                limit=limit,
                cursor=cursor,
            )
            messages.extend(resp.get("messages", []))
            if not resp.get("has_more", False):
                break
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        print(
            f"警告: チャンネル {channel_id} のメッセージ取得に失敗しました: {e}",
            file=sys.stderr,
        )
    return messages


def fetch_thread_replies(
    client: WebClient, channel_id: str, thread_ts: str
) -> list[dict]:
    """スレッドの返信を取得する。"""
    try:
        resp = client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=100
        )
        # 最初のメッセージ（親）は除外
        return resp.get("messages", [])[1:]
    except SlackApiError:
        return []


def format_message(
    msg: dict,
    *,
    channel_name: str,
    user_map: dict[str, str],
) -> dict:
    """メッセージを整形された辞書に変換する。"""
    ts = float(msg.get("ts", 0))
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    user_id = msg.get("user", "")
    text = msg.get("text", "")
    text = resolve_mentions(text, user_map)

    return {
        "channel": channel_name,
        "user": user_map.get(user_id, user_id),
        "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "text": text,
        "thread_reply_count": msg.get("reply_count", 0),
        "reactions": [
            {"name": r["name"], "count": r["count"]}
            for r in msg.get("reactions", [])
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slackメッセージを取得してタスク抽出用に出力する"
    )
    parser.add_argument(
        "--channels",
        type=str,
        default="",
        help="取得対象のチャンネル名（カンマ区切り）。未指定で全参加チャンネル",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="何日前までのメッセージを取得するか（デフォルト: 1）",
    )
    parser.add_argument(
        "--mentions-only",
        action="store_true",
        help="自分宛のメンションのみ抽出する",
    )
    parser.add_argument(
        "--dm",
        action="store_true",
        help="DMも含めて取得する",
    )
    parser.add_argument(
        "--include-threads",
        action="store_true",
        help="スレッドの返信も取得する",
    )
    parser.add_argument(
        "--output",
        choices=["json", "text"],
        default="text",
        help="出力形式（デフォルト: text）",
    )
    args = parser.parse_args()

    client = get_client()
    user_map = get_user_map(client)
    my_user_id = get_my_user_id(client) if args.mentions_only else ""

    # 取得期間
    oldest = (
        datetime.now(timezone.utc) - timedelta(days=args.days)
    ).timestamp()

    # チャンネル取得
    all_channels = list_channels(client, include_dm=args.dm)

    # フィルタ
    if args.channels:
        target_names = {n.strip().lstrip("#") for n in args.channels.split(",")}
        all_channels = [
            ch
            for ch in all_channels
            if ch.get("name", "") in target_names
            or ch.get("name_normalized", "") in target_names
        ]

    if not all_channels:
        print("対象チャンネルが見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    all_formatted: list[dict] = []

    for ch in all_channels:
        ch_id = ch["id"]
        ch_name = ch.get("name") or ch.get("user", ch_id)
        messages = fetch_messages(client, ch_id, oldest=oldest)

        for msg in messages:
            # bot メッセージやシステムメッセージはスキップ
            if msg.get("subtype") in (
                "channel_join",
                "channel_leave",
                "channel_topic",
                "channel_purpose",
                "bot_message",
            ):
                continue

            # メンションフィルタ
            if args.mentions_only and my_user_id:
                if f"<@{my_user_id}>" not in msg.get("text", ""):
                    continue

            formatted = format_message(msg, channel_name=ch_name, user_map=user_map)
            all_formatted.append(formatted)

            # スレッド展開
            if args.include_threads and msg.get("reply_count", 0) > 0:
                replies = fetch_thread_replies(client, ch_id, msg["ts"])
                for reply in replies:
                    r_formatted = format_message(
                        reply, channel_name=ch_name, user_map=user_map
                    )
                    r_formatted["is_thread_reply"] = True
                    all_formatted.append(r_formatted)

        # レート制限対策
        time.sleep(0.3)

    # 時系列ソート
    all_formatted.sort(key=lambda m: m["timestamp"])

    # 出力
    if args.output == "json":
        print(json.dumps(all_formatted, ensure_ascii=False, indent=2))
    else:
        print(f"=== Slack メッセージ ({len(all_formatted)}件) ===")
        print(f"期間: 直近 {args.days} 日間")
        print(f"チャンネル数: {len(all_channels)}")
        print()
        for msg in all_formatted:
            thread_marker = " [スレッド返信]" if msg.get("is_thread_reply") else ""
            print(f"--- #{msg['channel']} | {msg['user']} | {msg['timestamp']}{thread_marker} ---")
            print(msg["text"])
            if msg["reactions"]:
                reactions_str = "  ".join(
                    f":{r['name']}: x{r['count']}" for r in msg["reactions"]
                )
                print(f"  リアクション: {reactions_str}")
            if msg["thread_reply_count"] > 0 and not msg.get("is_thread_reply"):
                print(f"  → スレッド返信 {msg['thread_reply_count']}件")
            print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Slackメッセージからタスクを抽出するスクリプト。

Slack Web APIを使用してチャンネルのメッセージを取得し、
タスクとして認識できる内容を抽出・整形して出力する。
抽出結果をSlackチャンネルに投稿することも可能。

環境変数:
    SLACK_BOT_TOKEN: Slack Bot User OAuth Token (xoxb-...)
    SLACK_USER_TOKEN: Slack User OAuth Token (xoxp-...) ※Bot Tokenがない場合に使用

使い方:
    python slack_task_extractor.py                          # 参加チャンネルの直近メッセージを取得
    python slack_task_extractor.py --channels general,random # 特定チャンネルのみ
    python slack_task_extractor.py --days 3                  # 直近3日分
    python slack_task_extractor.py --mentions-only           # 自分宛メンションのみ
    python slack_task_extractor.py --dm                      # DMも含める
    python slack_task_extractor.py --extract-tasks           # タスク自動検出モード
    python slack_task_extractor.py --post-to daily-tasks     # 結果をSlackチャンネルに投稿
"""

import argparse
import json
import os
import re
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


# ---------------------------------------------------------------------------
# タスク検出キーワード（日本語 + 英語）
# ---------------------------------------------------------------------------
TASK_PATTERNS_HIGH = [
    r"至急",
    r"緊急",
    r"急ぎ",
    r"ASAP",
    r"今日中",
    r"本日中",
    r"今すぐ",
    r"大至急",
]

TASK_PATTERNS_REQUEST = [
    r"してください",
    r"お願いします",
    r"お願いいたします",
    r"頼みます",
    r"していただけ",
    r"してもらえ",
    r"して頂け",
    r"ご対応",
    r"ご確認",
    r"ご連絡",
    r"ご報告",
    r"ご返信",
    r"ご共有",
    r"レビューお願い",
    r"PR.*お願い",
    r"マージ.*お願い",
    r"デプロイ.*お願い",
    r"please",
    r"could you",
    r"can you",
    r"would you",
]

TASK_PATTERNS_DEADLINE = [
    r"〜?までに",
    r"期限",
    r"締め?切り?",
    r"デッドライン",
    r"deadline",
    r"今週中",
    r"来週まで",
    r"明日まで",
    r"月曜まで",
    r"火曜まで",
    r"水曜まで",
    r"木曜まで",
    r"金曜まで",
    r"\d+月\d+日",
    r"\d+/\d+",
]

TASK_PATTERNS_ACTION = [
    r"TODO",
    r"タスク",
    r"対応[：:]",
    r"要対応",
    r"アクション",
    r"やること",
    r"宿題",
    r"確認事項",
    r"作業依頼",
    r"FIXME",
    r"要修正",
    r"要確認",
]

TASK_PATTERNS_QUESTION = [
    r"でしょうか[？?]",
    r"ですか[？?]",
    r"ますか[？?]",
    r"どうでしょう",
    r"いかがでしょう",
    r"教えてください",
    r"わかりますか",
    r"知っていますか",
]


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
            "       - chat:write        (メッセージ投稿 ※--post-to使用時)\n"
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

    def replace_mention(m: re.Match[str]) -> str:
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


# ---------------------------------------------------------------------------
# タスク検出
# ---------------------------------------------------------------------------

def detect_task_priority(text: str) -> str | None:
    """メッセージテキストからタスクの優先度を判定する。

    Returns:
        "high" / "medium" / "low" / None (タスクでない場合)
    """
    text_lower = text.lower()

    # 高優先度: 緊急キーワード
    for pat in TASK_PATTERNS_HIGH:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "high"

    # 期限付き → 中〜高
    for pat in TASK_PATTERNS_DEADLINE:
        if re.search(pat, text_lower, re.IGNORECASE):
            # 「明日」「今日」を含む場合は高優先度
            if re.search(r"今日|本日|明日|today|tomorrow", text_lower):
                return "high"
            return "medium"

    # アクションアイテム → 中
    for pat in TASK_PATTERNS_ACTION:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "medium"

    # 依頼表現 → 中
    for pat in TASK_PATTERNS_REQUEST:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "medium"

    # 質問 → 低
    for pat in TASK_PATTERNS_QUESTION:
        if re.search(pat, text_lower, re.IGNORECASE):
            return "low"

    return None


def extract_deadline(text: str) -> str:
    """テキストから期限の記述を抽出する。"""
    # 具体的な日付
    m = re.search(r"(\d{1,2}月\d{1,2}日)", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2}/\d{1,2})", text)
    if m:
        return m.group(1)

    # 相対的な期限
    for keyword in ["今日中", "本日中", "今すぐ", "today"]:
        if keyword in text.lower():
            return "今日"
    for keyword in ["明日まで", "明日中", "tomorrow"]:
        if keyword in text.lower():
            return "明日"
    for keyword in ["今週中", "今週末まで", "this week"]:
        if keyword in text.lower():
            return "今週中"
    for keyword in ["来週まで", "来週中", "next week"]:
        if keyword in text.lower():
            return "来週"

    return "-"


def summarize_task(text: str) -> str:
    """メッセージから簡潔なタスク要約を生成する。"""
    # 長すぎるメッセージは先頭部分を使用
    lines = text.strip().split("\n")
    summary = lines[0] if lines else text
    if len(summary) > 80:
        summary = summary[:77] + "..."
    return summary


# ---------------------------------------------------------------------------
# Slack投稿
# ---------------------------------------------------------------------------

def post_to_slack(client: WebClient, channel_name: str, text: str) -> bool:
    """結果をSlackチャンネルに投稿する。"""
    # チャンネルIDを取得
    try:
        channels = list_channels(client)
        target = None
        for ch in channels:
            if ch.get("name") == channel_name or ch.get("name_normalized") == channel_name:
                target = ch
                break

        if not target:
            print(
                f"エラー: チャンネル #{channel_name} が見つかりません。\n"
                f"Botがチャンネルに参加しているか確認してください。",
                file=sys.stderr,
            )
            return False

        client.chat_postMessage(channel=target["id"], text=text, mrkdwn=True)
        print(f"#{channel_name} にタスク一覧を投稿しました。", file=sys.stderr)
        return True

    except SlackApiError as e:
        print(f"エラー: Slack投稿に失敗しました: {e}", file=sys.stderr)
        return False


def format_tasks_for_slack(tasks: list[dict], days: int) -> str:
    """タスク一覧をSlack用のmrkdwn形式に整形する。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f":clipboard: *{today} 今日のタスク一覧* (直近{days}日のSlackから抽出)\n"]

    high = [t for t in tasks if t["priority"] == "high"]
    medium = [t for t in tasks if t["priority"] == "medium"]
    low = [t for t in tasks if t["priority"] == "low"]

    if high:
        lines.append(":red_circle: *高優先度*")
        for i, t in enumerate(high, 1):
            deadline = f" (期限: {t['deadline']})" if t["deadline"] != "-" else ""
            lines.append(f"  {i}. {t['summary']}{deadline}  — _{t['user']}_ in #{t['channel']}")
        lines.append("")

    if medium:
        lines.append(":large_yellow_circle: *中優先度*")
        for i, t in enumerate(medium, 1):
            deadline = f" (期限: {t['deadline']})" if t["deadline"] != "-" else ""
            lines.append(f"  {i}. {t['summary']}{deadline}  — _{t['user']}_ in #{t['channel']}")
        lines.append("")

    if low:
        lines.append(":large_green_circle: *低優先度*")
        for i, t in enumerate(low, 1):
            lines.append(f"  {i}. {t['summary']}  — _{t['user']}_ in #{t['channel']}")
        lines.append("")

    if not tasks:
        lines.append(":tada: タスクは見つかりませんでした！")

    lines.append(f"---\n合計: {len(tasks)}件 (高: {len(high)}, 中: {len(medium)}, 低: {len(low)})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

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
        "--extract-tasks",
        action="store_true",
        help="キーワードベースでタスクを自動検出して出力する",
    )
    parser.add_argument(
        "--post-to",
        type=str,
        default="",
        help="抽出結果を投稿するSlackチャンネル名（例: daily-tasks）",
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

    oldest = (
        datetime.now(timezone.utc) - timedelta(days=args.days)
    ).timestamp()

    all_channels = list_channels(client, include_dm=args.dm)

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
            if msg.get("subtype") in (
                "channel_join",
                "channel_leave",
                "channel_topic",
                "channel_purpose",
                "bot_message",
            ):
                continue

            if args.mentions_only and my_user_id:
                if f"<@{my_user_id}>" not in msg.get("text", ""):
                    continue

            formatted = format_message(msg, channel_name=ch_name, user_map=user_map)
            all_formatted.append(formatted)

            if args.include_threads and msg.get("reply_count", 0) > 0:
                replies = fetch_thread_replies(client, ch_id, msg["ts"])
                for reply in replies:
                    r_formatted = format_message(
                        reply, channel_name=ch_name, user_map=user_map
                    )
                    r_formatted["is_thread_reply"] = True
                    all_formatted.append(r_formatted)

        time.sleep(0.3)

    all_formatted.sort(key=lambda m: m["timestamp"])

    # --extract-tasks モード: タスクを自動検出
    if args.extract_tasks or args.post_to:
        tasks: list[dict] = []
        for msg in all_formatted:
            priority = detect_task_priority(msg["text"])
            if priority:
                tasks.append(
                    {
                        "priority": priority,
                        "summary": summarize_task(msg["text"]),
                        "deadline": extract_deadline(msg["text"]),
                        "user": msg["user"],
                        "channel": msg["channel"],
                        "timestamp": msg["timestamp"],
                        "original_text": msg["text"],
                    }
                )

        # Slackへ投稿
        if args.post_to:
            slack_text = format_tasks_for_slack(tasks, args.days)
            post_to_slack(client, args.post_to.lstrip("#"), slack_text)

        # 端末出力
        if args.output == "json":
            print(json.dumps(tasks, ensure_ascii=False, indent=2))
        else:
            print(f"=== 抽出されたタスク ({len(tasks)}件) ===")
            print(f"期間: 直近 {args.days} 日間")
            print()

            for label, emoji, prio in [
                ("高優先度", "🔴", "high"),
                ("中優先度", "🟡", "medium"),
                ("低優先度", "🟢", "low"),
            ]:
                group = [t for t in tasks if t["priority"] == prio]
                if group:
                    print(f"{emoji} {label} ({len(group)}件)")
                    for i, t in enumerate(group, 1):
                        deadline = f"  期限: {t['deadline']}" if t["deadline"] != "-" else ""
                        print(f"  {i}. {t['summary']}")
                        print(f"     依頼者: {t['user']} | #{t['channel']}{deadline}")
                    print()

            if not tasks:
                print("タスクは見つかりませんでした。")

            print(f"---\n合計: {len(tasks)}件")
        return

    # 通常モード: 全メッセージ出力（Claude Codeのskillで分析用）
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

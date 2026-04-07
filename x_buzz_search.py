#!/usr/bin/env python3
"""X (Twitter) APIでバズっている家電投稿を検索するスクリプト。

使い方:
    # 環境変数にAPIキーを設定してから実行
    export X_BEARER_TOKEN="your_bearer_token_here"

    python x_buzz_search.py                          # デフォルト（全カテゴリ）
    python x_buzz_search.py --category 冷蔵庫        # カテゴリ指定
    python x_buzz_search.py --min-likes 100          # 最低いいね数を指定
    python x_buzz_search.py --days 7                 # 過去7日間（デフォルト）
    python x_buzz_search.py --output results.csv     # 出力ファイル名を指定
"""

import argparse
import csv
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from pathlib import Path

# ---------------------------------------------------------------------------
# 検索カテゴリ定義
# ---------------------------------------------------------------------------
CATEGORIES = {
    "冷蔵庫": [
        "冷蔵庫 おすすめ",
        "冷蔵庫 買い替え",
        "冷蔵庫 最高",
        "冷蔵庫 便利",
    ],
    "調理家電": [
        "調理家電 おすすめ",
        "ホットクック 最高",
        "電気圧力鍋 便利",
        "オーブンレンジ おすすめ",
        "ヘルシオ",
        "自動調理",
    ],
    "掃除機": [
        "ロボット掃除機 おすすめ",
        "掃除機 買ってよかった",
        "ルンバ 最高",
        "ダイソン 掃除機",
    ],
    "洗濯機": [
        "洗濯機 おすすめ",
        "ドラム式洗濯機 最高",
        "洗濯乾燥機 便利",
        "洗濯機 買い替え",
    ],
    "生活家電": [
        "生活家電 おすすめ",
        "空気清浄機 おすすめ",
        "加湿器 最高",
        "ドライヤー 買ってよかった",
        "食洗機 便利",
        "エアコン おすすめ",
    ],
}


def search_tweets(bearer_token: str, query: str, max_results: int = 20,
                  start_time: str | None = None) -> list[dict]:
    """X API v2 でツイートを検索する。

    Args:
        bearer_token: X APIのBearerトークン
        query: 検索クエリ
        max_results: 最大取得件数（10-100）
        start_time: 検索開始日時（ISO 8601形式）

    Returns:
        ツイート情報のリスト
    """
    base_url = "https://api.twitter.com/2/tweets/search/recent"

    params = {
        "query": f"{query} -is:retweet lang:ja has:media",
        "max_results": min(max_results, 100),
        "tweet.fields": "public_metrics,created_at,author_id,entities",
        "user.fields": "name,username,public_metrics",
        "expansions": "author_id,attachments.media_keys",
        "media.fields": "type,url,preview_image_url,public_metrics",
    }

    if start_time:
        params["start_time"] = start_time

    url = f"{base_url}?{urlencode(params)}"

    req = Request(url)
    req.add_header("Authorization", f"Bearer {bearer_token}")
    req.add_header("User-Agent", "BuzzSearchBot/1.0")

    try:
        with urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.readable() else ""
        if e.code == 401:
            print("エラー: APIキーが無効です。Bearer Tokenを確認してください。")
        elif e.code == 403:
            print("エラー: APIアクセス権限がありません。X Developer Portalで権限を確認してください。")
        elif e.code == 429:
            print("エラー: API制限に達しました。しばらく待ってから再実行してください。")
        else:
            print(f"APIエラー ({e.code}): {error_body}")
        return []
    except URLError as e:
        print(f"ネットワークエラー: {e.reason}")
        return []

    if "data" not in data:
        return []

    # ユーザー情報をマッピング
    users = {}
    if "includes" in data and "users" in data["includes"]:
        for user in data["includes"]["users"]:
            users[user["id"]] = user

    # メディア情報をマッピング
    media_map = {}
    if "includes" in data and "media" in data["includes"]:
        for m in data["includes"]["media"]:
            media_map[m["media_key"]] = m

    results = []
    for tweet in data["data"]:
        metrics = tweet.get("public_metrics", {})
        author = users.get(tweet.get("author_id", ""), {})

        # メディアタイプ判定
        media_types = set()
        if "attachments" in tweet and "media_keys" in tweet["attachments"]:
            for key in tweet["attachments"]["media_keys"]:
                if key in media_map:
                    media_types.add(media_map[key].get("type", "unknown"))

        media_label = "動画" if "video" in media_types else "画像" if "photo" in media_types else "不明"

        # ツイートURLを構築
        username = author.get("username", "")
        tweet_url = f"https://x.com/{username}/status/{tweet['id']}" if username else ""

        results.append({
            "text": tweet.get("text", "")[:200],
            "likes": metrics.get("like_count", 0),
            "retweets": metrics.get("retweet_count", 0),
            "replies": metrics.get("reply_count", 0),
            "views": metrics.get("impression_count", 0),
            "author_name": author.get("name", ""),
            "author_username": username,
            "author_followers": author.get("public_metrics", {}).get("followers_count", 0),
            "created_at": tweet.get("created_at", ""),
            "media_type": media_label,
            "url": tweet_url,
        })

    return results


def search_category(bearer_token: str, category: str, keywords: list[str],
                    min_likes: int, days: int) -> list[dict]:
    """1カテゴリ分の検索を実行し、いいね数でソートして返す。"""
    start_time = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_tweets = []
    seen_ids = set()

    for keyword in keywords:
        print(f"  検索中: {keyword} ...", end=" ", flush=True)
        tweets = search_tweets(bearer_token, keyword, max_results=20, start_time=start_time)
        new_count = 0
        for t in tweets:
            tweet_id = t["url"]
            if tweet_id not in seen_ids and t["likes"] >= min_likes:
                seen_ids.add(tweet_id)
                t["category"] = category
                all_tweets.append(t)
                new_count += 1
        print(f"{new_count}件")

    # いいね数でソート
    all_tweets.sort(key=lambda x: x["likes"], reverse=True)
    return all_tweets


def save_csv(results: list[dict], output_path: str) -> None:
    """結果をCSVファイルに保存する。"""
    fieldnames = [
        "カテゴリ", "投稿内容", "メディア", "いいね数", "リツイート数",
        "リプライ数", "表示回数", "投稿者", "フォロワー数",
        "投稿日時", "URL",
    ]

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "カテゴリ": r["category"],
                "投稿内容": r["text"],
                "メディア": r["media_type"],
                "いいね数": r["likes"],
                "リツイート数": r["retweets"],
                "リプライ数": r["replies"],
                "表示回数": r["views"],
                "投稿者": f"{r['author_name']} (@{r['author_username']})",
                "フォロワー数": r["author_followers"],
                "投稿日時": r["created_at"],
                "URL": r["url"],
            })

    print(f"\n結果を {output_path} に保存しました。")


def display_results(results: list[dict], top_n: int = 10) -> None:
    """結果をターミナルに表示する。"""
    if not results:
        print("\n該当する投稿が見つかりませんでした。")
        return

    print(f"\n{'='*70}")
    print(f" バズ投稿 TOP {min(top_n, len(results))}（いいね数順）")
    print(f"{'='*70}")

    for i, r in enumerate(results[:top_n], 1):
        print(f"\n--- #{i} [{r['category']}] {r['media_type']} ---")
        print(f"  投稿: {r['text'][:100]}...")
        print(f"  ❤ {r['likes']:,}  🔁 {r['retweets']:,}  💬 {r['replies']:,}  👁 {r['views']:,}")
        print(f"  投稿者: {r['author_name']} (@{r['author_username']}) "
              f"フォロワー: {r['author_followers']:,}")
        print(f"  URL: {r['url']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="X (Twitter) APIで家電カテゴリのバズ投稿を検索します。"
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help=f"検索カテゴリ ({', '.join(CATEGORIES.keys())}）。未指定で全カテゴリ検索",
    )
    parser.add_argument(
        "--min-likes",
        type=int,
        default=50,
        help="最低いいね数（デフォルト: 50）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="過去何日間を検索するか（デフォルト: 7）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="表示する上位件数（デフォルト: 10）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="CSV出力ファイル名（未指定で自動生成）",
    )
    args = parser.parse_args()

    # Bearer Token取得（token.txt > 環境変数 の優先順）
    bearer_token = None

    # 1. スクリプトと同じフォルダの token.txt から読み込み
    token_file = Path(__file__).parent / "token.txt"
    if token_file.exists():
        bearer_token = token_file.read_text(encoding="utf-8").strip()
        if bearer_token:
            print(f"トークンを {token_file} から読み込みました。")

    # 2. 環境変数から取得
    if not bearer_token:
        bearer_token = os.environ.get("X_BEARER_TOKEN")

    if not bearer_token:
        print("エラー: Bearer Tokenが見つかりません。")
        print()
        print("設定方法（どちらか1つ）:")
        print()
        print("  方法1: token.txt ファイルにトークンを書く（おすすめ）")
        print(f"    → {token_file} にトークンを貼り付けて保存")
        print()
        print("  方法2: 環境変数に設定")
        print("    Windows: set X_BEARER_TOKEN=your_token")
        print("    Mac/Linux: export X_BEARER_TOKEN=\"your_token\"")
        sys.exit(1)

    # 検索カテゴリ決定
    if args.category:
        if args.category not in CATEGORIES:
            print(f"エラー: 不明なカテゴリ「{args.category}」")
            print(f"使用可能: {', '.join(CATEGORIES.keys())}")
            sys.exit(1)
        targets = {args.category: CATEGORIES[args.category]}
    else:
        targets = CATEGORIES

    # 検索実行
    today = datetime.now().strftime("%Y%m%d")
    print(f"X (Twitter) バズ投稿検索")
    print(f"期間: 過去{args.days}日間 / 最低いいね数: {args.min_likes}")
    print(f"対象カテゴリ: {', '.join(targets.keys())}")
    print(f"{'='*50}")

    all_results = []
    for category, keywords in targets.items():
        print(f"\n■ {category}")
        results = search_category(bearer_token, category, keywords, args.min_likes, args.days)
        all_results.extend(results)

    # いいね数で全体ソート
    all_results.sort(key=lambda x: x["likes"], reverse=True)

    # 表示
    display_results(all_results, top_n=args.top)

    # CSV保存
    if all_results:
        cat_label = args.category if args.category else "家電全般"
        output_path = args.output or f"buzz_results_{cat_label}_{today}.csv"
        save_csv(all_results, output_path)

    print(f"\n合計: {len(all_results)}件のバズ投稿を取得しました。")


if __name__ == "__main__":
    main()

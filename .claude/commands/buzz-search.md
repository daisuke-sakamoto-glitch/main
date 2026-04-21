X (Twitter) APIを使って、家電カテゴリのバズ投稿（動画・画像付き）を検索します。

カテゴリ: $ARGUMENTS
（未指定の場合は全カテゴリ: 冷蔵庫, 調理家電, 掃除機, 洗濯機, 生活家電）

## やること

1. まず環境変数 `X_BEARER_TOKEN` が設定されているか確認してください
   - 設定されていなければ、ユーザーにAPIキーの取得方法を案内してください

2. 以下のコマンドでスクリプトを実行してください：
   - カテゴリ指定あり: `python x_buzz_search.py --category "$ARGUMENTS" --min-likes 50`
   - カテゴリ指定なし: `python x_buzz_search.py --min-likes 50`

3. 結果をユーザーにわかりやすくまとめてください：
   - いいね数・リツイート数・再生数が多い順に紹介
   - 各投稿のURLを含める
   - CSVファイルの保存先を案内

## APIキーが未設定の場合の案内

以下の手順をユーザーに伝えてください：

1. https://developer.x.com/ にアクセスしてアカウント作成（無料）
2. 「Projects & Apps」から新しいアプリを作成
3. 「Keys and tokens」ページで「Bearer Token」をコピー
4. ターミナルで以下を実行：
   ```
   export X_BEARER_TOKEN="コピーしたトークン"
   ```
5. その後 `/buzz-search` を再実行

# Slack タスク抽出ツール セットアップガイド

初心者向けに、一つずつ手順を説明します。

---

## 全体像

```
┌─────────────────────────────────────────────────────┐
│  あなたのSlack ワークスペース                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │ #general  │ │ #project │ │  DM      │             │
│  │ メッセージ │ │ メッセージ │ │ メッセージ │             │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘             │
│        └────────────┼────────────┘                   │
│                     ▼                                │
│         Slack Bot (APIで取得)                         │
└─────────────────────┬───────────────────────────────┘
                      ▼
            ┌──────────────────┐
            │ slack_task_       │
            │ extractor.py     │
            │ (メッセージ取得)   │
            └────────┬─────────┘
                     ▼
         ┌───────────────────────┐
         │ Claude Code           │
         │ (AIがタスクを分析)      │
         │                       │
         │  /slack-tasks で実行   │
         └───────────┬───────────┘
                     ▼
           タスク一覧を表示！
           ＆ Slackにも投稿可能
```

---

## ステップ1: Slack Appを作成する

### 1-1. Slack APIサイトを開く

ブラウザで以下のURLを開きます:

```
https://api.slack.com/apps
```

### 1-2. アプリを作成する

1. **「Create New App」** ボタンをクリック
2. **「From scratch」** を選択
3. 以下を入力:
   - **App Name**: `タスク抽出Bot`（好きな名前でOK）
   - **Pick a workspace**: あなたのSlackワークスペースを選択
4. **「Create App」** をクリック

### 1-3. 権限（スコープ）を設定する

1. 左メニューの **「OAuth & Permissions」** をクリック
2. **「Scopes」** セクションまでスクロール
3. **「Bot Token Scopes」** の **「Add an OAuth Scope」** をクリック
4. 以下のスコープを一つずつ追加:

| スコープ | 何ができるか |
|---------|------------|
| `channels:history` | パブリックチャンネルのメッセージを読む |
| `channels:read` | チャンネルの一覧を見る |
| `groups:history` | プライベートチャンネルのメッセージを読む |
| `groups:read` | プライベートチャンネルの一覧を見る |
| `im:history` | DMのメッセージを読む |
| `im:read` | DMの一覧を見る |
| `users:read` | ユーザー名を取得する |
| `chat:write` | メッセージを投稿する（毎朝通知を使う場合） |

### 1-4. ワークスペースにインストール

1. ページ上部の **「Install to Workspace」** をクリック
2. 権限を確認して **「許可する」** をクリック
3. **「Bot User OAuth Token」** が表示されます
4. `xoxb-` で始まるトークンを **コピー** してメモしておく

> ⚠️ このトークンは秘密情報です。他の人に教えないでください。

### 1-5. Botをチャンネルに招待する

読みたいチャンネルで、以下のメッセージを送信:

```
/invite @タスク抽出Bot
```

（アプリ名を変えた場合はその名前で）

---

## ステップ2: PCの環境を準備する

### 2-1. Python パッケージをインストール

ターミナル（コマンドプロンプト）で以下を実行:

```bash
pip install slack_sdk
```

### 2-2. トークンを設定する

ステップ1-4でコピーしたトークンを設定します。

**Mac / Linux の場合:**

```bash
# ターミナルで実行
export SLACK_BOT_TOKEN='xoxb-ここにトークンを貼り付け'
```

毎回設定するのが面倒な場合、シェル設定ファイルに追加:

```bash
# Mac
echo "export SLACK_BOT_TOKEN='xoxb-あなたのトークン'" >> ~/.zshrc
source ~/.zshrc

# Linux
echo "export SLACK_BOT_TOKEN='xoxb-あなたのトークン'" >> ~/.bashrc
source ~/.bashrc
```

**Windows の場合:**

```powershell
# PowerShellで実行
$env:SLACK_BOT_TOKEN = "xoxb-ここにトークンを貼り付け"

# 永続化する場合:
[System.Environment]::SetEnvironmentVariable("SLACK_BOT_TOKEN", "xoxb-あなたのトークン", "User")
```

---

## ステップ3: 動作確認する

### 3-1. スクリプトを直接実行してみる

```bash
# このリポジトリのディレクトリで実行
python slack_task_extractor.py --days 1
```

メッセージが表示されれば成功です！

### 3-2. タスク自動検出を試す

```bash
python slack_task_extractor.py --days 1 --extract-tasks
```

「してください」「お願いします」などのキーワードを含むメッセージが
タスクとして自動検出されます。

### 3-3. Claude Code のSkillとして使う

Claude Code を起動して以下を入力:

```
/slack-tasks
```

Claude が Slack メッセージを取得し、AIの判断でタスクを抽出・整理してくれます。
（キーワードだけでなく、文脈を理解した高精度な抽出ができます）

---

## ステップ4: 毎朝自動でタスクを受け取る

2つの方法があります。お好みで選んでください。

### 方法A: Slackチャンネルに毎朝投稿する（おすすめ）

Slackに `#daily-tasks` のようなチャンネルを作り、毎朝自動でタスク一覧を投稿します。

#### A-1. 投稿先チャンネルを作る

Slackで新しいチャンネルを作成:
- チャンネル名: `daily-tasks`（好きな名前でOK）
- Botを招待: `/invite @タスク抽出Bot`

#### A-2. 手動で投稿テスト

```bash
python slack_task_extractor.py --days 1 --extract-tasks --post-to daily-tasks
```

`#daily-tasks` にタスク一覧が投稿されれば成功！

#### A-3. 毎朝自動実行を設定する（cron）

**Mac / Linux の場合:**

```bash
# cron設定を開く
crontab -e
```

以下の行を追加（毎朝9:00に実行）:

```
0 9 * * 1-5 SLACK_BOT_TOKEN='xoxb-あなたのトークン' /usr/bin/python3 /path/to/slack_task_extractor.py --days 1 --extract-tasks --include-threads --post-to daily-tasks
```

> `/path/to/` はこのリポジトリの実際のパスに置き換えてください。
> `1-5` は月〜金のみ。毎日にするなら `*` に変更。

**Windows の場合:**

1. 「タスクスケジューラ」を開く（スタートメニューで検索）
2. 「基本タスクの作成」をクリック
3. 名前: `Slack タスク抽出`
4. トリガー: 毎日 9:00
5. 操作: プログラムの開始
   - プログラム: `python`
   - 引数: `slack_task_extractor.py --days 1 --extract-tasks --post-to daily-tasks`
   - 開始: このリポジトリのフォルダパス

### 方法B: Claude Code 起動時に表示する

Claude Code を開くと、自動でリマインドが表示されます。
（`.claude/settings.json` に設定済み）

`/slack-tasks` と打つだけでタスク一覧が表示されます。

---

## よくある質問

### Q: 「チャンネルが見つかりません」と表示される
**A:** Botがチャンネルに参加していない可能性があります。
対象チャンネルで `/invite @タスク抽出Bot` を実行してください。

### Q: 特定のチャンネルだけ見たい
**A:** `--channels` オプションを使います:
```bash
python slack_task_extractor.py --channels general,project-x
```

### Q: 自分宛のメンションだけ見たい
**A:** `--mentions-only` オプションを使います:
```bash
python slack_task_extractor.py --mentions-only
```

### Q: DMも含めたい
**A:** `--dm` オプションを追加:
```bash
python slack_task_extractor.py --dm
```

### Q: トークンが漏れた場合は？
**A:** すぐに https://api.slack.com/apps でトークンを再生成してください。
古いトークンは自動的に無効化されます。

---

## コマンド早見表

| やりたいこと | コマンド |
|------------|---------|
| 今日のメッセージを見る | `python slack_task_extractor.py` |
| 3日分のタスクを抽出 | `python slack_task_extractor.py --days 3 --extract-tasks` |
| 自分宛のみ | `python slack_task_extractor.py --mentions-only` |
| Slackに結果を投稿 | `python slack_task_extractor.py --extract-tasks --post-to daily-tasks` |
| Claude Codeで分析 | `/slack-tasks` |
| Claude Codeで特定ch | `/slack-tasks --channels general --days 3` |

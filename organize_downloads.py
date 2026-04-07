#!/usr/bin/env python3
"""ダウンロードフォルダをキーワードベースで案件別に整理するスクリプト。

整理ロジック:
  1. ファイル名から共通キーワードを抽出し、同じキーワードを持つファイルを
     案件フォルダとしてまとめる（例: "ABC商事" が含まれるファイル → ABC商事/）
  2. 案件に該当しないファイルはファイル名のキーワードで内容別カテゴリに振り分け
     （見積・請求書、採用関連、契約関連 など）
  3. どれにも該当しないファイルは「その他」フォルダへ

使い方:
    python organize_downloads.py                  # ~/Downloads を整理
    python organize_downloads.py /path/to/folder  # 指定フォルダを整理
    python organize_downloads.py --dry-run        # 実際には移動せず確認のみ
    python organize_downloads.py --min-group 2    # 案件グループの最小ファイル数を変更
"""

import argparse
import io
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# Windows環境での文字化け・エンコードエラーを防止
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 内容別カテゴリ: ファイル名に含まれるキーワード → カテゴリ名
# 案件グループに該当しなかったファイルをここで分類する
# ---------------------------------------------------------------------------
CONTENT_CATEGORIES = [
    {
        "name": "見積・請求書",
        "keywords": [
            "見積", "見積り", "見積もり", "estimate",
            "請求", "請求書", "invoice",
            "納品", "納品書", "delivery",
            "領収", "領収書", "receipt",
        ],
    },
    {
        "name": "採用関連",
        "keywords": [
            "履歴書", "職務経歴", "経歴書", "resume", "cv",
            "採用", "応募", "面接", "recruit",
            "エントリー", "entry",
            "ポートフォリオ", "portfolio",
        ],
    },
    {
        "name": "契約関連",
        "keywords": [
            "契約", "contract", "agreement",
            "覚書", "mou",
            "秘密保持", "nda", "機密",
            "委託", "業務委託",
            "利用規約", "terms",
        ],
    },
    {
        "name": "経理・税務",
        "keywords": [
            "経費", "精算", "expense",
            "税", "確定申告", "tax",
            "給与", "給料", "salary", "payroll",
            "源泉", "年末調整",
            "決算", "会計",
        ],
    },
    {
        "name": "議事録・会議資料",
        "keywords": [
            "議事録", "minutes",
            "会議", "meeting", "mtg",
            "アジェンダ", "agenda",
            "報告書", "report",
            "週報", "日報", "月報",
        ],
    },
    {
        "name": "マニュアル・資料",
        "keywords": [
            "マニュアル", "manual",
            "手順書", "手順",
            "仕様書", "spec", "specification",
            "設計書", "design",
            "ガイド", "guide",
            "研修", "training",
        ],
    },
]

# 案件キーワード抽出時に除外する一般的な語（ノイズ除去）
STOP_WORDS = {
    # 日本語の一般語
    "の", "に", "は", "を", "と", "が", "で", "から", "まで", "より",
    "について", "における", "に関する", "向け", "用",
    "新", "旧", "案", "版", "最終", "最新", "修正", "更新", "確認",
    "final", "draft", "copy", "new", "old", "rev", "ver",
    # 日付パターンに近い語
    "月", "日", "年",
    # ファイル整理で意味のない語
    "ダウンロード", "download", "downloads",
    "コピー", "backup", "tmp", "temp",
}

# 日付っぽいパターン（除外用）
DATE_PATTERN = re.compile(
    r"^(20[0-9]{2}|[0-9]{8}|[0-9]{6}|[0-9]{4}[01][0-9][0-3][0-9])$"
)


def tokenize_filename(file_path: Path) -> list[str]:
    """ファイル名（拡張子除く）を区切り文字でトークンに分割する。"""
    stem = file_path.stem
    # 記号・スペースで分割し、各セグメントをそのまま保持
    tokens = re.split(r"[_\-.\s　()（）\[\]【】{}「」『』]+", stem)
    # 数字のみのトークン・ストップワード・日付パターンを除外
    return [
        t for t in tokens
        if t and len(t) >= 2 and t.lower() not in STOP_WORDS and not DATE_PATTERN.match(t) and not t.isdigit()
    ]


def extract_project_keywords(files: list[Path], min_group: int) -> dict[str, list[Path]]:
    """ファイル名の共通キーワードから案件グループを抽出する。

    同じキーワード（トークン）を含むファイルが min_group 個以上あれば
    案件グループとみなす。複数のキーワードに該当する場合は、
    最も多くのトークンが一致するグループに割り当てる。
    """
    # 各トークンがどのファイルに出現するか集計
    token_to_files: dict[str, set[Path]] = defaultdict(set)
    file_tokens: dict[Path, set[str]] = {}

    for f in files:
        tokens = set(tokenize_filename(f))
        file_tokens[f] = tokens
        for token in tokens:
            token_to_files[token].add(f)

    # min_group 以上のファイルに共通するトークンを案件キーワード候補とする
    candidate_keywords = {
        token: file_set
        for token, file_set in token_to_files.items()
        if len(file_set) >= min_group and len(token) >= 2
    }

    if not candidate_keywords:
        return {}

    # 共通ファイル集合が同じキーワード同士をマージして案件名にする
    # (例: "ABC" と "商事" が完全に同じファイル群なら "ABC商事" にまとめる)
    fileset_to_keywords: dict[frozenset, list[str]] = defaultdict(list)
    for token, file_set in candidate_keywords.items():
        key = frozenset(file_set)
        fileset_to_keywords[key].append(token)

    # 各ファイルをスコアが最も高いグループに割り当て
    groups: dict[str, list[Path]] = {}
    assigned: set[Path] = set()

    # グループをファイル数の多い順にソート
    sorted_groups = sorted(fileset_to_keywords.items(), key=lambda x: len(x[0]), reverse=True)

    for file_set_frozen, keywords in sorted_groups:
        # グループ名: キーワードをファイル名に登場する順で結合
        group_name = _build_group_name(keywords, files)
        group_files = [f for f in file_set_frozen if f not in assigned]

        if len(group_files) >= min_group:
            groups[group_name] = sorted(group_files, key=lambda f: f.name)
            assigned.update(group_files)

    return groups


def _build_group_name(keywords: list[str], files: list[Path]) -> str:
    """キーワード群から読みやすいグループ名を作る。"""
    if len(keywords) == 1:
        return keywords[0]

    # 最初のファイル名における出現順でソート
    reference = files[0].stem
    keywords_sorted = sorted(keywords, key=lambda k: reference.find(k) if reference.find(k) >= 0 else 999)
    return "".join(keywords_sorted)


def classify_by_content(file_path: Path) -> str | None:
    """ファイル名のキーワードで内容別カテゴリに分類する。"""
    name_lower = file_path.stem.lower()
    for category in CONTENT_CATEGORIES:
        for keyword in category["keywords"]:
            if keyword.lower() in name_lower:
                return category["name"]
    return None


def resolve_conflict(dest: Path) -> Path:
    """同名ファイルが存在する場合、連番を付けて回避する。"""
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        new_name = f"{stem} ({counter}){suffix}"
        new_dest = parent / new_name
        if not new_dest.exists():
            return new_dest
        counter += 1


def organize(target_dir: Path, dry_run: bool = False, min_group: int = 2) -> None:
    """target_dir 内のファイルをキーワードベースで整理する。"""
    if not target_dir.is_dir():
        print(f"エラー: '{target_dir}' はディレクトリではありません。")
        return

    files = [f for f in target_dir.iterdir() if f.is_file()]

    if not files:
        print("整理するファイルがありません。")
        return

    moved_count = 0

    # --- ステップ1: 案件キーワードでグルーピング ---
    project_groups = extract_project_keywords(files, min_group)
    assigned_files: set[Path] = set()

    if project_groups:
        print("■ 案件別フォルダ:")
        for group_name, group_files in sorted(project_groups.items()):
            print(f"\n  [{group_name}]")
            for f in group_files:
                dest_dir = target_dir / group_name
                dest = resolve_conflict(dest_dir / f.name)
                if dry_run:
                    print(f"    [dry-run] {f.name}")
                else:
                    dest_dir.mkdir(exist_ok=True)
                    shutil.move(str(f), str(dest))
                    print(f"    {f.name}")
                moved_count += 1
                assigned_files.add(f)

    # --- ステップ2: 残りを内容別カテゴリで分類 ---
    remaining = [f for f in files if f not in assigned_files]
    content_groups: dict[str, list[Path]] = defaultdict(list)
    uncategorized: list[Path] = []

    for f in remaining:
        category = classify_by_content(f)
        if category:
            content_groups[category].append(f)
        else:
            uncategorized.append(f)

    if content_groups:
        print("\n■ 内容別フォルダ:")
        for category, cat_files in sorted(content_groups.items()):
            print(f"\n  [{category}]")
            for f in sorted(cat_files, key=lambda x: x.name):
                dest_dir = target_dir / category
                dest = resolve_conflict(dest_dir / f.name)
                if dry_run:
                    print(f"    [dry-run] {f.name}")
                else:
                    dest_dir.mkdir(exist_ok=True)
                    shutil.move(str(f), str(dest))
                    print(f"    {f.name}")
                moved_count += 1

    # --- ステップ3: どこにも該当しないファイル ---
    if uncategorized:
        print("\n■ その他:")
        for f in sorted(uncategorized, key=lambda x: x.name):
            dest_dir = target_dir / "その他"
            dest = resolve_conflict(dest_dir / f.name)
            if dry_run:
                print(f"    [dry-run] {f.name}")
            else:
                dest_dir.mkdir(exist_ok=True)
                shutil.move(str(f), str(dest))
                print(f"    {f.name}")
            moved_count += 1

    label = "移動予定" if dry_run else "移動完了"
    print(f"\n{label}: {moved_count} ファイル")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ダウンロードフォルダをキーワードベースで案件別に整理します。"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(Path.home() / "Downloads"),
        help="整理対象のディレクトリ (デフォルト: ~/Downloads)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際にはファイルを移動せず、何が行われるか表示のみ",
    )
    parser.add_argument(
        "--min-group",
        type=int,
        default=2,
        help="案件グループとみなす最小ファイル数 (デフォルト: 2)",
    )
    args = parser.parse_args()

    target = Path(args.directory).expanduser().resolve()
    print(f"対象フォルダ: {target}")
    print(f"案件グループ最小ファイル数: {args.min_group}")

    if args.dry_run:
        print("(dry-run モード: ファイルは移動されません)\n")
    else:
        print()

    organize(target, dry_run=args.dry_run, min_group=args.min_group)


if __name__ == "__main__":
    main()

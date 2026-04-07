#!/usr/bin/env python3
"""ダウンロードフォルダをキーワードベースで案件別に整理するスクリプト。

整理ロジック:
  1. ファイル名から日付や一般語を除外し、案件固有のキーワードを抽出
  2. 同じキーワードを含むファイルを案件フォルダとしてまとめる
  3. 案件に該当しないファイルは内容別カテゴリに振り分け
  4. どれにも該当しないファイルは「その他」フォルダへ

使い方:
    python organize_downloads.py                  # ~/Downloads を整理
    python organize_downloads.py /path/to/folder  # 指定フォルダを整理
    python organize_downloads.py --dry-run        # 実際には移動せず確認のみ
    python organize_downloads.py --min-group 2    # 案件グループの最小ファイル数を変更
"""

import argparse
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 内容別カテゴリ: ファイル名に含まれるキーワード → カテゴリ名
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

# 案件名として意味のない一般的な語（除外対象）
STOP_WORDS = {
    # 日本語の一般語・助詞
    "の", "に", "は", "を", "と", "が", "で", "から", "まで", "より",
    "について", "における", "に関する", "向け", "用",
    # バージョン・状態
    "新", "旧", "案", "版", "最終", "最新", "修正", "更新", "確認",
    "final", "draft", "copy", "new", "old", "rev", "ver",
    # 日付
    "月", "日", "年",
    # ファイル管理
    "ダウンロード", "download", "downloads",
    "コピー", "backup", "tmp", "temp",
    # 資料の種類（案件名ではなく一般的な文書種類）
    "資料", "追加資料", "定例会資料", "定例会", "定例",
    "提案書", "提案", "企画書", "企画",
    "報告書", "報告", "運用報告",
    "見積", "見積書", "見積もり", "見積り",
    "請求", "請求書", "納品書", "領収書",
    "契約書", "覚書", "議事録",
    "概要", "詳細", "一覧", "まとめ", "サマリー",
    "ご提案", "ご提案書", "ご相談", "ご確認",
    "御中", "御提案書", "御提案",
    "施策", "プロモーション施策",
    "追加", "補足", "参考", "別紙", "添付",
    "完成", "完了", "対応", "依頼", "共有",
    "初稿", "2稿", "3稿", "最終稿",
    "修正版", "確認用", "送付用", "提出用", "社内用",
    "プレゼン", "ミーティング",
    "運用", "X運用", "運用御提案書", "X運用御提案書",
    "X運用ご提案書", "運用ご提案書",
    "プロモーション",
    "image", "img", "photo", "screen", "screenshot",
    "test", "sample", "demo",
}

# 日付パターン（ファイル名の先頭・末尾によくある）
DATE_PATTERNS = [
    re.compile(r"^[0-9]{8}$"),          # 20240301
    re.compile(r"^[0-9]{6}$"),          # 240301
    re.compile(r"^[0-9]{4}$"),          # 0301
    re.compile(r"^20[0-9]{2}$"),        # 2024
    re.compile(r"^[0-9]{2,4}年$"),      # 2024年, 24年
    re.compile(r"^[0-9]{1,2}月$"),      # 3月
    re.compile(r"^[0-9]{4}[01][0-9]$"), # 202403
    re.compile(r"^\d+稿$"),             # 2稿, 3稿
]

# 数字のみ or 1文字のトークンを除外するパターン
NOISE_PATTERN = re.compile(r"^[0-9]+$|^.$")


def _is_noise_token(token: str) -> bool:
    """案件名として意味のないトークンかどうか判定する。"""
    if not token:
        return True
    if token.lower() in STOP_WORDS:
        return True
    if NOISE_PATTERN.match(token):
        return True
    for pat in DATE_PATTERNS:
        if pat.match(token):
            return True
    return False


def tokenize_filename(file_path: Path) -> list[str]:
    """ファイル名（拡張子除く）を区切り文字でトークンに分割し、ノイズを除去する。"""
    stem = file_path.stem
    # 記号・スペースで分割
    tokens = re.split(r"[_\-.\s　()（）\[\]【】{}「」『』｜|／/,，]+", stem)
    # 先頭の日付っぽい数字を除去（例: "0204追加資料" → "0204" と "追加資料"）
    cleaned = []
    for t in tokens:
        # トークン先頭の日付っぽい数字を分離（例: "0310定例会資料" → "0310", "定例会資料"）
        # ただし "100万" のように数字+単位は分離しない
        m = re.match(r"^(\d{2,8})([\u3000-\u9FFFa-zA-Z].+)$", t)
        if m:
            num_part, text_part = m.group(1), m.group(2)
            # 数字+「万」「億」「千」などの単位は分離せず1トークンとして保持
            if re.match(r"^\d+[万億千百]", t):
                if not _is_noise_token(t):
                    cleaned.append(t)
                continue
            if not _is_noise_token(num_part):
                cleaned.append(num_part)
            if not _is_noise_token(text_part):
                cleaned.append(text_part)
        else:
            if not _is_noise_token(t):
                cleaned.append(t)
    return cleaned


def _clean_filename_for_matching(file_path: Path) -> str:
    """ファイル名から日付・記号を除去し、マッチング用の文字列を返す。"""
    stem = file_path.stem
    # 先頭の日付を除去 (0204, 20240301, 251127 など)
    stem = re.sub(r"^\d{2,8}", "", stem)
    # 末尾の日付を除去
    stem = re.sub(r"\d{4,8}$", "", stem)
    # 記号を除去
    stem = re.sub(r"[_\-.\s　()（）\[\]【】{}「」『』｜|／/,，\d]+", "", stem)
    return stem


def extract_project_keywords(files: list[Path], min_group: int) -> dict[str, list[Path]]:
    """ファイル名の共通キーワードから案件グループを抽出する。

    各トークンを案件キーワード候補とし、そのキーワードがファイル名に
    「部分一致」するファイルをグループにまとめる。
    キーワードが別のキーワードの部分文字列である場合は短い方を優先し、
    より多くのファイルを1つのグループにまとめる。
    """
    # 各トークンがどのファイルに出現するか集計（トークン完全一致）
    token_to_files: dict[str, set[Path]] = defaultdict(set)

    for f in files:
        tokens = set(tokenize_filename(f))
        for token in tokens:
            token_to_files[token].add(f)

    # min_group 以上のファイルに共通するトークンを候補とする
    candidate_keywords = {
        token: file_set
        for token, file_set in token_to_files.items()
        if len(file_set) >= min_group and len(token) >= 2
    }

    if not candidate_keywords:
        return {}

    # キーワードAがキーワードBの部分文字列なら、Bに該当するファイルもAに含める
    # （例: "バディエディ" ⊂ "バディエディプロモーション施策"）
    merged_keywords: dict[str, set[Path]] = {}
    for keyword, file_set in candidate_keywords.items():
        # このキーワードがファイル名に部分一致するファイルをすべて集める
        matching_files: set[Path] = set()
        for f in files:
            if keyword in f.stem:
                matching_files.add(f)
        if len(matching_files) >= min_group:
            merged_keywords[keyword] = matching_files

    if not merged_keywords:
        return {}

    # 短いキーワードが長いキーワードの部分文字列なら、短い方に統合
    # まず短い順にソートし、長いキーワードを吸収していく
    keywords_by_length = sorted(merged_keywords.keys(), key=len)
    absorbed: set[str] = set()

    for i, short_kw in enumerate(keywords_by_length):
        if short_kw in absorbed:
            continue
        for long_kw in keywords_by_length[i + 1:]:
            if long_kw in absorbed:
                continue
            if short_kw in long_kw:
                # 長いキーワードのファイルを短い方に統合
                merged_keywords[short_kw] |= merged_keywords[long_kw]
                absorbed.add(long_kw)

    # 吸収されたキーワードを除去
    for kw in absorbed:
        del merged_keywords[kw]

    # ファイル数の多い順にソートして割り当て
    sorted_keywords = sorted(merged_keywords.items(), key=lambda x: len(x[1]), reverse=True)

    groups: dict[str, list[Path]] = {}
    assigned: set[Path] = set()

    for keyword, file_set in sorted_keywords:
        unassigned = [f for f in file_set if f not in assigned]
        if len(unassigned) >= min_group:
            groups[keyword] = sorted(unassigned, key=lambda f: f.name)
            assigned.update(unassigned)

    return groups


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
            print(f"\n  [{group_name}] ({len(group_files)}件)")
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
            print(f"\n  [{category}] ({len(cat_files)}件)")
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
        print(f"\n■ その他 ({len(uncategorized)}件):")
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
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="結果をファイルに保存 (例: --output result.txt)",
    )
    args = parser.parse_args()

    # --output が指定されたら、printの出力先をファイルに切り替える
    original_stdout = sys.stdout
    if args.output:
        output_path = Path(args.output).resolve()
        sys.stdout = open(output_path, "w", encoding="utf-8")

    target = Path(args.directory).expanduser().resolve()
    print(f"対象フォルダ: {target}")
    print(f"案件グループ最小ファイル数: {args.min_group}")

    if args.dry_run:
        print("(dry-run モード: ファイルは移動されません)\n")
    else:
        print()

    organize(target, dry_run=args.dry_run, min_group=args.min_group)

    if args.output:
        sys.stdout.close()
        sys.stdout = original_stdout
        print(f"結果を {output_path} に保存しました。")


if __name__ == "__main__":
    main()

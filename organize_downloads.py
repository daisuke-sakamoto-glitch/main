#!/usr/bin/env python3
"""ダウンロードフォルダを拡張子ごとにサブフォルダへ整理するスクリプト。

使い方:
    python organize_downloads.py                  # ~/Downloads を整理
    python organize_downloads.py /path/to/folder  # 指定フォルダを整理
    python organize_downloads.py --dry-run        # 実際には移動せず確認のみ
"""

import argparse
import shutil
from pathlib import Path

# 拡張子 → カテゴリのマッピング
CATEGORY_MAP = {
    # 画像
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".bmp": "Images",
    ".svg": "Images",
    ".webp": "Images",
    ".ico": "Images",
    ".tiff": "Images",
    # 動画
    ".mp4": "Videos",
    ".avi": "Videos",
    ".mov": "Videos",
    ".mkv": "Videos",
    ".wmv": "Videos",
    ".flv": "Videos",
    ".webm": "Videos",
    # 音声
    ".mp3": "Music",
    ".wav": "Music",
    ".flac": "Music",
    ".aac": "Music",
    ".ogg": "Music",
    ".wma": "Music",
    # ドキュメント
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".xls": "Documents",
    ".xlsx": "Documents",
    ".ppt": "Documents",
    ".pptx": "Documents",
    ".txt": "Documents",
    ".csv": "Documents",
    ".rtf": "Documents",
    ".odt": "Documents",
    # 圧縮ファイル
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".bz2": "Archives",
    ".xz": "Archives",
    # プログラム・インストーラ
    ".exe": "Programs",
    ".msi": "Programs",
    ".dmg": "Programs",
    ".deb": "Programs",
    ".rpm": "Programs",
    ".appimage": "Programs",
    # コード
    ".py": "Code",
    ".js": "Code",
    ".ts": "Code",
    ".html": "Code",
    ".css": "Code",
    ".json": "Code",
    ".xml": "Code",
    ".yaml": "Code",
    ".yml": "Code",
    ".sh": "Code",
    ".sql": "Code",
    # フォント
    ".ttf": "Fonts",
    ".otf": "Fonts",
    ".woff": "Fonts",
    ".woff2": "Fonts",
    # ディスクイメージ
    ".iso": "DiskImages",
    ".img": "DiskImages",
    # 電子書籍
    ".epub": "Ebooks",
    ".mobi": "Ebooks",
    ".azw3": "Ebooks",
}


def get_category(file_path: Path) -> str:
    """ファイルの拡張子からカテゴリを返す。"""
    ext = file_path.suffix.lower()
    return CATEGORY_MAP.get(ext, "Others")


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


def organize(target_dir: Path, dry_run: bool = False) -> None:
    """target_dir 内のファイルをカテゴリ別サブフォルダへ移動する。"""
    if not target_dir.is_dir():
        print(f"エラー: '{target_dir}' はディレクトリではありません。")
        return

    files = [f for f in target_dir.iterdir() if f.is_file()]

    if not files:
        print("整理するファイルがありません。")
        return

    moved_count = 0
    for file_path in sorted(files):
        category = get_category(file_path)
        dest_dir = target_dir / category
        dest = resolve_conflict(dest_dir / file_path.name)

        if dry_run:
            print(f"  [dry-run] {file_path.name} → {category}/")
        else:
            dest_dir.mkdir(exist_ok=True)
            shutil.move(str(file_path), str(dest))
            print(f"  {file_path.name} → {category}/")
        moved_count += 1

    label = "移動予定" if dry_run else "移動完了"
    print(f"\n{label}: {moved_count} ファイル")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ダウンロードフォルダを拡張子ごとに整理します。"
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
    args = parser.parse_args()

    target = Path(args.directory).expanduser().resolve()
    print(f"対象フォルダ: {target}")

    if args.dry_run:
        print("(dry-run モード: ファイルは移動されません)\n")
    else:
        print()

    organize(target, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

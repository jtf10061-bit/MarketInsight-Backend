"""ChromaDBのコレクションを作り直し、uploads配下のPDFを全て再インデックスする。

参照元にページ・行・章を持たせる改修に伴い、既存チャンクのメタデータが不足するため
一度作り直す必要がある。

使い方:
    python reindex.py

注意:
    - ChromaDBのコレクション "documents" を削除して作り直す(元に戻せない)
    - チャンク1件につき1回 Azure OpenAI のEmbedding APIを呼ぶ
"""

import glob
import os
import sys

import marketinsight_backend.rag as rag

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")


def main():
    pdfs = sorted(glob.glob(os.path.join(UPLOADS_DIR, "*.pdf")))
    if not pdfs:
        print("uploads配下にPDFがありません")
        return

    print(f"対象 {len(pdfs)} 件:")
    for path in pdfs:
        print(f"  - {os.path.basename(path)}")
    if input("\nコレクションを削除して再作成します。よろしいですか? [y/N] ").strip().lower() != "y":
        print("中止しました")
        return

    # 既存コレクションを削除して作り直す
    try:
        rag.chroma_client.delete_collection("documents")
        print("既存コレクションを削除しました")
    except Exception as e:
        print(f"削除をスキップ: {e}")

    # rag モジュール側のグローバル変数を差し替える
    # (process_pdf はモジュールグローバルの collection を参照しているため)
    rag.collection = rag.chroma_client.get_or_create_collection(name="documents")

    total = 0
    for path in pdfs:
        filename = os.path.basename(path)
        result = rag.process_pdf(path, filename)
        total += result["chunks"]
        print(f"  {filename}: {result['chunks']} チャンク")

    print(f"\n完了: 合計 {total} チャンク")


if __name__ == "__main__":
    sys.exit(main())

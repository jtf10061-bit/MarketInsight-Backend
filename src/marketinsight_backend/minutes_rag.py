from marketinsight_backend.rag import split_text_into_chunks, save_chunks, get_embedding, collection
from marketinsight_backend.minutes import _get_ai_client
from marketinsight_backend.tasks import create_task
import os
import json


def search_minutes_chunks(minutes_ids: list[str], n_results: int = 10) -> list[str]:
    """指定された議事録のチャンクをベクトル検索で取得"""
    # minutes_ids -> rag_filenameに変換
    rag_filenames = [f"minutes__{mid}" for mid in minutes_ids]

    # タスク抽出に関連するキーワードで検索
    query = "タスク アクション TODO 担当 期限 次回 決定事項"
    query_embedding = get_embedding(query)

    # ChromaDBのwhereフィルタで議事録のチャンクだけに絞る
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where={"filename": {"$in": rag_filenames}},
    )
    # テキストだけ取り出して返す
    return results["documents"][0] if results["documents"][0] else []


def register_minutes(minutes_id: str, minutes_text: str, filename: str):
    """議事録テキストをチャンク分割してベクトルDBに登録"""
    # 1. ファイル名を議事録用に区別(既存ドキュメントと混ざらないように)
    #   例: "minutes__{minutes_id}"をファイル名として使う → プレフィックスを付けて、通常のドキュメントと区別する
    rag_filename = f"minutes__{minutes_id}"

    # 2. チャンク分割(既存関数を流用)
    # split_text_into_chunks()は、## で始まる行をセクション名として識別するので、議事録の## 議題 ## 決定事項 がそのまま活用される
    chunks = split_text_into_chunks(minutes_text, rag_filename)

    # 3. ベクトルDB + CosmosDBに保存(既存関数を流用)
    save_chunks(chunks, rag_filename)

    return {"minutes_id": minutes_id, "chunks": len(chunks)}


def extract_tasks_from_minutes(minutes_ids: list[str], user_id: str):
    """選択された議事録からタスクを抽出してDBに保存"""
    # 1. 各議事録のチャンクをベクトル検索で取得
    chunks = search_minutes_chunks(minutes_ids)
    if not chunks:
        return {"tasks_created": 0, "error": "関連するチャンクが見つかりません"}

    # 2. 取得したチャンクをコンテキストにして、AIにタスク抽出を依頼
    context = "\n\n".join(chunks)

    # 3. AIのレスポンス(JSON文字列)をパース
    ai_client = _get_ai_client()
    prompt = f"""以下の議事録から、タスク・TODO・悪所内てむを全て抽出してください。

JSON配列で返してください。各要素は以下の形式です:
[
    {{
        "title": "タスクのタイトル",
        "description": "タスクの詳細説明",
        "priority": "high" または "medium" または "low",
        "due_date": "YYY-MM-DD" または null
    }}
]

JSON配列のみを返してください。説明文は不要です。

## 議事録:
{context}
"""

    response = ai_client.chat.completions.create(
        model=os.getenv("AZURE_OPEM_DEVELOPMEMT"),
        messages=[
            {
                "role": "system",
                "content": "あなたはタスク抽出の専門家です。議事録からアクションアイテムを正確に抽出してください。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    # 4. 各タスクをcreate_task() で保存
    raw = response.choices[0].message.content
    # ```json...```で囲まれている場合に対応
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    task_list = json.loads(raw.strip())

    # 5. 各タスクをcreate_task()で保存
    created = 0
    for t in task_list:
        create_task(
            user_id=user_id,
            title=t["title"],
            description=t.get("description", ""),
            priority=t.get("priority", "medium"),
            due_date=t.get("due_date"),
        )
        created += 1

    return {"task_created": created}

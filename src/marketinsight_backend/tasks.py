from pathlib import Path
from dotenv import load_dotenv
import uuid
from datetime import datetime


def load_env():
    load_dotenv(Path(__file__).resolve().parent.parent.parent / "./env", override=True)


# CosmodDBのtaskコンテナに接続するための関数
def _get_cosmos_container():
    from marketinsight_backend.rag import cosmos_client

    database = cosmos_client.get_database_client("history")
    return database.get_container_client("tasks")


# --- 1. タスク一覧取得 ---
def get_tasks(user_id: str) -> list[dict]:
    container = _get_cosmos_container()
    query = "SELECT * FROM c WHERE c.user_id = @uid ORDER BY c.order_index"
    items = container.query_items(
        query=query,
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    )
    return list(items)


# --- 2. タスク作成 ---
def create_task(
    user_id: str, title: str, description: str = "", priority: str = "medium", due_date: str = ""
) -> dict:
    container = _get_cosmos_container()
    existing = get_tasks(user_id)
    next_index = max((t["order_index"] for t in existing), default=-1) + 1
    task = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title,
        "description": description,
        "status": "todo",
        "priority": priority,
        "due_date": due_date,
        "created_at": datetime.utcnow().isoformat(),
        "order_index": next_index,
    }
    container.upsert_item(task)
    return task


# --- 3. タスク更新 ---
def update_task(task_id: str, updates: dict) -> dict | None:
    container = _get_cosmos_container()
    # 1. 既存のタスクを取得
    items = list(
        # container.query_items(): CosmosDBに対して、SQLクエリでデータを検索するメソッド
        container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": task_id}],
            enable_cross_partition_query=True,
        )
    )
    if not items:
        return None
    # 2. 取得したタスクにupdateの内容を上書き
    task = items[0]
    task.update(updates)
    # 3. 保存
    container.upsert_item(task)
    return task
    # pass


# --- 4. タスク削除 ---
def delete_task(task_id: str, user_id: str):
    container = _get_cosmos_container()
    items = list(
        container.query_items(
            query="SELECT c.id, c.user_id FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": task_id}],
            enable_cross_partition_query=True,
        )
    )
    if items:
        container.delete_item(items[0]["id"], partition_key=items[0]["user_id"])
    # pass

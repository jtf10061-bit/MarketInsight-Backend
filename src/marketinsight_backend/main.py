from fastapi import FastAPI     # アプリ本体。app = FastAPI() でサーバーを作る。
from fastapi.middleware.cors import CORSMiddleware      # CORS（Cross-Origin Resource Sharing）を許可するミドルウェア(ブラウザはセキュリティのため、異なるオリジン間の通信をデフォルトでブロックする)
# CORS設定：別ドメイン（フロント側など）からのAPI呼び出しを許可する
# （これがないとブラウザのセキュリティ機能で通信がブロックされてしまうため）
from fastapi.responses import StreamingResponse     # レスポンスを一括ではなくストリーミングで返す

from pydantic import BaseModel
"""
リクエストのバリデーション（型チェック）用。ChatRequest で message: str と定義すると、FastAPIが自動的に:
    JSONの中に message があるか確認
    型が str か確認
    不正なら自動で422エラーを返す
"""
import json
import time
from marketinsight_agent.react_loop import run # MCP-Agentのrunをimport
from marketinsight_history.chat_service import(
    get_chats,
    create_chat,
    delete_chat,
    toggle_favorite,
    update_title,
    add_message,
)

"""
FastAPIは
POST /chat にリクエストが来る
引数の型が ChatRequest（BaseModelを継承）だと見る
→ 「ボディはJSONだな」と自動判断する
→ JSONを ChatRequest に自動変換する
つまり「BaseModelを型ヒントに使う = JSONで受け取る」というFastAPIの規約（お約束）
↓
引数が str, int など → URLパラメータと判断
引数が BaseModel → JSONボディと判断
という、フレームワークの設計であり、BaseModel自体はJSON専用ではなく、FastAPIがそう解釈しているだけ
"""
app = FastAPI()
# ミドルウェアの作成
app.add_middleware(
    CORSMiddleware,
    allow_origins = ["http://localhost:5173"],      # どこからのアクセスを許可するか
    allow_methods=["*"],    # 許可するHTTPメソッドの指定です。"*" は全メソッド許可。    → /chat はPOSTで Content-Type: application/json ヘッダーを使っているので、これがないとブロックされ、エラー(エラー: サーバーに接続できません)が出ていた
    allow_headers=["*"],    # 許可するHTTPヘッダーの指定です。"*" は全ヘッダー許可。    → /chat はPOSTで Content-Type: application/json ヘッダーを使っているので、これがないとブロックされ、エラー(エラー: サーバーに接続できません)が出ていた
)
# .add_middleware(): FastAPIなどのWebフレームワークにおいて、アプリケーション全体のリクエストやレスポンスに横断的な処理（ログ記録、セキュリティ対策、データ圧縮など）を挿入（追加）するためのメソッド
# → 第1引数にミドルウェアのクラスを取り、第2引数以降にはミドルウェアに渡したいキーワード引数を取る
# classを使ってmessageの型定義をしている

# history = []

class ChatRequest(BaseModel):
    message: str

class CreateChatRequest(BaseModel):
    id: str
    title: str
    date: str
    user_id: str

class UpdateTitleRequest(BaseModel):
    title: str

class AddMessageRequest(BaseModel):
    role: str
    content: str


"""
@app.〇〇(): デコレータ。「このURLにこのHTTPメソッドでリクエストが来たら、この関数を実行する」というルーティング定義
例：
@app.get("/chats/{user_id}")    # GET /chats/test-user にアクセスしたら
def api_get_chats(user_id: str): # この関数を実行する
    return get_chats(user_id)

HTTPメソッドごとの使い分け：
デコレータ	    HTTPメソッド    用途
@app.get()	  GET	         データの取得
@app.post()	  POST	         データの作成
@app.patch()  PATCH	         データの一部更新
@app.delete() DELETE	     データの削除
"""

@app.get("/health")
# health(): サーバーが正常に動いているか確認するためのヘルスチェックエンドポイント
def health():
    return {"status": "ok"}

@app.get("/chats/{user_id}")
def api_get_chats(user_id: str):
    return get_chats(user_id)

@app.post("/chats")
def api_create_chat(req: CreateChatRequest):
    create_chat(req.user_id, req.id, req.title, req.date)
    return {"status": "ok"}

@app.delete("/chats/{user_id}/{chat_id}")
def api_delete_chat(user_id: str, chat_id: str):
    delete_chat(user_id, chat_id)
    return {"status": "ok"}

@app.patch("/chats/{user_id}/{chat_id}/favorite")
def api_toggle_favorite(user_id: str, chat_id: str):
    toggle_favorite(user_id, chat_id)
    return {"status": "ok"}

@app.patch("/chats/{user_id}/{chat_id}/title")
def api_toggle_title(user_id: str, chat_id: str, req: UpdateTitleRequest):
    update_title(user_id, chat_id, req.title)
    return {"status": "ok"}

@app.post("/chats/{user_id}/{chat_id}/messages")
def api_add_message(user_id: str, chat_id: str, req: AddMessageRequest):
    add_message(user_id, chat_id, req.role, req.content)
    return {"status": "ok"}

@app.post("/chat")
def chat(request: ChatRequest):
    def generate():
        for step in run(request.message, []):
            yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
            time.sleep(1)
    return StreamingResponse(generate(), media_type="text/event-stream")
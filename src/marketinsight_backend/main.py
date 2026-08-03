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

class ChatRequest(BaseModel):
    message: str

history = []

@app.get("/health")
# health(): サーバーが正常に動いているか確認するためのヘルスチェックエンドポイント
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(request: ChatRequest):
    history.append({"role": "user", "content": request.message})

    def generate():
        answer = ""
        for step in run(request.message, history):
            yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
            time.sleep(1)
            if step["type"] == "answer":
                answer = step["content"]
        history.append({"role": "assistant", "content": answer})
    # steps = run(request.message)
    # return {"steps": steps}
    return StreamingResponse(generate(), media_type="text/event-stream")
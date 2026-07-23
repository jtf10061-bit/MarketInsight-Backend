from fastapi import FastAPI     # アプリ本体。app = FastAPI() でサーバーを作る。
from fastapi.middleware.cors import CORSMiddleware      # CORS（Cross-Origin Resource Sharing）を許可するミドルウェア(ブラウザはセキュリティのため、異なるオリジン間の通信をデフォルトでブロックする)
# CORS設定：別ドメイン（フロント側など）からのAPI呼び出しを許可する
# （これがないとブラウザのセキュリティ機能で通信がブロックされてしまうため）
from pydantic import BaseModel
"""
リクエストのバリデーション（型チェック）用。ChatRequest で message: str と定義すると、FastAPIが自動的に:
    JSONの中に message があるか確認
    型が str か確認
    不正なら自動で422エラーを返す
"""


app = FastAPI()

# ミドルウェアの作成
app.add_middleware(
    CORSMiddleware,
    allow_origins = []
)
# .add_middleware(): FastAPIなどのWebフレームワークにおいて、アプリケーション全体のリクエストやレスポンスに横断的な処理（ログ記録、セキュリティ対策、データ圧縮など）を挿入（追加）するためのメソッド
# → 第1引数にミドルウェアのクラスを取り、第2引数以降にはミドルウェアに渡したいキーワード引数を取る

# classを使ってmessageの型定義をしている
class ChatRequest(BaseModel):
    message: str

# health(): サーバーが正常に動いているか確認するためのヘルスチェックエンドポイント
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(request: ChatRequest):
    return{"reply": f"「{request.message}」を審査中です"}

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

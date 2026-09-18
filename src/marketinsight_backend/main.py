from fastapi import FastAPI, UploadFile, File  # アプリ本体。app = FastAPI() でサーバーを作る。
import os
from fastapi.middleware.cors import (
    CORSMiddleware,
)  # CORS（Cross-Origin Resource Sharing）を許可するミドルウェア(ブラウザはセキュリティのため、異なるオリジン間の通信をデフォルトでブロックする)

# CORS設定：別ドメイン（フロント側など）からのAPI呼び出しを許可する
# （これがないとブラウザのセキュリティ機能で通信がブロックされてしまうため）
from fastapi.responses import StreamingResponse  # レスポンスを一括ではなくストリーミングで返す
from pydantic import BaseModel
from marketinsight_backend.rag import process_pdf

"""
リクエストのバリデーション（型チェック）用。ChatRequest で message: str と定義すると、FastAPIが自動的に:
    JSONの中に message があるか確認
    型が str か確認
    不正なら自動で422エラーを返す
"""
import json
import time
from marketinsight_agent.react_loop import run  # MCP-Agentのrunをimport
from marketinsight_history.chat_service import (
    get_chats,
    create_chat,
    delete_chat,
    toggle_favorite,
    update_title,
    add_message,
)
from marketinsight_agent.config import MODELS
from marketinsight_backend.rag import search_documents

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
    allow_origins=["http://localhost:5173"],  # どこからのアクセスを許可するか
    allow_methods=[
        "*"
    ],  # 許可するHTTPメソッドの指定です。"*" は全メソッド許可。    → /chat はPOSTで Content-Type: application/json ヘッダーを使っているので、これがないとブロックされ、エラー(エラー: サーバーに接続できません)が出ていた
    allow_headers=[
        "*"
    ],  # 許可するHTTPヘッダーの指定です。"*" は全ヘッダー許可。    → /chat はPOSTで Content-Type: application/json ヘッダーを使っているので、これがないとブロックされ、エラー(エラー: サーバーに接続できません)が出ていた
)
# .add_middleware(): FastAPIなどのWebフレームワークにおいて、アプリケーション全体のリクエストやレスポンスに横断的な処理（ログ記録、セキュリティ対策、データ圧縮など）を挿入（追加）するためのメソッド
# → 第1引数にミドルウェアのクラスを取り、第2引数以降にはミドルウェアに渡したいキーワード引数を取る
# classを使ってmessageの型定義をしている

# history = []


class ChatRequest(BaseModel):
    message: str
    chat_id: str = ""
    model: str = "aoai-gpt-4.1-mini"
    user_id: str = ""


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


class RagQueryRequests(BaseModel):
    query: str
    model: str = "aoai-gpt-4.1-mini"
    user_id: str = ""
    mode: str = "search"


# UPLOAD_DIR: アップロード先のフォルダ(MarketInsight-Backend/uploads/)
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
# os.makedirs(..., exist_ok=True): フォルダがなければ作る、あればそのまま
os.makedirs(UPLOAD_DIR, exist_ok=True)


"""
@app.〇〇(): FastAPIのルーティングデコレータ。「このURLにこのHTTPメソッドでリクエストが来たら、この関数を実行する」というルーティング定義
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
    # history = [{"role": "user", "content": request.message}]
    history = []
    if request.chat_id:
        try:
            from marketinsight_history.chat_service import get_messages

            history = get_messages(request.user_id, request.chat_id)
        except Exception:
            pass
    if not history or history[-1].get("content") != request.message:
        history.append({"role": "user", "content": request.message})

    def generate():
        try:
            for step in run(request.message, history, model=request.model):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
                time.sleep(1)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': f'エラーが発生しました: {str(e)}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/models")
def get_models():
    return MODELS


@app.post("/upload")
# UploadFile: FastAPIでファイルアップロードを受け取る型
# File(...): このパラメータは必須ファイルという指定
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        return {"error": "PDFファイルのみアップロード可能です"}

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as f:
        # await file.read(): アップロードされたファイルの中身を読み取る
        content = await file.read()
        f.write(content)

    process_pdf(file_path, file.filename)

    return {"filename": file.filename, "status": "uploaded"}


# /uploads: アップロード済みのファイル一覧を返すAPI
@app.get("/uploads")
def list_uploads():
    files = os.listdir(UPLOAD_DIR)
    pdf_files = [f for f in files if f.endswith(".pdf")]
    return pdf_files


@app.get("/rag/files")
def rag_files():
    from marketinsight_backend.rag import get_cosmos_files

    return get_cosmos_files()


# HTTPのDELETEメソッドを受け取るエンドポイント
# {filename}: URLパスからファイル名を受け取る(パスパラメータ)
@app.delete("/rag/files/{filename}")
def delete_rag_file(filename: str):
    from marketinsight_backend.rag import delete_document

    delete_document(filename)
    return {"status": "deleted"}


@app.get("/rag/history/{user_id}")
def api_get_rag_history(user_id: str):
    from marketinsight_backend.rag import get_rag_history

    return get_rag_history(user_id)


@app.delete("/rag/history/{user_id}/{history_id:path}")
def api_delete_rag_history(user_id: str, history_id: str):
    from marketinsight_backend.rag import delete_rag_history

    delete_rag_history(user_id, history_id)
    return {"status": "deleted"}


@app.post("/rag/search")
def rag_search(req: RagQueryRequests):
    # req: RagQueryRequests: リクエストボディのJSONをPydanticモデルで自動検証・型定義して受け取る

    # 1. ChromaDBからベクトル検索
    # search_documents(): ユーザーの質問文字列(req.query)を元にベクトル検索を実行し、関連チャンクを取得
    # modeに応じて検索パラメータを変える
    if req.mode == "reasoning":
        results = search_documents(req.query, n_results=6)
        results = [r for r in results if r["distance"] < 1.5]
    else:
        results = search_documents(req.query)

    # 2. エビデンス情報をSSEで先に送る
    # SSE: サーバーからWebブラウザ（クライアント）へ、リアルタイムにデータを連続送信（ストリーミング）するためのWEB標準技術
    # リスト内包表記: 検索結果(results)からUI表示に必要なメタデータだけを抽出し、整形
    evidence = [
        {
            "filename": r["filename"],
            "section": r["section"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "page": r.get("page"),
            "page_end": r.get("page_end"),
            "line_start": r.get("line_start"),
            "line_end": r.get("line_end"),
            "similarity": round((1 - r["distance"]) * 100, 1),  # 距離 → 類似度%に変換
        }
        for r in results
    ]

    # 3. 信頼度算出
    from marketinsight_backend.rag import calculate_confidence

    confidence = calculate_confidence(results)

    # 4. 検索結果をコンテキスト(AIに回答を生成させるときに与える「背景情報」や「参考資料となるテキスト」)としてまとめる
    context = "\n\n".join([f"【{r['filename']}】\n{r['content']}" for r in results])

    # 5. AIに質問 + コンテキストとしてまとめる

    if req.mode == "reasoning":
        prompt = f"""以下のドキュメントの情報を元に、質問に対して推論してください。

    【ルール】
    - ドキュメントに直接の記載がなくても、記載された情報から論理的に導ける結論を述べてください
    - 推論の各ステップで、根拠となるドキュメントの記述を「【根拠】○○（ファイル名）に『△△』と記載」の形式で引用してください
    - ドキュメントの情報から推論できない部分は「この部分は推論の範囲外です」と明記してください
    - Web検索や一般知識は使わないでください
    - 結論は**太字**で記載してください

    ## 参考ドキュメント
    {context}

    ## 質問
    {req.query}
    """
    else:
        prompt = f"""以下のドキュメントを参考に、質問に回答してください
        ドキュメントに記載がない内容については「この質問に関する情報はドキュメントに含まれていません」と回答してください。
        ドキュメントの内容に基づかない推測や一般知識での回答はしないでください。


## 参考ドキュメント
{context}

## 質問
{req.query}
"""
    # 回答を蓄積するリスト
    full_answer = []

    # 6. 既存のrun関数を使って回答をストリーミング生成するジェネレータ関数
    # ストリーミング生成：AIが回答を出力する際、全体の生成が完了するのを待つのではなく、生成されたテキストやデータから順次（リアルタイムに）クライアントへ送り返す仕組み
    def generate():
        # 最初にエビデンス情報 + 信頼度を送る
        yield f"data: {json.dumps({'type': 'evidence', 'content': evidence, 'confidence': confidence, 'mode': req.mode}, ensure_ascii=False)}\n\n"

        # 信頼度が低い場合はAIに聞かず、即終了
        # 推論は間接的な情報から答えを導くので、類似度が低くても有用なチャンクがある。ただし20未満は本当に無関係なので止める
        min_confidence = 20 if req.mode == "reasoning" else 40
        if confidence["score"] < min_confidence:
            yield f"data: {json.dumps({'type': 'answer', 'content': 'この質問に関する情報はドキュメントには含まれていません。'}, ensure_ascii=False)}\n\n"
            return

        for step in run(prompt, [{"role": "user", "content": prompt}], model=req.model):
            # run(): 既存のReActループ関数。promptとhistoryを渡してAIに回答させる
            # [{"role": "user", "content": prompt}]: 履歴としてpromptを渡す(RAGではコンテキストをpromptに含めている)
            # model: 使用するAIモデル
            if step.get("type") == "answer":
                full_answer.append(step["content"])
            yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
            # json.dumps(): Python辞書をJSON文字列に変換
            # ensure_ascii=False: 日本語をそのまま出力(\uxxxにしない) → データ量の削減、ログやデバッグの視認性向上、SSEでの受け渡し(日本語の文字化けの心配なく安全に受け取れる)
            # f"data: ...\n\n": SSE(Server-Sent Events)形式。ブラウザが1行ずつ受信できる
            # yield: 値を1つ返して処理を一時停止、次のforループで再開する(ジェネレータ)

        # ストリーミング完了後に履歴保存
        if req.user_id and full_answer:
            # full_answer: リストに回答チャンクを蓄積
            from marketinsight_backend.rag import save_rag_history

            # ストリーミング完了後に、save_rag_historyを呼ぶ
            save_rag_history(
                req.user_id, req.query, "".join(full_answer), evidence, confidence, req.mode
            )

    # StreamingResponse(): FastAPIのレスポンスクラス。一括ではなく逐次的にデータを返す
    # generate(): 上で定義したジェネレータ(クロージャ)を渡す。外側のfull_answerにアクセスできる
    # media_type: "text/event-stream": SSE形式であることをブラウザに伝えるContent-Type
    return StreamingResponse(generate(), media_type="text/event-stream")

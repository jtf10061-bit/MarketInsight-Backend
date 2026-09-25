from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    BackgroundTasks,
)  # アプリ本体。app = FastAPI() でサーバーを作る。
import os
from fastapi.middleware.cors import (
    CORSMiddleware,
)  # CORS（Cross-Origin Resource Sharing）を許可するミドルウェア(ブラウザはセキュリティのため、異なるオリジン間の通信をデフォルトでブロックする)

# CORS設定：別ドメイン（フロント側など）からのAPI呼び出しを許可する
# （これがないとブラウザのセキュリティ機能で通信がブロックされてしまうため）
from fastapi.responses import StreamingResponse  # レスポンスを一括ではなくストリーミングで返す
from pydantic import BaseModel
from marketinsight_backend.minutes_rag import register_minutes, extract_tasks_from_minutes

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
from marketinsight_backend.rag import (
    search_documents,
    summarize_document,
    get_summary_history,
    delete_summary,
    get_summary_detail,
)

from marketinsight_backend.minutes import (
    get_minutes_history,
    get_minutesdetail,
    delete_minutes,
    jobs,
    process_minutes_job,
)
import uuid

from marketinsight_backend.tasks import (
    get_tasks,
    create_task,
    update_task,
    delete_task,
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


# Classの役割: FastAPIでは、リクエストのJSONボディを受け取るにはクラス(BaseModel)が必要。JSONを受け取るためにどんなフィールドがあり、型は何かを定義する
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


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    due_date: str | None = None
    user_id: str = "test-user"


class UpdateTaskRequest(BaseModel):
    status: str = ""
    title: str = ""
    description: str = ""
    priority: str = ""
    due_date: str = ""
    order_index: int = -1


class ExtractTasksRequest(BaseModel):
    minutes_ids: list[str]
    user_id: str = "test-user"
    query: str = ""


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
async def upload_file(file: UploadFile = File(...)):
    # 対応拡張子のチェック
    allowed_ext = (".pdf", ".docx", ".txt", ".xlsx", ".pptx")

    # file.filename.lower(): 大文字小文字の表記揺れを避けるために、小文字に統一
    # endswith(): ファイル名が許可された拡張子のいずれかで終わっているかをチェック
    if not file.filename.lower().endswith(allowed_ext):
        # 許可されていない形式の場合は、エラーメッセージのJSONを返して処理を中断
        return {"error": "対応形式: PDF, DOCX, TXT, XLSX, PPTX"}

    # os.path.join(): 保存先ディレクトリとファイル名を安全に結合してフルパスを作成する
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    # open(..., "wb"): ファイルをバイナリ書き込みモード(wb)でオープン
    with open(file_path, "wb") as f:
        # await file.read(): アップロードされたファイルの内容を非同期で読み込み(メモリ上に取得)
        content = await file.read()
        # ローカルディスクの指定パスにファイルを書き込んで保存
        f.write(content)
    # モジュール遅延インポート: RAG処理よう関数を必要なタイミングで読み込み
    from marketinsight_backend.rag import process_file

    # process_file(): 保存したファイルを読み込み、テキスト抽出・チャンク分割・ベクトルDB登録などの前処理を実行
    process_file(file_path, file.filename)
    # アップロード完了メッセージとファイル名をレスポンスJSONとして返却
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


# --- 議事録: 音声アップロード + 文字起こし + 議事録生成 ---
@app.post("/minutes/upload")
# async: この関数は途中で待てると言う宣言。
async def upload_audio(
    file: UploadFile = File(...),
    user_id: str = Form("test-user"),
    background_tasks: BackgroundTasks = None,
):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {".mp4", ".m4a", ".wav", ".webm", ".mp3"}:
        raise HTTPException(status_code=400, detail=f"未対応の音声形式です: {ext}")

    file_path = f"uploads/{file.filename}"
    with open(file_path, "wb") as f:
        f.write(await file.read())

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "processing"}
    background_tasks.add_task(process_minutes_job, job_id, file_path, file.filename, user_id)

    return {"job_id": job_id, "status": "processing"}


@app.get("/minutes/status/{job_id}")
def minutes_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job Not Found")
    return {"job_id": job_id, "status": job["status"], "error": job.get("error", "")}


@app.get("/minutes/result/{job_id}")
def minutes_result(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job Not Found")
    if job["status"] != "completed":
        return {"job_id": job_id, "status": job["status"]}
    return {"status": "ok", **job["result"]}


@app.get("/minutes/history/{user_id}")
def minutes_history(user_id: str):
    result = get_minutes_history(user_id)
    return result


@app.get("/minutes/{minutes_id}")
def minutes_detail(minutes_id: str):
    result = get_minutesdetail(minutes_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Minutes not Found")
    return result


@app.delete("/minutes/{minutes_id}")
def minutes_delete(minutes_id: str):
    delete_minutes(minutes_id)
    return {"status": "ok"}


@app.post("/rag/summarize")
def rag_summarize(body: dict):
    result = summarize_document(body["filename"], body["user_id"])
    return result


@app.get("/rag/summary/history/{user_id}")
def rag_summary_history(user_id: str):
    return get_summary_history(user_id)


@app.get("/rag/summary/{summary_id}")
def rag_summary_detail(summary_id: str):
    result = get_summary_detail(summary_id)
    if result is None:
        return {"error": "Not Found"}
    return result


@app.delete("/rag/summary/{summary_id}")
def rag_summary_delete(summary_id: str, user_id: str = "test-user"):
    delete_summary(summary_id, user_id)
    return {"status": "deleted"}


# --- タスクを管理 ---
# タスク一覧を取得: 指定したユーザーのタスク一覧を返すエンドポイント
@app.get("/tasks/{user_id}")
# 非同期関数として定義しており、URLから受け取ったuser_id(文字列)を引数に取る
def api_get_tasks(user_id: str):
    # DB操作などを行う内部関数get_tasksと呼び出し、取得したタスクのデータをそのままレスポンス(JSON形式)として返す
    return get_tasks(user_id)


# タスクの新規作成
@app.post("/tasks")
# リクエストボディ(JOSNデータ)をPydanticモデルであるCreateChatRequest型のデータとsちえ受け取る(自動でバリデーションが行われる)
def api_create_task(req: CreateTaskRequest):
    # リクエストから取り出した各種パラメータを内部関数create_taskに渡し、タスクを作成する
    task = create_task(req.user_id, req.title, req.description, req.priority, req.due_date)
    return task


# タスクの更新
@app.patch("/tasks/{task_id}")
# 既存タスクの一部フィールドを部分更新する
def api_update_task(task_id: str, req: UpdateTaskRequest):
    # リクエストオブジェクトreqを辞書型に変換(req.dict())する
    # ない方表記を用いて、値が空文字や未指定を表すデフォルト値(-1)ではない項目だけ抽出し、更新用辞書updatesを作成する
    updates = {k: v for k, v in req.dict().items() if v != "" and v != -1}
    # reault = update_task(taskid, updates): 更新対象のタスクIDと抽出した更新データ(updates)を渡して、内部関数update_taskを実行する
    result = update_task(task_id, updates)
    # if result is None: 対象のタスクが存在しなかった場合
    if result is None:
        # raise HTTPException(status_code=404, detail="Task not found"): HTTPステータスコード404 Not Foundの例外を発生させ、クライアントにエラーを通史する
        raise HTTPException(status_code=404, detail="Task not found")
    # 更新完了後のタスクデータを返す
    return result


# タスクの削除
@app.delete("/tasks/{task_id}")
# DELETEリクエストを処理する
# user_id: str = "test-user": クエリパラメータからuser_idを受け取る。
# パラメータが指定されていない場合はデフォルト値として、"test-userが設定される
def api_delete_task(task_id: str, user_id: str = "test-user"):
    # 内部関数delete_taskを呼び出し、指定したタスクIDとユーザーIDを渡して、削除処理を実行し、結果を返す
    return delete_task(task_id, user_id)


# --- 議事録タスク抽出RAG ---
# 議事録をベクトルDBに登録
@app.post("/minutes-rag/register/{minutes_id}")
def api_register_minutes(minutes_id: str):
    from marketinsight_backend.minutes import get_minutesdetail

    detail = get_minutesdetail(minutes_id)
    if not detail:
        raise HTTPException(status_code=404, detail="議事録が見つかりません")
    result = register_minutes(minutes_id, detail["minutes"], detail["filename"])
    return result


# 議事録からタスクを抽出
@app.post("/minutes-rag/extract-tasks")
def api_extract_tasks(req: ExtractTasksRequest):
    result = extract_tasks_from_minutes(req.minutes_ids, req.user_id, req.query)
    return result

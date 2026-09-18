# os: ファイルパスの結合やディレクトリ操作
import os

# fitz: PyMuPDFのライブラリ。PDFを開いてテキストを抽出する
import fitz

# chromadb: ベクトルDBクライアント。E beddingを保存、検索する
import chromadb
from openai import AzureOpenAI
from dotenv import load_dotenv

# Path: ファイルパスをオブジェクトとして扱うクラス。/演算子で、パスを結合できる
from pathlib import Path
from azure.cosmos import CosmosClient
from datetime import datetime

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# CosmosDBクライアント(メタ情報とテキスト保存用)
cosmos_client = CosmosClient(
    os.getenv("COSMOS_ENDPOINT"),
    os.getenv("COSMOS_KEY"),
)
database = cosmos_client.get_database_client("history")
# コンテナ取得
rag_container = database.get_container_client("rag-documents")
rag_history_container = database.get_container_client("rag-history")

# ChromaDBクライアント(ローカルに保存)
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "chroma_db")
"""
os.path.dirname(__file__): rag.pyがあるディレクトリ
os.path.join(..., "..", "..", "chroma_db") : 2階層上に移動して、chroma_dbフォルダを指す
    結果： MarketInsight-Backend/chroma_db/
    ベクトルデータの保存先ディレクトリを定義する
"""
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
"""
PersistentClient(): データをディスクに永続保存するクライアント
path = CHROMA_DIR: 保存先のディレクトリを指定する
    サーバーを再起動してもデータが消えない(対義語: Client()はメモリのみで再起動すると消える)
"""
collection = chroma_client.get_or_create_collection(name="documents")
"""
get_or_create_collection(): 名前が"documents"のコレクションがあれば取得、なければ新規作成
コレクション = RDBでいうテーブルのようなもの。Embeddingとテキストをセットで格納する単位
name="documents": このコレクションの名前。PDFから作ったチャンクは全てここに入る
"""

# Embedding生成用のAzure AzureOpenAIクライアント
embedding_client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version="2024-06-01",
)
"""
OpenAI(...): OpenAI互換のAPIクライアントを作成する。
"""
EMBEDDING_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")
# "text-embedding-ada-002 → 環境変数があればそれを使い("AZURE_OPENAI_EMBEDDING_MODEL")、なければ"text-embedding-ada-002"をデフォルト値とする


def extract_text_from_pdf(file_path: str) -> str:
    """PDFからテキストを抽出する"""
    # doc: fitz.open(file_path)で開いたPDFオブジェクト
    # PDFのページを1ページずつ順番に取り出すループ
    # docはイテラブル(for分で回せる=要素を1つずつ順番に取り出すことができるオブジェクト)で、各pageは1ページ分のオブジェクト
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
        # get_text(): そのページ内のテキストを文字列として抽出するメソッド
        # text += 抽出したテキストをtext変数に連結していく
    doc.close()
    return text


def split_info_chunks(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    # chunk_size=500: 1チャンクの文字列(デフォルトで500文字)
    # overlap: チャンク間の重複文字列(デフォルトで100文字)
    # overlapがある理由：100文字ずつ重複することで、文の境目で意味が途切れるのを防ぐ
    """
    長いテキストをチャンク(小さな塊に)に分割する
    ↓
    Embeddingモデルには、入力長の制限があり、かつ検索精度を上げるために分割が必要
    """
    chunks = []  # 結果を入れる配列
    start = 0  # 切り出し開始位置
    # text: 分割したいものテキスト
    while start < len(text):  # テキストの最後まで繰り返す
        end = start + chunk_size  # 終了位置 = 開始位置 + 500
        chunk = text[start:end]  # start~endの範囲を切り出す
        if chunk.strip():  # chunkが空白でなければ
            chunks.append(chunk)  # リストに追加
        start = end - overlap  # 次の開始位置 = 終了位置 - 100(100も自分もどる)
    return chunks


def get_embedding(text: str) -> list[float]:
    """テキストのEmbedding(ベクトル)を生成する"""
    # embedding_client.embeddings.create(): Azure OpenAIのEmbedding APIを呼び出すメソッド
    response = embedding_client.embeddings.create(  # response: APIのレスポンスオブジェクトが返る
        model=EMBEDDING_MODEL,
        input=text,  # ベクトル化したいテキスト
    )
    return response.data[0].embedding
    # response.data: レスポンス内の結果リスト
    # [0]: 今回は1つしか送ってないので、最初の要素を取得
    # .embedding: 実際のベクトル([0.023, -0.041, 0.078, ...]のようなfloatのリスト)


# CosmosDBに保存する
def save_to_cosmos(filename: str, chunks: list[str]):
    # datetime.now(): 現在の日時を取得
    # .isformat(): "2026-09-18T14:30:00.123456" のような国際標準形式の文字列に変換
    uploaded_at = datetime.now().isoformat()
    # チャンクを1つずつ、CosmosDBに保存する
    for i, chunk in enumerate(chunks):
        # upsert_item(): isert + updateの合体メソッド
        # 同じidがなければ新規、あれば上書き更新する
        # → 同じPDFがアップロードされても重複しない
        rag_container.upsert_item(
            {
                "id": f"{filename}_{i}",  # 一意のID
                "filename": filename,  # どのファイルのチャンクか
                "chunk_index": i,  # 何番目のチャンクか
                "content": chunk,  # チャンクのテキスト本文
                "uploaded_at": uploaded_at,  # アップロード時
            }
        )


def process_pdf(file_path: str, filename: str):
    """PDFをテキスト化 → チャンク分割 → Embeddig生成 → ChromaDBに保存"""
    # Step2: テキストを抽出
    text = extract_text_from_pdf(file_path)

    # Step3: チャンク分割
    chunks = split_info_chunks(text)

    # Step4-5: Embeddinf生成→ChromaDB保存
    # CosmosDBに渡す4つのリストを用意する
    ids = []
    embeddings = []
    documents = []
    metadatas = []

    # チャンクを1つずつ処理する
    for i, chunk in enumerate(chunks):
        # 一意のIDを作る 例："AI市場レポート.pdf_0", "AI市場レポート.pdf_1", ...
        chunk_id = f"{filename}_{i}"
        # チャンクをベクトルに変換する
        embedding = get_embedding(chunk)
        # それぞれのリストに追加する
        ids.append(chunk_id)
        embeddings.append(embedding)
        documents.append(chunk)
        metadatas.append({"filename": filename, "chunk_index": i})

    # ChrmaDBに保存する
    collection.add(
        ids=ids,  # 各チャンクの一意の識別し
        embeddings=embeddings,  # ベクトル検索に使う数値配列
        documents=documents,  # 元のテキスト(検索結果として返す用)
        metadatas=metadatas,  # 付加情報(どのファイルの何番目か)
    )
    # CosmosDBにもチャンクを保存する
    save_to_cosmos(filename, chunks)

    # 処理したファイル名とチャンク数を返す  例: {"filename": "AI市場レポート.pdf", "chunks": 12}
    return {"filename": filename, "chunks": len(chunks)}


def search_documents(query: str, n_results: int = 3) -> list[dict]:
    # query: ユーザーの質問文
    # n_results=3: 返す件数(デフォルト3件)
    """質問に近いチャンクをtorageTypeで取得する"""
    query_embedding = get_embedding(query)
    # 質問分をベクトルに変換する    例: "AIの市場規模は？" → [0.02, -0.04, ...]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )
    # collection.query(): ChromaDBのベクトル検索メソッド
    # query_embeddings: 毛なはどう国使うベクトル(リストで渡すので[]で囲む)
    # n_rewults: 上位何件を返すか

    search_results = []
    for i in range(len(results["documents"][0])):
        # results["documents"][0]: ヒットしたチャンクのテキスト一覧
        # [0]: クエリを1つしか送っていないので、最初の結果をセット
        # len(...): ヒット件数分ループ
        search_results.append(
            {
                "content": results["documents"][0][i],
                "filename": results["metadatas"][0][i]["filename"],
            }
        )

    return search_results


def get_cosmos_files() -> list[dict]:
    """アップロード済みファイルの一覧を取得する"""
    query = "SELECT DISTINCT c.filename, c.uploaded_at FROM c"
    items = list(rag_container.query_items(query, enable_cross_partition_query=True))
    return items


def delete_document(filename: str):
    # CosmosDBからそのファイルのチャンクを全て削除する
    # query: CosmosDVからそのファイル名のアイテムのIDだけを取得するクエリ
    query = "SELECT c.id FROM c WHERE c.filename = @name"
    # parameters: SQLインジェクション防止のためのパラメータ化のクエリを扱う(@nameにfilenameを渡す)
    # SQLインジェクション: ユーザーの入力フォームやリクエストパラメータに悪意のあるSQL文（データベース操作命令）を注入（インジェクション）され、データベースを不正操作される重大な脆弱性（またはその攻撃手法）のこと
    parameters = [{"name": "@name", "value": filename}]
    items = list(
        rag_container.query_items(query, parameters=parameters, enable_cross_partition_query=True)
    )
    for item in items:
        # delete_item(id, partition_key=filename): パーティションキーが/filenameなので、partition_keyにファイル名を渡す
        rag_container.delete_item(item["id"], partition_key=filename)

    # ChromaDBからそのファイルのチャンクを全て削除
    # collection.delete(where=...): ChromaDBのメタデータ条件指定削除
    collection.delete(where={"filename": filename})


# ドキュメント検索RAG関連
def save_rag_history(user_id: str, query: str, answer: str, evidence: list, confidence: dict):
    """ドキュメント検索RAGの検索履歴を保存"""
    rag_history_container.upsert_item(
        {
            "id": f"{user_id}_{datetime.now().timestamp()}",
            "user_id": user_id,
            "query": query,
            "answer": answer,
            "evidence": evidence,
            "confidence": confidence,
            "created_at": datetime.now().isoformat(),
        }
    )


def get_rag_history(user_id: str) -> list[dict]:
    """ドキュメントRAG検索履歴を取得(新しい順)"""
    query = "SELECT c.id, c.query, c.answer, c.evidence, c.confidence, c.created_at FROM c WHERE c.user_id = @uid ORDER BY c.created_at DESC"
    parameters = [{"name": "@uid", "value": user_id}]
    items = list(
        rag_history_container.query_items(
            query, parameters=parameters, enable_cross_partition_query=True
        )
    )
    return items


def delete_rag_history(user_id: str, history_id: str):
    """ドキュメントRAG検索履歴を1件、削除"""
    rag_history_container.delete_item(history_id, partition_key=user_id)


def search_documents(query: str, n_results: int = 3) -> list[dict]:
    """エビデンス表示"""
    query_embedding = get_embedding(query)
    results = collection.query(query_embeddings=[query_embedding], n_results=n_results)

    search_result = []
    for i in range(len(results["documents"][0])):
        content = results["documents"][0][i]
        # チャンクの先頭行から章タイトルを抽出
        first_line = content.strip().split("\n")[0]

        search_result.append(
            {
                "content": content,
                "filename": results["metadatas"][0][i]["filename"],
                "chunk_index": results["metadatas"][0][i]["chunk_index"],
                "distance": results["distances"][0][i],  # ベクトル距離(築地どの元データ)
                "section": first_line[:50],  # チャンク先頭50文字を章の手がかりとして返す
            }
        )
    return search_result


"""
信頼度の計算ロジック（3要素の加重平均）
要素	重み	意味	算出方法
ベクトル類似度	50%	質問と参照チャンクがどれだけ近いか	ChromaDBの距離を変換
ヒットチャンク数	25%	複数チャンクが関連しているか	n_results中の有効ヒット数
コンテキスト長	25%	参照テキストの情報量が十分か	合計文字数に基づくスコア
"""


def calculate_confidence(results: list[dict]) -> dict:
    """検索結果から信頼度を算出する"""
    if not results:
        return {"score": 0, "details": {"similarity": 0, "coverage": 0, "context_richness": 0}}

    # 1. ベクトル類似度スコア(0-100)
    #       distanceが0に近いほど類似度が高い
    #       distance < 0.5 → 高類似度、> 1.5 → 底類似度
    avg_distance = sum(r["distance"] for r in results) / len(results)
    similarity_score = max(0, min(100, (1 - avg_distance / 2) * 100))

    # 2. ヒットカバレッジスコア(0-100)
    #       3件中、類似度が高い(distance < 1.0) チャンクが何件あるか
    relevant_count = sum(1 for r in results if r["distance"] < 1.0)
    coverage_score = (relevant_count / len(results)) * 100

    # 3. コンテキスト情報量スコア(0-100)
    #       参照テキストの合計文字数。500文字以上で満点
    total_chars = sum(len(r["content"]) for r in results)
    context_score = min(100, (total_chars / 500) * 100)

    # 加重平均
    confidencee = similarity_score * 0.50 + coverage_score * 0.25 + context_score * 0.25

    return {
        "score": round(confidencee, 1),
        "details": {
            "similarity": round(similarity_score, 1),
            "coverage": round(coverage_score, 1),
            "context_richness": round(context_score, 1),
        },
    }

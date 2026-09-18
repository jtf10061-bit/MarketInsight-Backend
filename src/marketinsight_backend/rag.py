# os: ファイルパスの結合やディレクトリ操作
import os

# re: 正規表現。「第1章」のような見出し書式の判定に使う
import re

# collections: Counterで「一番多く使われているフォントサイズ」を数えるのに使う
import collections

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


# 見出しらしい書き方のパターン  例: 第1章 / 第１条 / 1.2 / ３．
HEADING_PATTERN = re.compile(
    r"^(第[0-9０-９一二三四五六七八九十]+[編章節条項]|[0-9]+(\.[0-9]+)*[\s　]|[０-９]+[．.])"
)

# 見出しに見えるが中身が無いノイズ  例: 「2021 年4 月1 日制定」「第二十版」「12」
NOISE_PATTERN = re.compile(r"^[\d０-９\s　年月日改訂制定版第\.\-/]+$")


def extract_lines(file_path: str) -> list[dict]:
    """PDFを「行」単位で抽出する

    以前の extract_text_from_pdf は全ページを1本の文字列に連結していたため、
    「このテキストが何ページの何行目か」という情報が失われていた。
    行単位で取り出すことで、参照元として page / line_no を示せるようになる。

    返り値の1要素 = 1行
        page    : ページ番号(1始まり)
        line_no : そのページ内で何行目か(1始まり)
        text    : 行のテキスト
        size    : その行の最大フォントサイズ(見出し判定に使う)
        bold    : 太字が含まれるか
    """
    doc = fitz.open(file_path)
    lines = []
    # enumerate(doc, start=1): ページを1始まりの番号付きで回す
    for page_index, page in enumerate(doc, start=1):
        # get_text("dict"): ページの中身を「ブロック > 行 > スパン」の入れ子辞書で取得する
        #   span = 同じフォント・同じサイズが続くひとかたまり。ここに size と flags が入っている
        line_no = 0
        for block in page.get_text("dict")["blocks"]:
            # 画像ブロックには "lines" が無いので .get() で安全に取る
            for line in block.get("lines", []):
                # 1行は複数スパンに分かれることがあるので連結する
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text:
                    continue
                line_no += 1
                lines.append(
                    {
                        "page": page_index,
                        "line_no": line_no,
                        "text": text,
                        "size": max(round(span["size"], 1) for span in line["spans"]),
                        # flags の 4bit目(=16)が立っていると太字
                        "bold": any(span["flags"] & 16 for span in line["spans"]),
                    }
                )
    doc.close()
    return lines


def detect_body_size(lines: list[dict]) -> float:
    """本文のフォントサイズを推定する

    考え方: 文書の大半は本文なので「使われている文字数が多いサイズ」= 本文。

    ただし最頻の1サイズだけを本文とすると失敗する文書がある。
    例: 本文が 9.0 と 10.0 の2サイズで組まれているPDFでは、9.0 だけを本文とみなすと
        10.0 の本文まで「本文より大きい=見出し」と誤判定されてしまう。
    そこで「全体の10%以上の文字数を占めるサイズ」はすべて本文グループとみなし、
    その中の最大サイズを返す。これより大きいものだけが見出し候補になる。
    """
    counter = collections.Counter()
    for line in lines:
        counter[line["size"]] += len(line["text"])
    if not counter:
        return 0.0

    total = sum(counter.values())
    body_sizes = [size for size, count in counter.items() if count / total >= 0.1]
    if not body_sizes:
        # どのサイズも10%に満たない場合は、従来どおり最頻サイズを本文とする
        return counter.most_common(1)[0][0]
    return max(body_sizes)


def is_heading(line: dict, body_size: float) -> bool:
    """1行が見出しかどうかを判定する"""
    text = line["text"]
    # 長すぎる行は本文。短すぎる行は記号などのゴミ
    if len(text) > 60 or len(text) < 2:
        return False
    # 日付・版数だけの行(改訂履歴表など)は除外
    if NOISE_PATTERN.match(text):
        return False
    # 句点・読点で終わる行は文であって見出しではない
    # (「6.1.3 で対応する。」のような数字始まりの本文を弾くのに効く)
    if text[-1] in "。、.":
        return False
    # 「発行元:○○」「調査期間:○○」のようなメタ情報行を除外する
    # (見出しにコロンが入ることは稀なため)
    if "：" in text or ":" in text:
        return False
    # 1. 本文より明らかに大きい
    if line["size"] > body_size * 1.1:
        return True
    # 2. 太字かつ本文より大きい
    #    (本文と同じ大きさの太字は「本文中の強調」であることが多いので見出しにしない)
    if line["bold"] and line["size"] > body_size:
        return True
    # 3. サイズでは区別できないが「第1条」などの書式に一致(規程類がこれ)
    return bool(HEADING_PATTERN.match(text))


def find_toc_pages(lines: list[dict], body_size: float) -> set:
    """目次ページのページ番号を集める

    目次ページには章タイトルがずらりと並ぶため、見出し検出をそのまま走らせると
    目次の行を「本文中の章」と誤認してしまう。そこで目次ページは検出対象から外す。

    目次は複数ページに渡ることがあるので、「目次」と書かれたページだけでなく、
    そこから連続する「ほとんどの行が見出しに見えるページ」も目次とみなす。
    """
    # ページ番号 → そのページの行リスト
    by_page = collections.defaultdict(list)
    for line in lines:
        by_page[line["page"]].append(line)

    # 「目次」とだけ書かれた行を持つページが目次の起点
    starts = {
        line["page"]
        for line in lines
        if line["text"].replace(" ", "").replace("　", "") == "目次"
    }

    toc_pages = set(starts)
    for start in starts:
        page = start + 1
        # 起点の次ページ以降、見出しらしい行が過半を占める間は目次の続きとみなす
        while page in by_page:
            page_lines = by_page[page]
            heading_count = sum(1 for line in page_lines if is_heading(line, body_size))
            if not page_lines or heading_count / len(page_lines) < 0.5:
                break
            toc_pages.add(page)
            page += 1
    return toc_pages


def assign_sections(lines: list[dict], body_size: float) -> None:
    """各行に「直前に現れた見出し」を section として付与する(リストを直接書き換える)

    見出し検出はあくまで推定なので誤りが混ざる。ただしページ番号と行番号は常に正しいので、
    section がずれても「どこを見ればよいか」は失われない、という前提の設計。
    """
    toc_pages = find_toc_pages(lines, body_size)
    current = ""  # 直近の見出し。まだ1つも出てきていなければ空文字
    for line in lines:
        if line["page"] not in toc_pages and is_heading(line, body_size):
            current = line["text"]
        line["section"] = current


def split_into_chunks(
    lines: list[dict], chunk_size: int = 500, overlap: int = 100, min_chunk_size: int = 200
) -> list[dict]:
    """行のリストをチャンク(小さな塊)に分割する

    以前の split_info_chunks は文字数だけで機械的に切っていたため、
    チャンクが「どのページの何行目か」を持てなかった。
    行を積み上げる方式にすることで、チャンク先頭行から page / line / section を引き継げる。

    返り値の1要素 = 1チャンク
        content    : 本文
        page       : 開始ページ / page_end: 終了ページ
        line_start : 開始ページ内の開始行 / line_end: 終了ページ内の終了行
        section    : そのチャンクが属する章(取れなければ空文字)

    章の切り替わりはチャンクを区切る「候補」として扱う。ただし min_chunk_size に
    満たないうちは区切らない。見出し検出には誤りが混ざるため、章が変わるたびに
    必ず切ると数十文字の細切れチャンクが大量にでき、検索精度が落ちるため。
    """
    chunks = []
    start = 0  # 今のチャンクが lines の何番目から始まるか
    while start < len(lines):
        length = 0
        end = start
        # chunk_size 文字を超えるまで行を足す。length == 0 の条件で「最低1行は必ず入れる」
        # (1行が chunk_size より長い場合でも進めるようにするため)
        section = lines[start].get("section", "")
        while end < len(lines) and (length == 0 or length + len(lines[end]["text"]) <= chunk_size):
            # 章が切り替わったらそこでチャンクを確定する。
            # 1チャンクに複数の章が混ざると、先頭行の章が全体のラベルになってしまい
            # 「参照元の章」が実態とずれるため
            if lines[end].get("section", "") != section and length >= min_chunk_size:
                break
            length += len(lines[end]["text"])
            end += 1

        block = lines[start:end]
        head, tail = block[0], block[-1]
        chunks.append(
            {
                "content": "\n".join(line["text"] for line in block),
                "page": head["page"],
                "page_end": tail["page"],
                "line_start": head["line_no"],
                "line_end": tail["line_no"],
                "section": head.get("section", ""),
            }
        )

        if end >= len(lines):
            break

        # overlap: 末尾から overlap 文字ぶんの行を次のチャンクにも重ねる
        # (文の途中でぶつ切りになって意味が失われるのを防ぐ。目的は従来と同じ)
        back = 0
        acc = 0
        while back < len(block) - 1 and acc + len(block[-1 - back]["text"]) <= overlap:
            acc += len(block[-1 - back]["text"])
            back += 1
        # back は最大でも len(block)-1 なので、start は必ず1つ以上前進する(無限ループ防止)
        start = end - back

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
def save_to_cosmos(filename: str, chunks: list[dict]):
    # datetime.now(): 現在の日時を取得
    # .isoformat(): "2026-09-18T14:30:00.123456" のような国際標準形式の文字列に変換
    uploaded_at = datetime.now().isoformat()
    # チャンクを1つずつ、CosmosDBに保存する
    for i, chunk in enumerate(chunks):
        # upsert_item(): insert + update の合体メソッド
        # 同じidがなければ新規、あれば上書き更新する
        # → 同じPDFがアップロードされても重複しない
        rag_container.upsert_item(
            {
                "id": f"{filename}_{i}",  # 一意のID
                "filename": filename,  # どのファイルのチャンクか
                "chunk_index": i,  # 何番目のチャンクか
                "content": chunk["content"],  # チャンクのテキスト本文
                "page": chunk["page"],  # 開始ページ
                "page_end": chunk["page_end"],  # 終了ページ
                "line_start": chunk["line_start"],  # 開始行
                "line_end": chunk["line_end"],  # 終了行
                "section": chunk["section"],  # 属する章
                "uploaded_at": uploaded_at,  # アップロード時
            }
        )


def process_pdf(file_path: str, filename: str):
    """PDFを行抽出 → 章の付与 → チャンク分割 → Embedding生成 → ChromaDBに保存"""
    # Step2: 行単位でテキストを抽出(ページ番号・行番号・フォントサイズ付き)
    lines = extract_lines(file_path)

    # Step2-b: 本文サイズを推定し、各行に「直近の見出し」を付与する
    body_size = detect_body_size(lines)
    assign_sections(lines, body_size)

    # Step3: チャンク分割(ページ/行/章を引き継ぐ)
    chunks = split_into_chunks(lines)

    # Step4-5: Embedding生成 → ChromaDB保存
    # ChromaDBに渡す4つのリストを用意する
    ids = []
    embeddings = []
    documents = []
    metadatas = []

    # チャンクを1つずつ処理する
    for i, chunk in enumerate(chunks):
        # 一意のIDを作る 例："AI市場レポート.pdf_0", "AI市場レポート.pdf_1", ...
        chunk_id = f"{filename}_{i}"
        # チャンクをベクトルに変換する
        embedding = get_embedding(chunk["content"])
        # それぞれのリストに追加する
        ids.append(chunk_id)
        embeddings.append(embedding)
        documents.append(chunk["content"])
        # metadata に入れられるのは str / int / float / bool のみ(入れ子やNoneは不可)
        metadatas.append(
            {
                "filename": filename,
                "chunk_index": i,
                "page": chunk["page"],
                "page_end": chunk["page_end"],
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "section": chunk["section"],
            }
        )

    # ChromaDBに保存する
    collection.add(
        ids=ids,  # 各チャンクの一意の識別子
        embeddings=embeddings,  # ベクトル検索に使う数値配列
        documents=documents,  # 元のテキスト(検索結果として返す用)
        metadatas=metadatas,  # 付加情報(どのファイルの何ページ何行目か)
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

        meta = results["metadatas"][0][i]

        search_result.append(
            {
                "content": content,
                "filename": meta["filename"],
                "chunk_index": meta["chunk_index"],
                "distance": results["distances"][0][i],  # ベクトル距離(類似度の元データ)
                # 以下は再インデックス後のチャンクにしか入っていないため .get() で安全に取る
                # (古いチャンクが残っている場合は None / 空文字になる)
                "page": meta.get("page"),
                "page_end": meta.get("page_end"),
                "line_start": meta.get("line_start"),
                "line_end": meta.get("line_end"),
                # section が空なら、従来どおりチャンク先頭行を手がかりとして出す
                "section": meta.get("section") or first_line,
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

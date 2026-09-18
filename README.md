# MarketInsight-Backend

MarketInsightAI の API サーバーです（FastAPI）。

フロントエンド（`MarketInsight-Web-App`）からのリクエストを受け、
チャット履歴の CRUD と、エージェント実行結果の SSE 配信を担当します。

## 役割

このリポジトリは「窓口」であり、AI の処理そのものは持ちません。

| やること | やらないこと |
| --- | --- |
| HTTP エンドポイントの定義 | LLM の呼び出し（→ MCP-Agent） |
| リクエストのバリデーション（Pydantic） | 履歴の保存処理（→ History） |
| CORS の設定 | ツールの実装（→ MCP-Agent / MCP-Tool） |
| SSE でのストリーミング応答 | |

## 依存関係

```
Backend
 ├── marketinsight_agent   （MarketInsight-MCP-Agent）── run(), MODELS
 └── marketinsight_history （MarketInsight-History）  ── チャット CRUD
```

この 2 つを editable インストールしていないと import エラーで起動しません。

## ファイル構成

```
MarketInsight-Backend/
├── pyproject.toml
└── src/
    └── marketinsight_backend/
        └── main.py    # FastAPI アプリ本体（全エンドポイント）
```

## セットアップ

venv は使わず、pyenv の 3.10.13 に直接 editable インストールしています。

```bash
cd /Users/estyle-180/Documents/study/MarketInsightAI

pyenv local 3.10.13   # または pyenv global 3.10.13

pip install -e MarketInsight-MCP-Agent
pip install -e MarketInsight-History
pip install -e MarketInsight-Backend
```

`MarketInsight-Lib` は Backend の依存に入っていないため、インストール不要です
（`requires-python = ">=3.12"` のため 3.10.13 ではインストール自体が失敗します）。

環境変数はこのリポジトリでは持ちません。
Azure OpenAI のキーは `MarketInsight-MCP-Agent/.env`、
Cosmos DB のキーは `MarketInsight-History/.env` に置きます。

## 起動方法

```bash
cd /Users/estyle-180/Documents/study/MarketInsightAI/MarketInsight-Backend
uvicorn marketinsight_backend.main:app --reload --port 9000
```

停止は Ctrl+C です。

| オプション | 意味 |
| --- | --- |
| `marketinsight_backend.main:app` | `main.py` の `app` 変数を起動対象にする |
| `--reload` | ファイル変更を検知して自動再起動（開発用） |
| `--port 9000` | フロントの `API` 定数と合わせる必要がある |

editable インストール済みなのでどのディレクトリからでも起動できますが、
`--reload` はカレントディレクトリ配下を監視するため、Backend の変更だけを拾いたい場合は
上記のとおり Backend ディレクトリで実行します。

動作確認：

```bash
curl http://127.0.0.1:9000/health
# => {"status":"ok"}

curl http://127.0.0.1:9000/models
```

API ドキュメント（FastAPI が自動生成）：`http://127.0.0.1:9000/docs`

## エンドポイント

| メソッド | パス | リクエストボディ | 用途 |
| --- | --- | --- | --- |
| GET | `/health` | – | ヘルスチェック |
| GET | `/models` | – | モデル一覧（`config.MODELS`） |
| GET | `/chats/{user_id}` | – | チャット一覧 |
| POST | `/chats` | `CreateChatRequest` | チャット作成 |
| DELETE | `/chats/{user_id}/{chat_id}` | – | チャット削除 |
| PATCH | `/chats/{user_id}/{chat_id}/favorite` | – | お気に入り切替 |
| PATCH | `/chats/{user_id}/{chat_id}/title` | `UpdateTitleRequest` | タイトル更新 |
| POST | `/chats/{user_id}/{chat_id}/messages` | `AddMessageRequest` | メッセージ保存 |
| POST | `/chat` | `ChatRequest` | エージェント実行（SSE） |

### リクエストモデル

```python
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
```

引数の型が `BaseModel` を継承していると、FastAPI は「JSON ボディで受け取る」と解釈します。
`str` や `int` の場合は URL パラメータとして扱われます。

## `/chat` の仕組み（SSE）

`/chat` だけは他と異なり、`StreamingResponse` で `text/event-stream` を返します。

```
1. chat_id があれば History から過去のメッセージを取得
2. 最新のユーザー発言を history に追加
3. marketinsight_agent.react_loop.run() をジェネレータとして回す
4. 各ステップを "data: {...}\n\n" 形式で 1 件ずつ送信
```

送られるステップの形：

```json
{"type": "thought",     "content": "..."}
{"type": "action",      "content": "web_search({'query': '...'})"}
{"type": "observation", "content": "- [タイトル](URL): 概要"}
{"type": "answer",      "content": "最終回答（Markdown）"}
{"type": "error",       "content": "エラーが発生しました: ..."}
```

`return` ではなく `yield` を使うことで「全部できてからまとめて返す」のではなく
「できたものを 1 つずつ返す」形になり、フロント側で思考過程を逐次表示できます。

`time.sleep(1)` が入っているのは、ステップの切り替わりを目で追えるようにするための演出です。

## CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

ブラウザは異なるオリジン間の通信をデフォルトでブロックするため、この設定が必要です。
フロントを 5173 以外のポートで動かす場合はここも変更します。

## トラブルシューティング

| 症状 | 原因と対処 |
| --- | --- |
| `ModuleNotFoundError: marketinsight_agent` | MCP-Agent を `pip install -e` していない |
| `ModuleNotFoundError: marketinsight_history` | History を `pip install -e` していない |
| `[Errno 48] Address already in use` | 9000 が他プロセスに使われている。`lsof -nP -iTCP:9000 -sTCP:LISTEN` で確認（VSCode の Jupyter カーネルが掴んでいることがある）。カーネルを停止するか `--port 9001` で起動（フロントの `API` 定数も合わせる） |
| Pylance だけ `could not be resolved`（実行は通る） | `.pth` のスキャンが古い。`Developer: Reload Window` か `Python: Restart Language Server`。インタープリタが `~/.pyenv/versions/3.10.13/bin/python` か確認 |
| 起動時に Cosmos DB のエラー | `MarketInsight-History/.env` の 4 変数を確認（import 時に接続する作りのため） |
| フロントで CORS エラー | `allow_origins` とフロントのポートが不一致 |
| `/chat` が `error` イベントを返す | Azure OpenAI の認証情報、またはデプロイ名を確認 |

## 今後の整備ポイント

- `main.py` に全エンドポイントが入っている。`api/routes/`、`api/schemas/`、`service/` に
  分割すると見通しがよくなる（当初の構成案どおり）
- `/chat` 内の `from marketinsight_history...` がローカル import になっており、
  例外を `except Exception: pass` で潰している。失敗理由が見えない状態
- テストが未整備（`tests/` なし）

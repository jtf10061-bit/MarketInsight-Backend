import os
from dotenv import load_dotenv
# msal: Azureにログインしてトークン(認証の一時チケット)を取得する
import msal
# requests: HTTPリクエストを送るライブラリで、Graph APIの呼び出しに使う
import requests

load_dotenv()

# 認証情報の定数
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")

# 認証の設定
# AUTHORITY: このテナントの認証サーバーを使うというURL
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
# SCOPES: Microsoft Graph APIの権限を全部使うという指定
# SCOPES = ["https://graph.microsoft.com/User.Read",
#            "https://graph.microsoft.com/Sites.ReadWrite.All"]
# SCOPES = ["https://graph.microsoft.com/User.Read"]
SCOPES = ["https://graph.microsoft.com/Files.Read"]

# トークンの取得
# PublicClientApplication: シークレットを持つアプリとして認証クライアントを作る
app = msal.PublicClientApplication(
    CLIENT_ID,
    authority=AUTHORITY
)
# acquire_token_by_device_flow: アプリ自身としてトークンを取得する
flow = app.initiate_device_flow(scopes=SCOPES)
print(flow["message"])  # ブラウザでログインする指示が表示される
result = app.acquire_token_by_device_flow(flow)

# エラーチェック
if "access_token" not in result:
    print("トークン取得失敗", result.get("error_description"))
    exit()

# APIリクエストの準備
# access_token: APIを呼ぶための入場券
token = result["access_token"]
# Authorization: Bearer ... : HTTPヘッダーにトークンをつけてAPIに送る(入場券を持っているよ という意味)
headers = {"Authorization": f"Bearer {token}"}

# SharePointルートサイトのドキュメント一覧を取得
# url: Graph APIのエンドポイント。SharePointルートサイトのドライブ(ドキュメントライブラリ)一覧を取得
# url = "https://graph.microsoft.com/v1.0/sites/estyleinc.sharepoint.com/drives"
# url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
url = "https://graph.microsoft.com/v1.0/me/drive/root/children"

# requests.get: GETリクエストを送信
response = requests.get(url, headers=headers)
# status_code: 200なら成功 / 400系ならエラー
print("Status:", response.status_code)
# response.json(): 帰ってきたJSON(ドライブ一覧)を表示
print(response.json())


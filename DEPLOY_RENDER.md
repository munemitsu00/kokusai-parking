# Renderへの公開手順（無料・閲覧専用プレビュー）

## 現状
- `render.yaml` と `Dockerfile` を用意済み。
- 環境変数 `DEMO_MODE=true` で起動するとサンプルの12施設を自動登録します。
- **公開プレビューは読み取り専用**。会員登録、駐車場投稿、ポイント交換、管理画面の編集を停止しています。
- 地図の正確な入口座標は未確認です。位置を捏造しないため、確認前の駐車場にはピンが表示されません。
- SEOインデックスは無効化。独自料金は現地未確認として案内します。

## GitHubに置く
1. https://github.com/new で `kokusai-parking` というリポジトリを作成。
2. 公開リポジトリならRenderから取得しやすくなります（秘密情報はこのソースコードに含めないこと）。
3. 「Add a README file」にチェックを入れて作成すると `main` ブランチができます。
4. ChatGPTのGitHub連携からファイルを登録するか、このフォルダの全ファイルをGitHubにプッシュします。

## Renderで無料公開
- GitHubリポジトリにコードが上がったら、RenderでWeb Serviceを作成します。
- Runtime: Docker、Region: Singapore、Plan: Free、Branch: main。
- 環境変数:
  - `ENVIRONMENT=production`
  - `DEMO_MODE=true`
  - `SESSION_SECURE=true`
  - `SECRET_KEY`: 十分長い安全なランダム文字列（公開しない）
- `APP_BASE_URL` は省略可能。Renderが付与する `RENDER_EXTERNAL_URL` を自動参照します。
- ヘルスチェックは `/healthz` にします。
- `render.yaml` を使うBlueprint方式でも同等の設定で作成できます。

## なぜ会員登録を止めたか
Renderの無料Web Serviceは保存領域が一時的で、15分間アクセスがないと休止し、再起動でSQLite・アップロード画像が消えます。個人情報やポイントを保存するのは危険です。

本番の会員登録・投稿運用を始める前には、永続データベースへの移行と写真用オブジェクトストレージが必要です。Render無料Postgresは30日で期限切れになります。本番運用では長期利用できるDB/バックアップを選んでください。またRenderの無料Web Serviceでは通常のSMTP送信ポート(465/587)がブロックされるため、メール認証にはHTTPS経由のメールAPIなどへの変更が必要です。

## 検証
`python -m pytest tests -q` でテストできます。公開後は `https://<サービス名>.onrender.com/healthz` にアクセスし `{"status":"ok"}` を確認してください。

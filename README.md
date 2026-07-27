# Oasis Voice Bot

Discordのテキスト投稿をVOICEVOXで音声合成し、ボイスチャンネルで読み上げるBotです。
音声合成部分を分離しているため、後から本人同意済みの録音サンプルを使う
音声クローンサービスを追加できます。

## 現在の機能

- `/join` を実行したテキストチャンネルの投稿を読み上げ
- `/leave`、`/skip`
- `/voices` でVOICEVOXの話者・スタイルIDを確認
- `/voice voicevox 3` のように音声を変更
- URL省略、絵文字除去、長文制限、30件の再生キュー
- Railway用のDockerfileと`/health`エンドポイント
- 後付け音声クローン用HTTPインターフェース

## Discord Botの準備

1. Discord Developer PortalでApplicationとBotを作成します。
2. Bot設定で **Message Content Intent** を有効にします。
3. OAuth2 URL Generatorで `bot` と `applications.commands` を選びます。
4. Bot権限は最低限 `View Channels`、`Send Messages`、`Connect`、
   `Speak`、`Use Voice Activity` を付与します。
5. `.env.example`を`.env`へコピーし、`DISCORD_TOKEN`を設定します。

トークンをGitへコミットしないでください。

## ローカル実行

Python 3.12、FFmpeg、VOICEVOX Engineが必要です。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
docker run -d --name oasis-voicevox -p 50021:50021 voicevox/voicevox_engine:cpu-latest
python -m oasisvoicebot
```

Discordでボイスチャンネルに入り、読み上げ対象のテキストチャンネルで
`/join`を実行します。ずんだもんのスタイルIDはVOICEVOX Engineの
バージョンで確認するため、`/voices`の結果から選んでください。

## Railwayへのデプロイ

BotとVOICEVOX Engineを別々のRailwayサービスとして構成します。

### 1. VOICEVOX Engineサービス

RailwayプロジェクトにDocker Imageサービスを追加し、
イメージを `voicevox/voicevox_engine:cpu-latest` にします。
起動コマンドではEngineが `0.0.0.0:50021` をListenするよう設定し、
Private Networkのホスト名を控えます。VOICEVOXの利用規約・各音声ライブラリの
規約と、Railwayのメモリ使用量も事前に確認してください。

### 2. Botサービス

このGitHubリポジトリからサービスを作成します。ルートのDockerfileが自動使用されます。
Variablesに以下を設定します。

```dotenv
DISCORD_TOKEN=...
VOICEVOX_URL=http://VOICEVOXサービスのPrivate-Networkホスト:50021
DEFAULT_SPEAKER_ID=3
SPEECH_SPEED=1.15
MAX_TEXT_LENGTH=180
```

SettingsのHealthcheck Pathには `/health` を指定します。Botサービスは
複数Replicaにすると同じ投稿を重複して読むため、1 Replicaで運用してください。

## 録音サンプル由来の音声を追加する

Botは次の契約を満たす別サービスへ接続できます。

```http
POST /synthesize
Authorization: Bearer <任意のAPIキー>
Content-Type: application/json

{"text":"読み上げる文章","voice_id":"voice-model-id"}
```

レスポンスは `audio/wav` のバイト列にします。接続時はRailway Variablesへ設定します。

```dotenv
CLONE_TTS_URL=http://クローン音声サービスのPrivate-Networkホスト:ポート
CLONE_TTS_API_KEY=...
CLONE_VOICES_JSON={"yuuyuu":"voice-model-id"}
```

Discordでは `/voice clone yuuyuu` で切り替えます。学習・生成サービスはBotとは
別リポジトリ／別Railwayサービスにするのがおすすめです。録音データは本人が同意した
用途・保存期間・アクセス範囲に限定し、公開リポジトリへ置かないでください。

## テスト

```powershell
pytest
```

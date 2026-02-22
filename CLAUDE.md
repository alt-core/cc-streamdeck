# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cc_streamdeck は、Claude Code の権限確認プロンプト（Yes/No/Always 等）を Stream Deck Mini（6ボタン、3x2グリッド、各80x80px）に表示し、ボタン押下で応答するシステム。6ボタン全体を1つのディスプレイとして使い、メッセージ表示領域を最大化しつつ選択肢をコンパクトに表示する。

## Architecture

```
Claude Code
  ↓ PermissionRequest Hook (stdin: JSON)
Hook Client (cc-streamdeck-hook, 短命プロセス)
  ↓ Unix domain socket (/tmp/cc_streamdeck.sock)
Stream Deck Daemon (cc-streamdeck-daemon, 常駐プロセス)
  ↓ python-elgato-streamdeck
Stream Deck Mini (6ボタン表示 + ボタン入力)
  ↓ ボタン押下
Hook Client へ応答返却
  ↓ stdout: JSON
Claude Code へ結果返却
```

### ソースコード構成

- `src/cc_streamdeck/config.py` — 共有定数（ソケットパス、タイムアウト、キーサイズ等）
- `src/cc_streamdeck/protocol.py` — IPCメッセージ型（PermissionRequest/Response）+ NDJSON encode/decode
- `src/cc_streamdeck/renderer.py` — PIL画像生成。6ボタンレイアウト計算、メッセージ合成画像のタイル分割、選択肢ボタン描画
- `src/cc_streamdeck/device.py` — DeviceState: Stream Deck接続管理、ホットプラグポーリング（3秒間隔）、スレッドセーフな画像設定
- `src/cc_streamdeck/daemon.py` — Daemon本体: Unixソケットサーバ、リクエスト→レンダリング→ボタン待ち→応答の統合
- `src/cc_streamdeck/hook.py` — Hook Client: stdin JSON→Daemon通信→stdout JSON。Daemon自動起動。エラー時はexit 0でフォールバック
- `src/cc_streamdeck/fonts/` — PixelMplus10フォント（M+ FONT LICENSE）

### Hook連携: PermissionRequest

Claude Code の **PermissionRequest** hook を使用。権限確認ダイアログが表示されるタイミングでのみ発火する。

**stdinで受信するJSON:**
```json
{
  "hook_event_name": "PermissionRequest",
  "tool_name": "Bash",
  "tool_input": { "command": "rm -rf node_modules" },
  "permission_suggestions": [{ "type": "toolAlwaysAllow", "tool": "Bash" }]
}
```

**stdoutで返すJSON:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": { "behavior": "allow", "updatedPermissions": [] }
  }
}
```

### Daemon自動起動とデバイス状態管理

Hook Client はソケット接続失敗時にDaemonを自動起動（lazy init）。Daemonは `DeviceState.status` で `"ready"` / `"no_device"` を管理し、3秒間隔のポーリングでUSBホットプラグに対応。Hook Clientはデバイス状態を気にせずDaemonの応答だけで分岐する。

### スレッドモデル (daemon.py)

- メインスレッド: Unix socketサーバ（accept loop）
- 接続ハンドラスレッド: Hook Client接続の処理（`_request_lock`で直列化）
- デバイスポーリングスレッド: DeviceStateのデーモンスレッド
- StreamDeckライブラリ内部スレッド: `_key_callback` 配信（`with deck:` で排他制御）

## Commands

```bash
# パッケージ管理
uv sync                    # 依存関係インストール
uv add <package>           # パッケージ追加

# テスト
uv run pytest              # 全テスト実行
uv run pytest tests/test_renderer.py::TestComputeLayout  # 単一テスト実行
uv run pytest -x           # 最初の失敗で停止

# リント・フォーマット
uv run ruff check .        # リントチェック
uv run ruff format .       # フォーマット
uv run ruff check --fix .  # 自動修正

# 実行
uv run cc-streamdeck-daemon   # Daemon手動起動
```

## Tech Stack

- Python 3.11+ (uv, pyproject.toml, hatchling)
- streamdeck (python-elgato-streamdeck, Stream Deck制御)
- Pillow (ボタン画像生成)
- PixelMplus10 (バンドルフォント、等幅、日本語対応)
- pytest, ruff

## Design Principles

- Stream Deck Mini 6ボタンを1つの連結ディスプレイとして扱う
- メッセージ表示領域を最大化、選択肢は最小限のスペースで表示
- Hook Client は高速起動・応答が必須（Claude Code がブロックされるため）
- あらゆるエラーで exit 0（出力なし）→ 端末フォールバック（ユーザーがスタックしない）

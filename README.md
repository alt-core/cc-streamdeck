# cc-streamdeck

Claude Code の権限確認プロンプト（Allow / Deny / Always Allow）を Stream Deck Mini に表示し、ボタン押下で応答するシステム。

```
Claude Code → PermissionRequest Hook → Hook Client → Unix Socket → Daemon → Stream Deck Mini
                                                                          ← ボタン押下 ← ユーザー
```

## Stream Deck Mini レイアウト (3x2)

```
3選択肢時:                 2選択肢時:
┌──────┬──────┬──────┐     ┌──────┬──────┬──────┐
│ msg  │ msg  │ msg  │     │ msg  │ msg  │ msg  │
├──────┼──────┼──────┤     ├──────┼──────┼──────┤
│ Deny │Always│Allow │     │ msg  │ Deny │Allow │
└──────┴──────┴──────┘     └──────┴──────┴──────┘
```

- 全6ボタンがメッセージ表示領域として使われ、選択肢は下段ボタンの下部20pxに色付きラベルとしてオーバーレイ
- **Always はトグル式**: 1回押すとON/OFF切替（色が変わる）。ON状態で Allow を押すと Always Allow として送信
- テキスト量に応じてフォントサイズを自動選択（20px → 16px → 10px）。溢れる場合は `...` で省略

## 前提条件

- macOS または Linux
- Stream Deck Mini（Discord Edition 含む）
- [Claude Code](https://claude.ai/code) CLI
- Python 3.11+
- hidapi:
  ```bash
  # macOS
  brew install hidapi
  # Debian/Ubuntu
  sudo apt install libhidapi-libusb0 libudev-dev libusb-1.0-0-dev
  ```

## インストール

```bash
# pip
pip install git+https://github.com/alt-core/cc-streamdeck.git

# uv
uv pip install git+https://github.com/alt-core/cc-streamdeck.git

# ソースから
git clone https://github.com/alt-core/cc-streamdeck.git
cd cc-streamdeck
uv sync
```

## Claude Code Hook 設定

`~/.claude/settings.json` に以下を追加。`command` にはインストール先の絶対パスを指定:

```bash
# パスの確認
which cc-streamdeck-hook          # pip install の場合
# または
echo $(pwd)/.venv/bin/cc-streamdeck-hook  # uv sync の場合
```

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/cc-streamdeck-hook",
            "timeout": 86400
          }
        ]
      }
    ]
  }
}
```

> **Note**: `timeout` は秒単位。86400 = 24時間。離席して戻ってきても待機し続ける。

## 使い方

Daemon は Hook Client から自動起動されるため、特別な操作は不要。Claude Code が権限確認を求めると、Stream Deck Mini にメッセージと選択肢が表示される。

```bash
# 手動で Daemon を起動
cc-streamdeck-daemon

# Daemon を停止
cc-streamdeck-daemon --stop
```

### 動作仕様

- **Daemon 自動起動**: Hook Client がソケット接続に失敗すると Daemon をバックグラウンドで自動起動
- **ホットプラグ**: Stream Deck 未接続でも Daemon は待機し、接続されると自動検知（3秒間隔ポーリング）
- **フォールバック**: デバイス未接続時やエラー時は通常の端末確認プロンプトにフォールバック
- **PPID ベースキャンセル**: ターミナルで応答後に次のリクエストが来ると、同じ Claude インスタンスからの古いリクエストを自動キャンセル
- **自動終了**: Stream Deck が24時間接続されない場合、Daemon は自動終了

## 開発

```bash
uv sync                    # 依存関係インストール
uv run pytest              # テスト実行
uv run pytest -x           # 最初の失敗で停止
uv run ruff check .        # リント
uv run ruff format .       # フォーマット
```

## ライセンス

- コード: MIT License
- 同梱フォント: M PLUS 1 Code ([SIL OFL 1.1](https://scripts.sil.org/OFL)), PixelMplus10 ([M+ FONT LICENSE](http://mplus-fonts.sourceforge.jp/mplus-outline-fonts/))

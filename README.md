# cc-streamdeck

Claude Code の権限確認プロンプト（Allow / Deny / Always Allow）を Stream Deck Mini に表示し、ボタン押下で応答するシステム。

```
Claude Code → PermissionRequest Hook → Hook Client → Unix Socket → Daemon → Stream Deck Mini
                                                                          ← ボタン押下 ← ユーザー
```

## Stream Deck Mini レイアウト (3x2)

```
3選択肢時:               2選択肢時:
┌─────┬─────┬─────┐     ┌─────┬─────┬─────┐
│ msg │ msg │ msg │     │ msg │ msg │ msg │
├─────┼─────┼─────┤     ├─────┼─────┼─────┤
│Allow│Deny │Alwys│     │ msg │Allow│Deny │
└─────┴─────┴─────┘     └─────┴─────┴─────┘
```

## 前提条件

- macOS または Linux
- Stream Deck Mini
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
# uv (推奨)
uv tool install cc-streamdeck

# pip
pip install cc-streamdeck
```

### 開発用インストール

```bash
git clone https://github.com/yourname/cc-streamdeck.git
cd cc-streamdeck
uv sync
```

## Claude Code Hook 設定

`~/.claude/settings.json` に以下を追加:

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cc-streamdeck-hook",
            "timeout": 600
          }
        ]
      }
    ]
  }
}
```

## 使い方

Daemon は Hook Client から自動起動されるため、特別な操作は不要。Claude Code が権限確認を求めると、Stream Deck Mini にメッセージと選択肢が表示される。

手動で Daemon を起動する場合:

```bash
cc-streamdeck-daemon
```

Stream Deck が未接続の場合、Daemon は `no_device` 状態で待機し、接続されると自動で切り替わる。未接続時のフックは通常の端末確認プロンプトにフォールバックする。

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
- 同梱フォント (PixelMplus10): M+ FONT LICENSE

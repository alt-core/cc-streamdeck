# cc-streamdeck

Claude Code の権限確認プロンプト（Allow / Deny / Always Allow）を Stream Deck に表示し、ボタン押下で応答するシステム。

> **注意**: このプロジェクトは個人利用のために作ったものを、同じことをしたい方の参考になればと思い公開しています。**動作確認は Stream Deck Mini のみ**で行っており、他モデル（Original/MK.2/XL/Plus）では未検証です。Mini 以外で問題がある場合は fork して修正してください。Issue やサポート対応は行っていません。

```
Claude Code → Hook (stdin JSON) → Hook Client → Unix Socket → Daemon → Stream Deck
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
- **ヘッダ色でリスク表示**: ツール名ヘッダの背景色+文字色が操作の危険度に応じて4段階に変化
- **ボディ色でインスタンス識別**: 複数の Claude Code を並行実行しているとき、ボディ背景色でどのインスタンスかを区別。出現順に以下のパレットから割当:

  | # | 色 | Hex |
  |---|------|---------|
  | 1 | 暗い紺 | `#0A0A20` |
  | 2 | 暗い緑 | `#0A200A` |
  | 3 | 暗い赤茶 | `#200A0A` |
  | 4 | 暗い黄土 | `#1A1A0A` |
  | 5 | 暗い紫 | `#150A20` |
  | 6 | 暗いティール | `#0A1A1A` |
  | 7 | 暗い赤 | `#1A0A0A` |
  | 8 | 暗い鋼青 | `#0A1020` |
  | 9 | 暗い茶 | `#1A100A` |
  | 10 | 暗い深夜 | `#0A0A10` |

  パレット数を超えると色が循環する。設定ファイルの `[colors.instance] palette` でカスタマイズ可能（後述）

## 前提条件

- macOS または Linux
- Stream Deck（Mini, Original/MK.2, XL, Plus — Mini 以外は未検証）
  > **Elgato 公式の Stream Deck ソフトウェアは終了してください。** 本ツールは USB/HID で直接デバイスと通信するため、公式ソフトウェアと同時には使用できません。
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

Daemon は Hook Client から自動起動されるため、特別な操作は不要。Claude Code が権限確認を求めると、Stream Deck にメッセージと選択肢が表示される。

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
- **表示ガード**: 表示切替直後のボタン押下を無視する猶予時間（PermissionRequest/AskUserQuestion はデフォルト 500ms）
- **自動終了**: Stream Deck が24時間接続されない場合、Daemon は自動終了

### AskUserQuestion 対応

Claude Code が AskUserQuestion ツールで選択肢を提示する際、Stream Deck のボタンに選択肢を直接表示し、ボタン押下で回答できる。選択肢のラベルと説明文がボタンに表示され、選択済みのボタンは色が変わる。複数の質問がある場合はページ送りで順に回答し、最後に確認ページで Submit する。

> **注意**: AskUserQuestion が PermissionRequest hook を発火するのは Claude Code の現時点でのバグ ([#15400](https://github.com/anthropics/claude-code/issues/15400)) であり、将来のアップデートで振る舞いが変わる可能性がある。その場合、本機能は動作しなくなる。

### リスクレベル色

ヘッダ（ツール名）の背景色と文字色で操作の危険度を表示:

| レベル | 対象の例 |
|--------|----------|
| **critical** (赤) | `rm -rf`, `sudo`, `git push --force`, `curl\|bash` |
| **high** (琥珀) | `rm`, `git push`, `curl`, `pip install`, `mv` |
| **medium** (紺) | Write, Edit, WebFetch, 未知コマンド |
| **low** (暗灰) | `ls`, `cat`, `git status`, `npm test` |

### Notification 表示（オプション）

Claude Code の Notification hook を設定すると、入力待ちなどの状態を Stream Deck 最下段にさりげなく表示できる。権限確認ほど重要ではないが、複数インスタンスの状況を把握したいときに便利。

使いたい場合は `~/.claude/settings.json` に Notification hook を追加:

```json
{
  "hooks": {
    "PermissionRequest": [ ... ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/cc-streamdeck-hook"
          }
        ]
      }
    ]
  }
}
```

表示する通知の種類は `~/.config/cc-streamdeck/config.toml` で絞り込める（デフォルトは全種類）:

```toml
[notification]
types = ["idle_prompt"]  # idle_prompt のみ表示
```

### Deny 後・通知 dismiss 後のウィンドウ フォーカス（オプション）

Deny ボタン押下後や Notification の OK 押下後に、Claude Code のターミナルや Claude Desktop を自動的にアクティブにできる。設定しない場合（デフォルト）は何もしない。

```toml
[focus]
# on_deny と on_notification に実行するシェルコマンドを指定。
# 省略した場合・空文字の場合は何もしない。
#
# "auto" を指定すると macOS 限定で自動検出:
#   client_pid（Claude Code のプロセス）の親プロセス（ターミナル）を
#   ps + osascript で取得してフォアグラウンドに移動。
#   他の OS では何もしない。
on_deny         = "auto"
on_notification = "auto"

# アプリを直接指定する場合の例:
# on_deny = "osascript -e 'tell application \"iTerm2\" to activate'"
# on_deny = "osascript -e 'tell application \"Terminal\" to activate'"
# on_deny = "osascript -e 'tell application \"Ghostty\" to activate'"
# on_deny = "open -a Claude"   # Claude Desktop をフォーカス（macOS）
# on_deny = "wmctrl -a claude" # Linux (X11, wmctrl 要インストール)
```

| 値 | 動作 |
|---|---|
| `""` (省略) | 何もしない |
| `"auto"` | macOS: `ps` + `osascript` でターミナルを自動アクティブ。他 OS は no-op |
| その他の文字列 | `shell=True` でシェルコマンドとして実行 |

### 設定ファイル

`~/.config/cc-streamdeck/config.toml` で色やリスク評価ルールをカスタマイズ可能（全セクション省略可）:

```toml
[colors.risk]
critical_bg = "#800000"
critical_fg = "#FFFFFF"

[colors.instance]
palette = [
    "#0A0A20", "#0A200A", "#200A0A", "#1A1A0A", "#150A20",
    "#0A1A1A", "#1A0A0A", "#0A1020", "#1A100A", "#0A0A10",
]

[risk.tools]
Bash = "evaluate"   # コマンド内容でパターン評価（デフォルト）
Write = "high"      # 固定リスクレベルも指定可

# Bash パターンの追加（built-in ルールの前に評価される）
[[risk.bash.prepend]]
name = "terraform-destroy"
pattern = "terraform * destroy"     # シンプル記法: * → .*, 単語間は \s+
level = "critical"

[[risk.bash.prepend]]
name = "my-safe-tool"
pattern = "regex:^\\s*my-safe-tool\\b"  # regex: プレフィックスで正規表現
level = "low"

# Write/Edit のファイルパスによるリスク引き上げ
[risk.path_high]
patterns = ['/etc/.*', '.*\.env$']

# 表示する通知の種類（Notification hook 使用時）
[notification]
types = ["idle_prompt", "auth_success", "elicitation_dialog"]  # デフォルト

# 表示切替後のボタン押下ガード時間（ミリ秒）
[display]
guard_ms = 500         # PermissionRequest / AskUserQuestion（デフォルト 500）
minor_guard_ms = 0     # Fallback / Notification（デフォルト 0）
guard_dim = false      # ガード中にラベル文字を暗くする（デフォルト false）

# Deny 押下・通知 dismiss 時のウィンドウ フォーカス
[focus]
# on_deny          = "auto"    # macOS: ps + osascript でターミナルを自動アクティブ
# on_notification  = "auto"
# on_deny = "osascript -e 'tell application \"iTerm2\" to activate'"
# on_deny = "osascript -e 'tell application \"Terminal\" to activate'"
# on_deny = "open -a Claude"   # Claude Desktop をフォーカス
```

設定はデーモン起動時に1回読み込み。変更後は `cc-streamdeck-daemon --stop` で停止すれば、次の hook 呼び出し時に自動再起動される。

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

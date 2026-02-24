# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cc_streamdeck は、Claude Code の権限確認プロンプト（Allow/Deny/Always）を Stream Deck に表示し、ボタン押下で応答するシステム。全ボタンを1つの連結ディスプレイとして使い、メッセージ表示領域を最大化しつつ選択肢をコンパクトに表示する。Mini/Original/MK.2/XL/Plus に対応（動的グリッドレイアウト）。動作確認は Mini のみ。

## Architecture

```
Claude Code
  ↓ PermissionRequest / Notification / Stop Hook (stdin: JSON)
Hook Client (cc-streamdeck-hook, 短命プロセス)
  ↓ Unix domain socket (/tmp/cc_streamdeck.sock)
Stream Deck Daemon (cc-streamdeck-daemon, 常駐プロセス)
  ↓ python-elgato-streamdeck
Stream Deck (ボタン表示 + ボタン入力)
  ↓ ボタン押下
Hook Client へ応答返却 (PermissionRequest のみ)
  ↓ stdout: JSON
Claude Code へ結果返却
```

### ソースコード構成

- `src/cc_streamdeck/config.py` — 共有定数（ソケットパス、タイムアウト、キーサイズ等）
- `src/cc_streamdeck/protocol.py` — IPCメッセージ型（PermissionRequest/Response/NotificationMessage）+ NDJSON encode/decode。`client_pid` でClaude インスタンス識別。`ask_answers` でAskUserQuestion回答を伝送
- `src/cc_streamdeck/settings.py` — TOML設定ファイル読み込み（`~/.config/cc-streamdeck/config.toml`、XDG準拠）。tomllib使用
- `src/cc_streamdeck/risk.py` — リスク評価エンジン。4段階（critical/high/medium/low）、Bashパターンマッチ、パス引き上げ、インスタンスパレット管理
- `src/cc_streamdeck/renderer.py` — PIL画像生成。動的グリッドレイアウト計算、フォントフォールバック、メッセージ合成画像のタイル分割、選択肢ラベル描画、AskUserQuestion全面ボタン描画（ラベル+description）、フォールバックメッセージ表示、Notification表示（最下段のみ）。ヘッダ背景色（リスク）・ボディ背景色（インスタンス）パラメータ対応
- `src/cc_streamdeck/device.py` — DeviceState: Stream Deck接続管理、ホットプラグポーリング（3秒間隔）、スレッドセーフな画像設定、`get_grid_layout()` でデバイスのグリッドサイズ取得、24時間未接続で自動終了。Mini Discord Edition (PID 0x00B3) のパッチ含む
- `src/cc_streamdeck/daemon.py` — Daemon本体: Unixソケットサーバ、統一キュー（`_items`）による表示管理。`_DisplayItem` で全種別を統一、`_select_and_display()` で表示判定。`_add_item`（notification上書き）、`_remove_item`（除去+再計算）、`_purge_connected_items`（stale items 一括削除）、`_wait_for_resolution`（per-item `done_event` で接続スレッドがブロック）。`_key_callback` → `_handle_permission_key`/`_handle_ask_key` で種別ごとのボタン処理。`_handle_stop_hook` で Stop hook 処理（パージ + Done 通知）。`_AskQuestionState` でページ遷移・回答管理。設定読み込み
- `src/cc_streamdeck/hook.py` — Hook Client: stdin JSON→Daemon通信→stdout JSON。Daemon自動起動（sys.executable親ディレクトリからパス解決）。AskUserQuestion時は`updatedInput.answers`で回答返却。Notification/Stop hookはfire-and-forget（応答不要、daemon未起動時はスキップ）。エラー時はexit 0でフォールバック。`status="open"` 時は `cc-streamdeck-focus` を呼んでターミナルにフォーカス
- `src/cc_streamdeck/focus.py` — ターミナルフォーカスコマンド（cc-streamdeck-focus）。PIDからプロセスツリーを辿ってターミナルアプリを特定し、tmuxペイン選択→TTYベースのタブ選択→アプリアクティベートの3層でフォーカス。macOS専用、標準ライブラリのみ
- `src/cc_streamdeck/fonts/` — M PLUS 1 Code (SIL OFL, AA描画用), PixelMplus10 (M+ FONT LICENSE, dot-by-dot用)

### ボタンレイアウト

レイアウトは `deck.key_layout()` から動的計算。`compute_layout()` が選択肢を下段右詰めに配置:

```
3択時 (Mini 3x2):         2択時:
[msg  ] [msg  ] [msg  ]   [msg  ] [msg  ] [msg  ]
[Deny ] [Alwys] [Allow]   [msg  ] [Deny ] [Allow]
```

- 全ボタンがメッセージ表示領域。選択肢は下段ボタンの下部20pxに色付きラベルとしてオーバーレイ
- Allow=右下, Deny=Allowの左, Always=DenyとAllowの間（トグル式）
- Alwaysトグル: 1回押すとON/OFF切替。ON状態でAllowを押すとAlways Allow
- Always非アクティブ時はラベル文字がグレー、アクティブ時は白

### Go CC ボタン（macOS 自動有効）

macOS（`sys.platform == "darwin"`）では全てのビューの右上キーに "Go CC" ボタンが自動表示される。押すとリクエストを送った CC のターミナルにフォーカスを移し、CC のプロンプトにフォールバック:

```
PermissionRequest (macOS, Mini 3x2):
[header][header][Go CC▲]   ← 右上にトップオーバーレイ（ヘッダは2ボタン分に縮小）
[Deny▼][Alwys▼][Allow▼]   ← 全選択肢が維持

Fallback (macOS):
[msg  ][msg  ][Go CC▲]
[     ][     ][OK▼  ]

Notification (macOS):
[     ][     ][Go CC▲]   ← 黒背景に上部オーバーレイ
[ message text    OK▼]

AskUserQuestion (macOS, 1ページ目):
[Opt A][Opt B][Go CC▲]   ← 右上: Go CC（トップオーバーレイ）
[Opt C][Opt D][Submit▼]  ← 右下: Submit/Next

AskUserQuestion (2ページ目以降):
[Opt X][Opt Y][Back▲ ]   ← 右上: Back
[     ][     ][Next▼ ]

非macOS:
[Opt A][Opt B][Cancel▲]  ← 右上: Cancel
[Opt C][Opt D][Submit▼]
```

- フロー: Go CC 押下 → daemon が `status="open"` で応答 → hook が `cc-streamdeck-focus <pid>` を呼ぶ → ターミナルにフォーカス → hook exit 0 → CC がフォールバック
- Notification は fire-and-forget なので daemon が直接 `focus_pid()` を呼ぶ
- cc-streamdeck-focus はプロセスツリーから3層でフォーカス: (1) tmux ペイン選択 (2) TTYベースのタブ選択 (3) アプリアクティベート
- 非 macOS: 全ビューで Go CC なし（右上もヘッダの一部）

### 表示ガード時間

表示切替直後のボタン押下を防止するガード時間。表示が変わった瞬間に誤って Allow を押す事故を防ぐ。アイテム種別ごとに独立設定:

| 種別 | 設定キー | デフォルト | 対象 |
|------|----------|-----------|------|
| PermissionRequest / AskUserQuestion | `display.guard_ms` | 500ms | 誤 Allow 防止 |
| Fallback / Notification | `display.minor_guard_ms` | 0ms | 即座に操作可能 |

- ガード期間中はボタン押下を無視
- `display.guard_dim = true` を設定すると、PermissionRequest のガード期間中に選択肢ラベルの文字色を暗くして視覚的に操作不可を示す。ガード終了後に `_guard_timer` で再レンダリングして通常色に復帰（デフォルトOFF）
- `_guard_for_item(item)`: アイテム種別に応じて適切なガード秒数を返す

### フォントフォールバック

テキスト量に応じてフォントサイズを自動選択:
1. **20px** M PLUS 1 Code (アンチエイリアス) — 短いテキスト
2. **16px** M PLUS 1 Code (アンチエイリアス) — 中程度のテキスト
3. **10px** PixelMplus10 (ドット・バイ・ドット) — 長いテキスト
4. 10pxでも溢れる場合は末尾を `...` で省略

ヘッダ行（ツール名）は本文10pxフォールバック時のみ20pxを維持。本文16pxのときはヘッダも16px。

### リスク色（ヘッダ背景+文字色）

ツール名ヘッダの背景色と文字色でリスクレベルを4段階で表現:

| Level | 背景色 | 文字色 | 対象 |
|-------|--------|--------|------|
| critical | `#800000` | `#FFFFFF` | `rm -rf`, `sudo`, `git push --force`, `curl\|bash` |
| high | `#604000` | `#FFD080` | `rm`, `git push`, `curl`, `pip install`, `mv`, `chmod` |
| medium | `#203050` | `#80C0FF` | Write, Edit, WebFetch, 未知コマンド, MCP tools |
| low | `#101010` | `#808080` | `ls`, `cat`, `git status`, `npm test` |

Bash: 正規表現パターンマッチ（critical→low→high→medium の優先順）。Write/Edit: file_pathパターンで引き上げ可能。

### インスタンス識別色（ボディ背景）

ボディ背景色でClaudeインスタンスを区別。固定パレットから出現順で割当、常に表示:
1. `#0A0A20` 暗い紺 2. `#0A200A` 暗い緑 3. `#200A0A` 暗い赤茶 4. `#1A1A0A` 暗い黄土 5. `#150A20` 暗い紫 6. `#0A1A1A` 暗いティール 7. `#1A0A0A` 暗い赤 8. `#0A1020` 暗い鋼青 9. `#1A100A` 暗い茶 10. `#0A0A10` 暗い深夜

### 設定ファイル

`~/.config/cc-streamdeck/config.toml`（XDG準拠、全セクション省略可）。リスク色、インスタンスパレット、ツール別リスクレベル、Bashパターン追加、パス引き上げパターン、Notification表示種別、表示ガード時間をカスタマイズ可能。ユーザーパターンはbuilt-inに加算。デーモン起動時に1回読み込み。

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

### Hook連携: Stop

Claude Code の **Stop** hook を使用。Claude のレスポンスが終わりユーザー入力待ちになるたびに発火する。

1. **Done 通知表示**: `"stop"` が `notification_types` に含まれていれば、Notification と同形式で "Done" メッセージを `_add_item()` で投入。同一PIDの既存アイテムは supersede で自動除去
2. **Done 通知 disabled 時**: `_purge_connected_items()` で同一PIDの stale items を明示的にパージ

fire-and-forget（stdout 不要）。daemon 未起動時はスキップ（自動起動しない）。`_handle_stop_hook()` で処理。

### AskUserQuestion（インタラクティブUI）

AskUserQuestion は Stream Deck 上で選択肢を直接表示し、ボタン押下で回答する（注: PermissionRequest 発火はバグ #15400、将来修正される可能性あり）。hook は `updatedInput.answers` で回答を返す。

```
単一質問 (3選択肢, Mini):   複数質問 (2ページ目):     確認ページ:
[Opt A ] [Opt B ] [Cancel▲]  [Opt X ] [Opt Y ] [Back▲ ]  [     ] [     ] [Back▲ ]
[Opt C ] [     ] [Submit▼]  [     ] [     ] [Next▼ ]  [     ] [     ] [Submit▼]
                  ↑question                   ↑question
```

- 選択肢ボタン: 左上から順に1ボタン1選択肢（右列のコントロールキーを除く）、最大 `ボタン数 - 2` 個。ラベル（太字）+ description（小文字グレー）を表示。選択済みは強調色
- コントロールボタン: 右列に配置（上: ナビゲーション、下: アクション）
  - Cancel/Back/Go CC = 右上（トップオーバーレイ）。本体領域に `page_info`（header + ページ番号、例: "Language\n1/3"）を `#808080` で描画。単一質問時はページ番号なし
  - Submit/Next = 右下（下部オーバーレイ）。本体領域に `page_description`（question テキスト）を `#606060` で描画
  - macOS では1ページ目の Cancel が Go CC に置換
- multiSelect: トグル式（各ボタン ON/OFF 切替、複数選択して Submit）
- 複数質問: 1ページ目右上=Cancel/Go CC、2ページ目以降右上=Back。最終質問後に確認ページ（Back + Submit のみ）
- 空きボタン押下は全ページで無視。背景色はインスタンス識別色
- `_AskQuestionState`: ページ番号・回答管理。`_handle_ask_key()` でボタン操作、`_render_ask_page()` で再レンダリング
- `render_ask_question_page()`: 選択肢は全面ボタン、操作ボタンは本体+オーバーレイラベル

### フォールバック表示

ExitPlanMode など、hook の `allow`/`deny` では適切にハンドリングできないツールは、Stream Deck に「See Claude Code」メッセージと OK ボタンを表示し、任意のボタン押下で dismiss。daemon は `status="fallback"` を返し、hook は exit 0（出力なし）でターミナルプロンプトにフォールバックする。背景色はインスタンス識別色。`render_fallback_message()` でレンダリング。

### 統一キュー表示管理 (daemon.py)

全ての表示物（PermissionRequest, AskUserQuestion, Fallback, Notification）を `_items: list[_DisplayItem]` で統一管理。表示すべきアイテムは `_select_and_display()` が `(priority DESC, timestamp DESC)` で決定する。

| 優先度 | 種別 | item_type | 応答 |
|--------|------|-----------|------|
| HIGH (3) | PermissionRequest, AskUserQuestion | "permission", "ask" | ブロッキング応答 |
| MEDIUM (2) | Fallback (ExitPlanMode) | "fallback" | fallback 返却 |
| LOW (1) | Notification | "notification" | 不要（fire-and-forget） |

**`_DisplayItem`**: 全種別を統一するデータクラス。priority, timestamp, client_pid, item_type, request, done_event, response, always_active, ask_state 等を保持。

**主要メソッド:**
- `_add_item(item)`: リストに追加。同一PIDの既存アイテムを supersede（上書き）する。**例外**: permission は同一PIDの他の permission を supersede しない（並行サブエージェント対応）。`_select_and_display()` を呼ぶ
- `_remove_item(item)`: リストから除去。`_select_and_display()` を呼ぶ
- `_purge_connected_items(pid)`: 指定PIDの connected items（permission, ask, fallback）を全除去。`done_event` をエラーで起こし、接続スレッドに通知。Stop hook で Done 通知が disabled の場合に使用
- `_select_and_display()`: `max(self._items, key=lambda i: (i.priority, i.timestamp))` で最優先アイテムを選択し、表示が変わった場合のみ `_render_item()` を呼ぶ。ガード時間がある場合は `guard_active=True` でレンダリングし、タイマーで期限後に `guard_active=False` で再レンダリング
- `_wait_for_resolution(item, conn)`: 接続ハンドラスレッドが per-item `done_event` を1秒ポーリングで待機。client 切断検出時は `_remove_item()` で除去
- `_render_item(item, guard_active)`: item_type に応じて適切な renderer を呼ぶ。permission 時は `guard_active` を `render_permission_request()` に渡す
- `_guard_for_item(item)`: アイテム種別に応じて `_display_guard_sec`（permission/ask）または `_minor_guard_sec`（fallback/notification）を返す

**動作:**
- **同一PID supersede**: 同一PIDからの新しい hook は既存アイテムを supersede する（最新のもののみ有効）。**例外**: permission 同士は supersede せず蓄積する（WebSearch 等のサブエージェントが並行して hook を投げるため）。各アイテムは独立した `done_event` を持ち、ボタン押下・切断で個別に解決される
- **stale items cleanup**: CC はターミナル側で応答済みの hook プロセスを kill しない（バグ #15433）。notification, fallback, Stop hook の Done 通知が `_add_item()` で追加されると、同一PIDの全アイテム（permission 含む）が自動的に supersede される。Stop hook で Done 通知が disabled の場合は `_purge_connected_items()` で明示的にパージ
- **プリエンプション**: 新しいアイテムが来ると `_select_and_display()` が最優先・最新を選択。古いアイテムはリストに残り、新しいものが解決されると自動的に再表示される
- **Notification**: LOW として `_items` に投入。HIGH/MEDIUM 表示中は選択されないだけ。解決後に自然に表示される
- **`_items_lock`**: 短命ロック（リスト操作と `_current_item` 更新のみ保護）。長期保持なし

### Notification 表示

Claude Code の Notification hook で受信した通知を最下段に表示。上段は黒（デバイスのベゼルと一体化）:

```
Mini 3x2:
[     ] [     ] [     ]     ← 黒
[ message text         OK ] ← 最下段（インスタンス背景色、暗い文字色、右下にOK）
```

- `render_notification()`: 最下段を横長キャンバスとしてレンダリング。フォントは16px→10pxの自動選択
- `_handle_notification()`: `_DisplayItem(priority=LOW)` として `_add_item()` に投入。同一PIDの既存アイテムは `_add_item()` の supersede で自動除去
- 設定ファイル `[notification] types = [...]` で表示する notification_type を選択可能（disabled な type は無視される）
- OK ボタン押下で dismiss（`_remove_item()` → 次のアイテムへ）

### PPIDベースの同一インスタンス識別

hook が `os.getppid()` を `client_pid` として daemon に送信。同一 PID = 同一 Claude Code インスタンス。

**同一PID supersede と並行サブエージェント**: `_add_item()` は同一PIDの既存アイテムを supersede する。ただし permission は他の permission を supersede しない（並行サブエージェント対応）。CC はターミナル側で応答済みの hook プロセスを kill しない（既知のバグ: GitHub #15433）ため、stale な items が残留するが、notification, fallback, Stop hook Done 通知の追加時に自動的に supersede される。

### Daemon自動起動とデバイス状態管理

Hook Client はソケット接続失敗時にDaemonを自動起動（lazy init）。Daemon パスは `sys.executable` の親ディレクトリから解決（.venv/bin/ が PATH に無くても動作）。Daemonは `DeviceState.status` で `"ready"` / `"no_device"` を管理し、3秒間隔のポーリングでUSBホットプラグに対応。24時間デバイス未接続で自動終了。

### デバイスライフサイクル

- 起動時: 全キー黒画面に初期化（Elgatoロゴを消す）
- リクエスト表示中: メッセージ+選択肢ラベル表示
- 応答後: 黒画面に戻る
- Daemon終了時: `reset()` でElgatoロゴに復帰

### スレッドモデル (daemon.py)

- メインスレッド: Unix socketサーバ（accept loop、デバイスポーリングスレッド終了も監視）
- 接続ハンドラスレッド: `_add_item()` → `_wait_for_resolution()` → 応答送信。per-item `done_event` を待つだけでロック長期保持なし。複数スレッドが同時に待機可能
- デバイスポーリングスレッド: DeviceStateのデーモンスレッド（24時間未接続でself._running=False）
- StreamDeckライブラリ内部スレッド: `_key_callback` → アイテム解決 → `_remove_item()` → `_select_and_display()`

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
uv run cc-streamdeck-daemon          # Daemon手動起動
uv run cc-streamdeck-daemon --stop   # Daemon停止
uv run cc-streamdeck-focus <pid>     # ターミナルフォーカス（手動テスト用）
```

## Tech Stack

- Python 3.11+ (uv, pyproject.toml, hatchling)
- streamdeck (python-elgato-streamdeck, Stream Deck制御)
- Pillow (ボタン画像生成、TrueTypeアンチエイリアス描画)
- M PLUS 1 Code (20/16px, AA描画、日本語等幅コーディングフォント)
- PixelMplus10 (10px, dot-by-dot描画)
- pytest, ruff

## Design Principles

- **統一キューによる表示管理**: PermissionRequest、AskUserQuestion、フォールバック、Notification など全ての表示物は同一のリスト（`_items`）に投入する。表示すべきアイテムは常に `(priority DESC, timestamp DESC)` で決定する（優先度が最も高いものの中で、最も新しいもの）。アイテムの追加・解決・除去のたびに `_select_and_display()` を呼び、この単一ロジックで次の表示を再計算する。優先度ごとの分岐、同一PID上書き、プリエンプション、復帰などは全てこのルールの自然な帰結として実現する。種別ごとのアドホックな退避・復元機構を作らないこと
- Stream Deck Mini 6ボタンを1つの連結ディスプレイとして扱う
- メッセージ表示領域を最大化、選択肢はボタン下部20pxの色付きラベルのみ
- フォント幅計算は `getlength()` (advance width) を使用。`getbbox()` はピクセル範囲で等幅が崩れる
- テキスト描画は descent 分上にシフトして行間をピチピチに詰める
- Alwaysは安全のためトグル式（誤タップ防止）。非アクティブ時はグレー文字
- Hook Client は高速起動・応答が必須（Claude Code がブロックされるため）
- あらゆるエラーで exit 0（出力なし）→ 端末フォールバック（ユーザーがスタックしない）
- 同一PIDからの新しい hook は `_add_item()` で古いアイテムを supersede。ただし permission 同士は蓄積（並行サブエージェント対応）
- リスク評価: low は確実にread-onlyな操作のみ。誤判定の余地があるものはmedium以上。未知コマンドはmedium
- 設定ファイルのユーザーパターンはbuilt-inに加算（上書きではない、安全性のため）
- AskUserQuestion は Stream Deck 上で直接回答（`updatedInput.answers` で返却）。ExitPlanMode はフォールバック表示でターミナルに誘導
- Notification は fire-and-forget（応答不要）。LOW アイテムとして統一キューに投入し、最下段のみ控えめに表示
- 意味のある色（リスクヘッダ、選択肢ラベル等）以外の背景色はインスタンス識別色を使用
- レイアウトは `deck.key_layout()` から動的計算。Mini以外のモデルでもコード変更なしで動作する設計

# リサーチエージェント サイクル17 レポート (2026-09-19)

## 今回やったこと(概要)

前回セッション(2026-09-17〜18)が`SEARCH_PAPERS`→`EXTRACT_PAPERS`→
`READ_PAPERS`を実行し、新規論文3件(`cuni2026-pocket-offline-simulst`、
`simulstream2025-eval-toolkit`、`lacuna2026-beam-search-cascade-flicker`)
を追加・要約した状態で`GENERATE_HYPOTHESES`まで進んでいました。今回の
セッションはそこから再開し、`GENERATE_HYPOTHESES`→`HUMAN_APPROVAL`→
`RUN_EXPERIMENTS`→`ANALYZE_RESULTS`→本レポート執筆まで一気に進めました。

1. **GENERATE_HYPOTHESES**: 新規仮説`h-hard-prefix-lock-continuation`を
   1件追加。前回`READ_PAPERS`で「`lacuna2026-beam-search-cascade-flicker`
   の角度(MTビームサーチの状態再利用によるフリッカー削減)は既存の
   仮説・実験でまだカバーされていない」とフラグが立っていたことを受けて。
2. **HUMAN_APPROVAL**: `check-budget 0.05` → `AUTO_APPROVE`(`uses_existing_
   clips=true`)で自動承認。
3. **RUN_EXPERIMENTS**: 環境確認の上、新規スクリプト`prefix_lock_replay.py`
   を実装・実行(実費約$0.05)。
4. **ANALYZE_RESULTS**: 交絡チェック後、結果を分析。
5. 本レポート執筆(`WRITE_REPORT`)。

## 新規仮説: `h-hard-prefix-lock-continuation`(完了)

**背景**: `lacuna2026-beam-search-cascade-flicker`(Interspeech 2024)は、
カスケード型のリアルタイム音声翻訳においてMTのビームサーチ状態を
ASRの逐次更新をまたいで再利用することで、文字単位のフリッカー率を
20%以上削減できると報告しています。本リポジトリの翻訳器はAPI経由
(Gemini/OpenAIへのプロンプト呼び出し)であり、ビームサーチの内部
状態には直接アクセスできません。既存の`h-continuation-context-anchor`
は、この考え方をプロンプトで近似する試み(前回の翻訳結果を`<prior_
translation>`として助言的に添えるだけ)でしたが、1回限りのライブ
実行で結果が不安定(交絡ありと判定済み)でした。

今回の仮説は、それより構造的に強い近似を試すものです: **継続バッチ
では、確定済みの翻訳テキストを機械的に(モデルを介さず)そのまま
文字列として固定し、モデルには新しく聞こえた部分(差分)だけを翻訳
させて連結する**、というアプローチです。これは「確定部分は絶対に
書き換わらない」ことを構造的に保証する点で、ビームサーチの状態再利用が
実際に保証していることに近く、`h-continuation-context-anchor`の
「お願いベース」の助言よりも強い制約です。

**実装**:
- `llm_translator.py`に`delta_only`という新しいオプション引数を追加
  (`translate_stream`/`_build_user_prompt`)。`prior_translation`と
  併用すると、`<target>`は「発話全体」ではなく「新しく聞こえた差分
  部分のみ」として扱われ、モデルには`<prior_translation>`を繰り返さ
  ないよう明示的に指示します。デフォルトは`False`(既存の挙動に影響
  なし)。
- 新規スクリプト`src/real_time_translation/experiments/prefix_lock_
  replay.py`: 既に録音済みの実験JSON(`20260915_h_continuation_anchor_
  test_rpmunlimited.json`、`anchor_continuation_translation=true`で
  録音されたもの)から、複数バッチにまたがる発話スパンを15件抽出し、
  3条件(`baseline_replay`=累積テキストを毎回ゼロから再翻訳、今日の
  デフォルト挙動 / `soft_anchor_replay`=既存の`h-continuation-context-
  anchor`の助言的アンカー / `hard_lock`=今回新規の機械的プレフィックス
  固定)×各2回リプレイしました。ライブのDeepgramは一切使わず、Gemini
  呼び出しのみです。

**途中で見つけたデータ品質上の発見**: `TimedEvent.original_text`
フィールド(各翻訳バッチの原文英語を記録するフィールド)が、複数
バッチにまたがる発話スパン内で常に累積的とは限らないことが分かり
ました。全47件の実験JSONを対象に、「直前バッチが発話終了フラグ
`False`」となる連続バッチの組(1591組)を調べたところ、後のバッチの
`original_text`が前のバッチの`original_text`を文字列プレフィックス
として含む(=累積的)ケースが1213組(76%)、まったく重なりが無い
(=そのバッチ自身の新規差分のみ)ケースが378組(24%)と、**フィー
ルドの意味が一貫していませんでした**。根本原因(おそらく非同期の
`utterance_id`管理まわり)は今回は完全には特定していませんが、
`PLAYBOOK.md`が繰り返し警告している「イベントフィールドの意味を
決めつけずに実際の構築箇所を確認する」というルールに関する新しい
ニアミス事例として`reflections.md`に記録しました。回避策として、
連続するバッチのペアごとにプレフィックス関係を実行時に判定し、どちら
の形式でも正しく差分・累積テキストを再構築するロジックを実装しました。

**交絡チェック**: 3条件はすべて同一プロセス内・同一`LLMTranslator`
インスタンス・同一モデル(gemini-3-flash-preview)で実行され、
`context_lines=[]`/`update_context=False`を全呼び出しで固定。プロンプト
戦略以外の変数は完全に一致しており、ライブ実行時に問題となった
rpm制限のようなconfig差分の交絡リスクはありません。

**結果**(15スパン、各条件2回リピート):

| 条件 | 平均クロスバッチNE | NE=0だったスパン数 | 平均忠実度スコア |
|---|---|---|---|
| baseline_replay(現状) | 0.597 | 1/15 | 99.3 |
| soft_anchor_replay(既存アンカー) | 0.212 | 7/15 | 98.7 |
| hard_lock(今回新規) | 0.0 | 15/15 | 90.7(最低50) |

- **hard_lockのNE=0は構造的に自明**(固定した部分は物理的に書き
  換わりようがないため)であり、それ自体は品質の証拠にはなりません。
  実際、忠実度審査員のスコアはhard_lockのみ明確に低下しており
  (90.7、7/15スパンで100点未満)、最悪ケース(スパン3、原文
  "Until at the very end, the hope is that all of the | essential
  meaning of the passage" — ちょうど節の途中でバッチ境界が来る例)
  ではスコアが50まで落ちました。手動で確認したところ、固定された
  プレフィックスの続きを自然に完結させる代わりに、モデルが「〜という
  期待を持っており」のように内容を冗長に繰り返す、一貫性のない訳文を
  生成していました。これは本仮説の`predicted_effect`で懸念していた
  リスクがそのまま顕在化した具体例です。
- **より有益な発見は`soft_anchor_replay`(既存の`h-continuation-
  context-anchor`の仕組み)についてです。** 1回限りのノイズの多い
  ライブ実行という交絡を取り除き、決定的なリプレイを複数回繰り返す
  この手法で検証したところ、平均クロスバッチNEはベースラインの
  0.597から0.212へと大きく(約65%)改善し、15スパン中12スパンで
  ベースライン以下のNEを両リピートとも達成、忠実度の低下もごくわずか
  (99.3→98.7)でした。これは`h-continuation-context-anchor`自身の
  元々のライブ単発実行の結果(NEが0.836→0.972と悪化、ノイズの可能性
  ありとフラグ済み)とは**正反対の結論**です。

**結論**: `status`は`tested`としました。`hard_lock`は現状の実装のまま
では推奨しません — NE=0という数字自体が構造的に自明であり、実際には
節の途中でバッチ境界が来るケースで一貫性・忠実度が明確に悪化する
リスクがあるためです。一方で`soft_anchor_replay`、つまり既存の
`anchor_continuation_translation`フラグについては、今回の管理された
リプレイ実験の結果は明確にポジティブであり、`h-continuation-context-
anchor`自身のライブ単発実験の結論を上書きする、より信頼できる証拠だと
考えます。ただし今回のリプレイは1本のクリップ由来の15スパンのみが対象
であるため、他の複数バッチ豊富な実験JSON(例:`llm_course_ep8_guest_
talk_10min_rpm60.json`、83スパンあり)でも同様の結果が再現するかを
確認するフォローアップを次サイクル以降の候補として残します。

なお本実験はリプレイ/分析系のスクリプトであり(`compression_actions_
replay.py`等の既存の前例と同様)、`experiments/results.csv`への行追加は
行っていません。

## 環境状況

`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`はいずれも設定済みで、Gemini REST API
は到達可能(200)でした。`ffmpeg`は今回のセッションでは未インストール
でしたが、今回の仮説はライブのDeepgram/ffmpegを一切必要としないリプレイ
設計だったため影響ありませんでした。Deepgram Listen WebSocketの生死は
今回も確認していません(この仮説には不要だったため)— サイクル5以降
続いているプロキシのWebSocketアップグレード問題が解消したかどうかは
依然として不明です。

## バックログの状態(queued/proposedは0件、上限6件以内)

今回追加した`h-hard-prefix-lock-continuation`は同一サイクル内で
`tested`まで完了したため、現在queued/proposedの仮説はありません。

## 人間(rm-2278)へのお願い

1. **判断不要、共有のみ**: `h-continuation-context-anchor`
   (`anchor_continuation_translation`フラグ)について、今回の管理された
   リプレイ実験は元のライブ単発実験と正反対の(ポジティブな)結果を
   示しました。ライブ環境での再検証や本番投入判断の前に、まず他の
   クリップでも同じリプレイ手法で再現するかを確認したいと考えています。
2. **新しいバグ報告**: `TimedEvent.original_text`フィールドがバッチ間で
   常に累積的とは限らないという、既存イベントログの仕様上の不整合を
   発見しました(本文参照)。将来この生ログフィールドに依存する分析を
   書く際は注意が必要です。
3. **環境ブロッカー(継続)**: Deepgram Listen WebSocketの状況は今回も
   未確認です。解消していれば`h-masking-holdback`等のライブパイプライン
   実験を優先的に再開したいところです。

## 次のサイクルでやること

- `soft_anchor_replay`の良好な結果が他のクリップでも再現するかを、
  同じリプレイ手法で追試する新規仮説を検討する。
- バックログが空(0件、上限6件)のため、`GENERATE_HYPOTHESES`で新規
  仮説を追加する余地は大きい。今回未着手の`simulstream2025-eval-
  toolkit`(評価ツール比較)や、他のREAD済み論文からもまだ拾える角度
  がないか確認する。
- Deepgram WebSocketが復旧していれば、ライブパイプラインでの本格的な
  検証(masking-holdback系)を最優先候補にする。

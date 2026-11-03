# サイクル81 結果レポート(2026-11-03, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`READ_PAPERS`状態でした(サイクル81は
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS`まで既に別セッションで
進んでいた状態)。今回は`READ_PAPERS → GENERATE_HYPOTHESES →
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで
実行しました。

## READ_PAPERS: 2件の論文を要約

`papers.json`で`status: "extracted"`だった2件に`summary`と
`key_findings`を記入し、`status: "read"`にしました。

1. `papi2026-hearing-to-translate-cascade-vs-speechllm`
   (arXiv:2512.16378, TACL 2026) -- SpeechLLM(音声をネイティブに扱うLLM)
   と従来のカスケード型(ASR→LLM翻訳)を16ベンチマーク・13言語対・9種の
   困難条件で比較。**結論: カスケード型が全体で最も安定しており、
   SpeechLLMは一部の条件でしか追いつかない。** これは本リポジトリの
   既存アーキテクチャ(Deepgram ASR + 別立てのLLM翻訳段)を変更する
   動機にはならず、むしろ裏付けとなる結果です。
2. `zhu2026-cyclicjudge-variance-decomposition`(arXiv:2603.01865) --
   LLM判定者(judge)のばらつきには、ランダムなノイズとは別に、
   **判定者固有の系統的なバイアス項**があり、その大きさは測りたい
   モデル差と同程度になりうる、という指摘。既存の判定者ノイズ分析
   (サイクル19-21の`judge()`ノイズフロア調査、
   `h-judge-repeat-count-diminishing-returns`等)はすべて**同じ単一の
   判定モデルを繰り返し呼ぶ**設計なので、ランダムノイズは測れても
   系統的バイアス項は原理的に検出できません。この論文の提案手法
   (複数の異なる判定モデルをラウンドロビンで回す)は、
   `experiments/results.csv`とここ数サイクルのgit logを確認した限り
   このリポジトリでまだ試されていない、本当に新しい切り口です。
   ただし実行には2つ以上の到達可能な判定モデルAPIが必要で、現状
   Geminiしか安定して到達できていない点(下記参照)が制約になります。

いずれも`papers.json`の`key_findings`に根拠と制約を明記済みです。

## GENERATE_HYPOTHESES: 新規仮説なし(バックログが上限に到達済み)

バックログを確認すると、`queued`3件・`proposed`3件(いずれも
`approval: needs_human`)の計6件で、PLAYBOOKの上限(6件)にちょうど
到達していました。`proposed`3件はすでに`pending_approval.json`に
記録済みで、自己承認ブロック(サイクル36で確定)によりエスカレーション
済みであることを確認したため、今回は新規仮説を追加せず、既存項目の
処理を優先しました。

## HUMAN_APPROVAL: 処理対象なし

`approval`が未設定、または値が`"proposed"`の仮説は存在しませんでした
(`proposed`な`status`の3件はいずれも`approval: needs_human`として
すでに処理済み)。`queued`な3件(いずれも`approval: approved_by_human`)
が残っていたため、PLAYBOOKのルールに従い`RUN_EXPERIMENTS`へ進みました。

## RUN_EXPERIMENTS: 既存の3件の`queued`仮説すべてが依然ブロック中

環境チェックを再実行しました:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`: いずれも環境に設定済み。
  `ffmpeg`も利用可能。
- Deepgram REST(`/v1/projects`): **200**(到達可能)。
- Gemini REST(`/v1beta/models`): **200**(到達可能)。
- OpenAI REST(`/v1/models`): **CONNECT拒否(403)**(`curl`終了コード
  56、プロキシのステータスで`connect_rejected`)。サイクル72/78/80と
  完全に一致する再現です。
- Deepgram **listen-websocket**ハンドシェイク: システムのpython3.11に
  `websockets==17.4`をインストールして実際のWebSocketアップグレードを
  試したところ、**HTTP 400 "Connection header did not include
  'upgrade'"**で失敗。これもサイクル5以降繰り返し確認されている既知の
  ブロックと完全に一致します。

既存の3件の`queued`仮説はいずれも今回も実行不可でした(各
`blocked_reason`にサイクル81分の再確認を追記済み):

1. `h-judge-cross-model-agreement-check` -- OpenAI接続拒否のため継続
   ブロック。
2. `h-review-short-acronym-keyterm-false-positive` -- Deepgram
   listen-websocket失敗のため継続ブロック。
3. `h-continuous-rating-cps-calibration` -- rm-2278さんの視聴時間待ち、
   変更なし(環境の問題ではありません)。

PLAYBOOKの指示(環境が実験を実行できない場合は結果を捏造せず、ギャップを
記録して報告に進む)に従い、新規の実験は一件も実行せず、`ANALYZE_RESULTS`
(分析対象なし)を経て本レポートの作成に進みました。

## 環境面のメモ(継続事項、変化なし)

サイクル80から変化なく、以下の2つの構造的制約が今回も再確認されました:

- OpenAI APIへの接続が明示的にブロックされている(ポリシーによるCONNECT
  拒否)。
- Deepgramのストリーミング(WebSocket)接続のアップグレードハンドシェイク
  がプロキシによって壊れている(RESTは問題ない)。

この2つの制約がある限り、`h-judge-cross-model-agreement-check`と
`h-review-short-acronym-keyterm-false-positive`はこのクラウド実行環境
では原理的に実行不可能です。`h-judge-cross-gemini-model-agreement-check`
(Geminiのみで完結する設計、`pending_approval.json`参照)が承認されれば、
この制約を回避して次サイクルで実行できる見込みです。

## HUMAN_APPROVAL待ちの項目(rm-2278さんの確認が必要、変更なし)

`pending_approval.json`に3件あります(いずれも前サイクルから継続):

1. `h-judge-repeat-independent-batches-corrected-design`
   (2026-10-20付、$0.35)
2. `h-soft-anchor-gate-exempt-utterance-end`(2026-10-28付、$0.35)
3. `h-judge-cross-gemini-model-agreement-check`(2026-11-01付、$0.05)
   -- OpenAI/Deepgram WebSocketの制約とは無関係に実行できる可能性が
   高い項目です。

いずれも`check-budget`は`AUTO_APPROVE`、`uses_existing_clips=true`で、
通常のPLAYBOOKルールなら自動承認される内容です。rm-2278さんが
`hypotheses.json`を直接編集するか、何らかの形で承認の意思を示して
いただければ、次サイクルで`RUN_EXPERIMENTS`に進めます。

## 次のステップ

`REFLECT`に進み、今回の振り返り(2論文の読み込みと新規仮説を見送った
判断、3件の`queued`仮説すべてが3サイクル連続でブロックされている状況、
`zhu2026-cyclicjudge`論文が示す「次に埋まる予定のバックログ枠」の候補)
を`reflections.md`に記録したうえで、次サイクルの方針を決定します。

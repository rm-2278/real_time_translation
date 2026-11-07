# サイクル85 結果レポート(2026-11-07, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル84の
`REFLECT`が前回のセッションで完了済み)。今回は
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES →
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで
実行しました。

## SEARCH_PAPERS: 新規1件を発見(judge言語化確信度スレッドの2本目の裏付け論文)

サイクル84の振り返りで提案されていた「judgeの言語化確信度/較正スレッドを
裏付ける2本目の独立した論文を探す」という次サイクルの方向性に従い、
2回のWebSearchを実行しました。

1. judge言語化確信度/較正文献の追加探索 -- 新規候補1件を発見:
   `marashian2026-verbalized-vs-internal-confidence-mt`
   (`arxiv.org/pdf/2606.17234`, Marashian・Palmer・von der Wense, 2026)。
   既存の`papers.json`の全URLとarXiv IDを突き合わせ、重複でないことを
   確認しました。
2. SimulST(同時音声翻訳)のストリーミング方針に関する一般的な新着探索
   -- 既存登録済みの論文(DOA/papi2026、HPO/cmu-nvidia2026、
   SimulU/simulu2026、CUHKSZ-IWSLT、Pinch-AST-IWSLT)以外に、
   SimulMEGA(MoE router方式, arXiv 2509.01200)とStreamUni
   (streaming chain-of-thought, arXiv 2507.07803)を発見しましたが、
   いずれもファインチューニング/自前モデルホスティングが前提の手法
   であり、本リポジトリのAPIオンリー構成では直接適用不可能なため
   (PLAYBOOK記載のexposst2026/sasst2026と同種の不適用パターン)、
   あえて`papers.json`には追加しませんでした。

一般的な較正系の論文(Calibration as a First-Class Criterion、
Auditing Cross-Domain Recalibration、ConfProBench)も見つかりましたが、
翻訳/judge特有の具体的なメカニズムを持たない、または対象が異なる
(マルチモーダルprocess judge)ため、深さ優先のPLAYBOOK方針に従い
追加しませんでした。

## EXTRACT_PAPERS → READ_PAPERS: marashian2026論文を`status: "read"`に

`arxiv.org`への`WebFetch`は今回も`EGRESS_BLOCKED`(DNS解決失敗)でした。
これまでの全サイクルと同じ現象です。PLAYBOOK記載のフォールバック通り
WebSearchで代替し、具体的な数値を含む内容を取得できました。

- 5種類の言語化確信度プロンプト手法を考案し、モデルの内部確信度信号
  (出力確率・エントロピー)と比較。
- エラー検出: F1スコアは0.02〜0.23の範囲で、チャンスレベルより高いが
  概して低い。
- 較正: 言語化確信度はモデル出力確率とほぼ同等だが、エントロピーには
  劣る。
- **数値スケール(0-100)よりLikert/順序尺度の方が較正性能が高い** --
  本リポジトリのjudgeに確信度フィールドを追加する場合に直接使える
  設計上の知見です。
- 内部信号と言語化信号の間には**ほとんど相関がない** -- 両者は
  互換可能な代理指標ではなく、異なる情報を持つ。

`experiments/results.csv`(`confidence`関連の行はASRのDeepgram確信度の
み、judgeの自己確信度ではない)、`experiments/*.json`の judge関連ファイル
名、`hypotheses.json`の全`h-judge-*`/`h-*confidence*`項目、`git log`を
突き合わせ、本リポジトリの`translation_fidelity_judge.judge()`に
「項目ごとの言語化確信度」を追加するという角度は、既存のどの仮説・実験
にも含まれていないことを再確認しました。

## GENERATE_HYPOTHESES: 新規仮説なし(バックログが依然上限)

`queued`3件・`proposed`3件(いずれも`approval`設定済み: 3件
`approved_by_human`、3件`needs_human`で`pending_approval.json`に記録済み)
の計6件で、PLAYBOOKの上限(6件)に到達したままのため、新規仮説は
追加しませんでした(サイクル79-85と同じ状況)。次回バックログに空きが
出た際の最有力候補として、`marashian2026-verbalized-vs-internal-confidence-mt`
と`wmt2026-verbalized-confidence-judge`を根拠とする
「judge()にLikert尺度の言語化確信度フィールドを追加する」仮説を
`papers.json`に記録しました。

## HUMAN_APPROVAL: 処理対象なし

6件すべてに`approval`が既に設定済みで、未処理の項目はありませんでした。
`approved_by_human`の`queued`3件が残っていたため、PLAYBOOKのルールに
従い`RUN_EXPERIMENTS`へ進みました。

## RUN_EXPERIMENTS: 既存の3件の`queued`仮説すべてが依然ブロック中

環境チェックを再実行しました:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`: いずれも環境に設定済み。
  `ffmpeg`も利用可能。キャッシュ済みクリップも存在。
- Deepgram REST(`/v1/projects`): **200**(到達可能)。
- Gemini REST(`/v1beta/models`): **200**(到達可能)。
- OpenAI REST(`/v1/models`): **CONNECT拒否(403)**(`curl`終了コード56、
  プロキシのステータスで`connect_rejected`)。サイクル72以降と完全に
  一致する再現(今回で7回連続)です。
- Deepgram **listen-websocket**ハンドシェイク: `python3.13`で新規に
  `.venv`を作成し(`uv pip install -e ".[experiments]"`)、実際の
  WebSocketアップグレードを試したところ、**HTTP 400 "Connection header
  did not include 'upgrade'"**で失敗。サイクル5以降繰り返し確認されて
  いる既知のブロックと完全に一致します。

既存の3件の`queued`仮説はいずれも今回も実行不可でした
(`result_summary`/`blocked_reason`にサイクル85分の再確認を追記済み):

1. `h-judge-cross-model-agreement-check` -- OpenAI接続拒否のため継続
   ブロック(7回連続同一結果)。
2. `h-review-short-acronym-keyterm-false-positive` -- Deepgram
   listen-websocket失敗のため継続ブロック。
3. `h-continuous-rating-cps-calibration` -- 環境の問題ではなく、
   rm-2278さん自身の視聴時間(20-30分程度)が必要な人間参加型の仮説
   のため、今回も新しい確認は行いませんでした(状況は前サイクルから
   変化なし)。

PLAYBOOKの指示(環境が実験を実行できない場合は結果を捏造せず、ギャップを
記録して報告に進む)に従い、新規の実験は一件も実行せず、`ANALYZE_RESULTS`
(分析対象なし)を経て本レポートの作成に進みました。コストログには
$0(到達性チェックのみ、新規API呼び出しなし)を記録しました。

## 環境面のメモ(継続事項、変化なし)

サイクル84から変化なく、以下の2つの構造的制約が今回も再確認されました:

- OpenAI APIへの接続が明示的にブロックされている(ポリシーによるCONNECT
  拒否)。
- Deepgramのストリーミング(WebSocket)接続のアップグレードハンドシェイク
  がプロキシによって壊れている(RESTは問題ない)。

この2つの制約はサイクル72・78・80・81・82・83・84・85の8回連続で
同一の再現を示しており、十分に安定した既知の制約として扱ってよい段階に
来ています。

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

`REFLECT`に進み、今回の振り返り(judge言語化確信度スレッドに2本目の
裏付け論文を発見し`read`まで進めたこと、Likert尺度>数値尺度という
具体的な設計知見が得られたこと、OpenAI/Deepgram WebSocketブロックが
8回連続で安定していること、バックログが依然上限で埋まっていること)を
`reflections.md`に記録したうえで、次サイクルの方針を決定します。

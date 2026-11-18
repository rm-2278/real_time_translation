# サイクル93 結果レポート (2026-11-18)

## 概要: 新規文献3件を読了、バックログは上限のため新仮説なし、キュー中の1件を実際に実行(ジェミニ間クロスモデル検証)

本サイクルはスケジュール実行(cron)によるもので、前回セッション
(2026-11-16、サイクル92)が `REFLECT -> SEARCH_PAPERS` まで進めた状態から
再開しました。本セッションでは
`SEARCH_PAPERS -> EXTRACT_PAPERS -> READ_PAPERS`(前回スレッドの続き、
本セッション開始時点で既に完了していた)から
`GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS`
まで一通り進め、本レポート(`WRITE_REPORT`)を作成しています。

## SEARCH_PAPERS / EXTRACT_PAPERS / READ_PAPERS: 新規文献3件を読了

本セッション開始前に完了していた文献調査で、以下の3件を読了済み:

- `easist2025-unidirectional-simulst`: 音声エンコーダとLLMデコーダの両方を
  完全単方向化するSimulSTアーキテクチャ(EASiST)。本リポジトリはAPI利用
  のみで独自モデル学習ができないため、直接の実装対象にはならず、
  読み/書きポリシー設計の比較参考として記録。
- `caal2609-causality-aware-s2st-policy`: LLMベースの同時音声→音声翻訳
  向けに、遅延を「回避可能(avoidable)」と「回避不可能(unavoidable)」に
  分解して測る CAAL (Causality-Aware Average Lagging) 指標を提案。本
  リポジトリの `mt_latency_decomposition.py`(queue_wait vs
  stream_duration)や `simul_latency_metrics.py` の既存計算と関連が深く、
  **$0のretroactive分析仮説の有力候補**として記録(次回GENERATE_HYPOTHESES
  で検討)。
- `mocha2601-streaming-asr-decoder-llm-latency`: 中国語(AISHELL)向けの
  ASR専用(翻訳なし)レイテンシ最適化手法。本リポジトリはDeepgramの
  ホスト型ASRをブラックボックスとして使うのみで、直接のアクション
  対象にはならず、背景知識として記録のみ。

## GENERATE_HYPOTHESES: 新規仮説は追加せず(バックログが既に6/6上限)

カウントした結果、`hypotheses.json` は
`queued` 5件 + `proposed` 1件 = 6件で、PLAYBOOK.mdの上限(約6件)に
達していました(前サイクルからの差分: `h-judge-cross-gemini-model-agreement-check`
と `h-soft-anchor-gate-exempt-utterance-end` が、サイクル92〜93の間に
rm-2278さんご本人により `approved_by_human` / `queued` へ承認済みでした
-- コミット `c26e12d`)。6件は全て `approval` が設定済みで、未処理の
HUMAN_APPROVAL作業ではないことを確認しました。`caal2609` の
CAAL分解アイデアは、バックログに空きができた次回に備えて記録を
継続しています。

## HUMAN_APPROVAL: 新規の承認/エスカレーション作業なし

`queued` 5件は全て `approval=approved_by_human` 済み、`proposed` 1件
(`h-judge-repeat-independent-batches-corrected-design`)は既に
`needs_human` としてエスカレーション済み(`pending_approval.json` に
記録済み、自己承認ブロックの前例に従い再試行せず)。少なくとも1件が
`approval != needs_human` の `queued` 状態だったため、`RUN_EXPERIMENTS`
へ進みました。

## RUN_EXPERIMENTS: `h-judge-cross-gemini-model-agreement-check` を実際に実行

環境チェック(新しいコンテナでの実行、`.venv` を `python3.13 -m venv` +
`uv pip install -e ".[experiments]"` で再構築):
`DEEPGRAM_API_KEY`・`GOOGLE_API_KEY`・`OPENAI_API_KEY` は全て設定済み、
`ffmpeg` も利用可能でした。本サイクルは本セッションの実行環境が
`api.deepgram.com` へのREST到達を明示的に許可・認証情報注入する設定に
なっていたため(過去のサイクルから変化している可能性のある実環境
シグナル)、reduced-cadence規定による再確認スキップではなく、ネット
ワーク到達性を実際にライブ再確認しました:

- `api.deepgram.com` REST: **200**(到達可能)
- `generativelanguage.googleapis.com`(Gemini)REST: **200**(到達可能)
- `api.openai.com` REST: **CONNECT拒否(403)**— サイクル72以降と同一
  シグネチャ、変化なし
- Deepgramの `listen` WebSocketアップグレード: **HTTP 400 "Connection
  header did not include upgrade"**— REST自体は許可されているにも
  関わらず、WebSocketアップグレードのハンドシェイクのみ依然プロキシに
  よって阻害されており、サイクル5以降と同一のシグネチャでした。

この結果、キュー中5件のうち:

- `h-judge-cross-model-agreement-check`(OpenAI/gpt-4o-mini必要): **依然
  ブロック**(ホストレベルの拒否)
- `h-review-short-acronym-keyterm-false-positive`(Deepgramライブ
  WebSocket必要): **依然ブロック**(アップグレード阻害)
- `h-continuous-rating-cps-calibration`(rm-2278さんの視聴時間+元動画
  ファイルが必要): **依然ブロック**(`video/` ディレクトリ・該当ファイル
  は本コンテナにも存在せず、`find`/`ls` で再確認)
- **`h-judge-cross-gemini-model-agreement-check`(Gemini系の別モデルのみ
  必要): 実行可能と判断し、実際に実行しました。**
- `h-soft-anchor-gate-exempt-utterance-end`(Gemini系のみ必要、$0.35):
  時間の都合上、本サイクルでは着手せず次回以降に持ち越しました。

### 実行した実験: `judge_cross_gemini_model_replay.py`

新規スクリプト `src/real_time_translation/experiments/judge_cross_gemini_model_replay.py`
を実装し、既存の3つの実験JSON
(`20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json`、
`20260920_h_soft_anchor_disfluency_gate_replay.json`、
`20260918_h_backlog_compression_control_rpm9.json`)から27組の
(英語ソース、日本語翻訳)ペアを抽出し、本番ジャッジモデル
(`gemini-3.1-flash-lite`、キャッシュ済みスコアを再利用)と、別の
Geminiモデル(`gemini-3.6-flash`、到達可能性を実際に確認済み)の
両方で再採点しました。新規API消費は代替モデルの27回の呼び出し + 元の
JSONにスコアが無かった1組向けの本番モデルの追加呼び出し1回のみで、
Deepgramの新規呼び出しはゼロ、新規翻訳生成もゼロです。実測コストは
$0.05(`log-cost` で記録)。

## ANALYZE_RESULTS: 「散らばり」は正常範囲内だが、モデル間に系統的な採点差あり

- Pearson相関 r=0.61、Spearman相関 r=0.41(Spearmanの方が弱いのは、
  代替モデルのスコアが上限(100点)付近に強く偏っている影響 -- 27組中
  21組が `gemini-3.6-flash` では満点100点、本番の `gemini-3.1-flash-lite`
  では27組中13組が満点)。
- 平均の符号付き差分(代替−本番)= **+6.0点**、標準偏差9.91(n=27での
  標準誤差~1.91、差分/標準誤差~3.1)— これは単純なばらつきでは
  説明できない、**統計的に有意な系統的バイアス**です。`gemini-3.6-flash`
  は本番ジャッジより一貫して甘い点数を付ける傾向があります。
- 一方、平均絶対差分(6.74点)は、本番ジャッジ自身の同一ペア再試行時の
  ノイズの床(`within_group_noise_stdev`=8.39、293グループから算出)
  **より小さい**ため、「ペアごとの採点の散らばり」という観点では、
  クロスモデルの不一致は同一モデルの再試行ノイズの範囲内に収まって
  おり、アンサンブルが単一モデルのノイズを超えて追加的な不一致を
  検出できている、とは言えません。
- 仮説の `predicted_effect` が要求していたCohen's kappa(三分位バンド化)
  も試みましたが、このサンプルが上限付近に強く偏っているため三分位の
  カットオフが崩壊し(kappa=0.0という結果自体が手法上のアーティファクト
  で、意味のある不一致の指標にならない)、誤解を招く数値として報告せず
  棄却しました。
- **結論**: 単一ジャッジモデル設計が、自身の再試行ノイズを超える
  「隠れた不一致」を見逃している、という証拠は今回は得られませんでした
  (散らばりはノイズの床の範囲内)。しかし、2つのGeminiモデル間には
  無視できない**系統的なキャリブレーション差**(+6点、代替モデルが
  甘め)が確認されました。これは `arxiv2606-19544` が指摘する
  「ジャッジのスコアはモデル間で普遍的な尺度ではない」という警告と
  一致します。将来クロスモデル/アンサンブル型ジャッジ(OpenAIとの比較
  が実行可能になった場合を含む)を構築する際は、各モデル固有の
  オフセットを補正せずに生スコアを単純平均してはいけない、という
  実務上の示唆が得られました。同一ベンダー内の比較である点の留保は
  仮説自体の `predicted_effect` にも明記した通りで、依然ブロック中の
  OpenAI比較の代替にはなりません。本チェックは代替モデル側を1回しか
  呼んでいない単発設計であり、十分な検定力を持つ研究ではなく、簡易な
  $0.05の代理チェックです。

`hypotheses.json` の該当エントリは `status: tested` に更新済みです。

## 人間への確認依頼

以下は引き続き rm-2278 さんの判断/作業待ちです:

1. `pending_approval.json` の1件
   (`h-judge-repeat-independent-batches-corrected-design`)— 自己承認が
   実行環境のポリシーでブロックされているため、承認するかどうかの
   判断をお願いします。
2. `h-continuous-rating-cps-calibration` — 20-30分ほどの視聴・評価時間と、
   元動画ファイル(`video/LLM2025...mp4`、またはすでにリポジトリに
   存在する別クリップへの再対応)をご用意いただけると実行できます。
3. (新規)上記のクロスGeminiモデル検証結果について、もし
   `h-soft-anchor-gate-exempt-utterance-end`(Gemini系のみで実行可能、
   $0.35、未着手)を優先的に次サイクルで実行してよいか、もしくは別の
   優先順位があればお知らせください。

## 次サイクルへの申し送り

- バックログは5/6(本サイクルで1件 `tested` になり1枠空いた)。次回
  GENERATE_HYPOTHESESでは `caal2609` のCAAL分解アイデア($0の
  retroactive分析候補)を検討する想定。
- `h-soft-anchor-gate-exempt-utterance-end`(Gemini系のみ必要、$0.35)が
  次に優先的に実行可能なキュー中仮説。
- OpenAI CONNECT拒否とDeepgram WebSocketアップグレード阻害は、
  本サイクルで実際にライブ再確認済み(reduced-cadenceのキャッシュ
  再利用ではなく)。どちらも変化なし。次回はreduced-cadence規定に
  戻って構わない(直近のライブ確認はサイクル93)。

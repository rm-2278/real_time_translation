# サイクル97 結果レポート (2026-11-22)

## 概要: 新規文献4件は仮説化せず、`h-soft-anchor-gate-exempt-utterance-end` を実行。NE改善は確認したが、ゲートが実質的に無効化される副作用を発見し、採用は見送り

本サイクルはスケジュール実行(cron)によるもので、`current_state: GENERATE_HYPOTHESES`
(前セッションが `SEARCH_PAPERS -> EXTRACT_PAPERS -> READ_PAPERS` まで進めていた直後)
から再開しました。本セッションでは
`GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS -> WRITE_REPORT`
まで進めています。

## GENERATE_HYPOTHESES: 新規文献4件、いずれも新規仮説化せず

前セッションで読み込んだ4件の新規論文(`reina2026-entropy-information-loss-simulst`,
`streamuni2025-unified-speechllm-streaming`, `deepl-slator-caption-churn-2026`,
`smallest-ai-streaming-asr-comparison-2026`)を精査しましたが、いずれも
新規仮説には至りませんでした:

- **streamuni2025**: 唯一の「本リポジトリで再現可能なアイデア」として挙がっていた
  「翻訳呼び出し自体にCoT形式で完了性判定を組み込む」という手法は、
  既に `h-combined-translate-completeness-signal`(tested)で全く同じ仕組みを
  実装・検証済み(既存の分離classifier呼び出しとの一致率100%、n=31)でした。
- **reina2026**: 本リポジトリのLLMクライアントはchat-completions形式のAPIで
  token log-probsにアクセスできないため、この論文の中核手法(log-prob由来の
  read/writeポリシー)自体は再現不可能。
- **deepl-slator-caption-churn-2026** / **smallest-ai-streaming-asr-comparison-2026**:
  いずれもベンダー公表・自己申告のマーケティング系ベンチマークで、
  方向性の裏付けにはなるが引用可能な定量評価としては使えません。

4件とも `papers.json` にこの判断根拠を明記しました。バックログは
`queued` 4件 + `proposed` 1件(`needs_human`、`pending_approval.json`記載済み)の
計5件で、上限(~6件)の範囲内のまま変化なしです。

## HUMAN_APPROVAL: 新規承認作業なし

`queued` の4件はいずれも既に `approval: approved_by_human` 済みでした。
`proposed` の1件(`h-judge-repeat-independent-batches-corrected-design`)は
既に `needs_human` として `pending_approval.json` に記載済み・エスカレーション
完了済み(自己承認ブロックのため、再度の書き込みは試みていません)。
そのまま `RUN_EXPERIMENTS` へ進みました。

## RUN_EXPERIMENTS: `h-soft-anchor-gate-exempt-utterance-end` を実行

本セッションの実行環境では `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`
全て設定済み、`ffmpeg` も利用可能でしたが、改めて再確認した結果:

- Deepgram REST (`/v1/projects`): **200 OK**
- Gemini REST: **200 OK**
- OpenAI REST: **403(接続拒否、egressプロキシによる拒否 — 前サイクルと同一の
  パターンが継続)**
- Deepgram **listen-websocket**: **HTTP 400「Connection header did not
  include 'upgrade'」**(こちらもcycle 5以来、継続して同一の症状)

この結果、`OPENAI_API_KEY`が必要な`h-judge-cross-model-agreement-check`と、
実際のDeepgram ASRが必要な`h-review-short-acronym-keyterm-false-positive`は
依然ブロックされたままです。`h-continuous-rating-cps-calibration`は
rm-2278さんの視聴時間待ちのまま変化なし。

残る`h-soft-anchor-gate-exempt-utterance-end`(Gemini-onlyのreplay、
Deepgram不要)を実行しました。新規スクリプト
`soft_anchor_gate_exempt_utterance_end_replay.py`を作成し、
4条件(`baseline_replay`, `soft_anchor_replay`, 既存の`gated_soft_anchor`、
新規の`gated_soft_anchor_exempt_end`)で既存の2クリップに対してreplayを実行。

**環境ドリップの発見**: 旧スクリプト群が使っていたモデル名
`gemini-3-flash-preview`が、このセッションのgoogle-genai SDKでは
404(「API version v1では見つからない」)になることを発見しました。
`ListModels`には今も表示され、v1beta REST直叩きでは到達可能なため、
キー・到達性の問題ではなく、SDKのデフォルトAPIバージョン絡みの
モデル退役ドリップと判断しています。4条件すべてに同一のフォールバック
(`config.gemini_model` = `gemini-3.1-flash-lite`、judge()が既に使っている
モデルと同じ)を適用することで実行内の比較は妥当なままにしていますが、
この仮説系列の過去の絶対NE数値とは直接比較できない点を明記しています。

## ANALYZE_RESULTS: 予測(a)は確認、だが重大な副作用を発見

- **予測(a)(formerly-gatedなis_utterance_endバッチのNEがbaseline近くまで
  回復する)は強く確認されました**: 該当transitionのNEが
  `gated_soft_anchor`の0.586/0.559(クリップ1/2)から
  0.140/0.039まで低下し、`soft_anchor_replay`のbaseline(0.130/0.023)に
  近づきました。span全体の集計NEも同様に回復(0.235→0.097、
  0.311→0.061、baselineは0.080/0.039)。
- **ただし大きな副作用**: 旧ゲートが捉えていたバッチの98.6%が
  `is_utterance_end`バッチだったため(h-gate-utterance-end-temporal-blindness
  で既知)、これを除外すると新ゲートはほぼ発火しなくなります
  (eligible batchのうちgateされたのは0/20、1/125のみ)。これは
  「的を絞った例外化」ではなく「ゲートを実質的に無効化する」に
  近い結果です。
- **この仮説自身の記述にあった要検証事項を直接確認**: ゲーティング
  系列全体の発端である、guest-talkクリップのspan 69(delta_text
  「AIM to」から「AIME」を幻覚的に補完した事例)について、仮説の
  説明文では「このバッチはおそらく `is_utterance_end=False`
  (未検証、確認が必要)」としていましたが、実際のバッチ記録を
  確認した結果 **`is_utterance_end=True`** でした。つまり本仮説の
  除外ルールは、まさにこの旗艦的な失敗事例への保護を外してしまう
  ことになります。
- 今回のモデルフォールバック(`gemini-3.1-flash-lite`)下では、
  この「AIME」幻覚が`baseline_replay`・`soft_anchor_replay`・
  `gated_soft_anchor_exempt_end`で軒並み再現し、`gated_soft_anchor`さえ
  同じ幻覚フレーズを生成していました(judge()が両repeatとも100点を
  付けていたのはサイクル19で既知のjudge単発呼び出しノイズと判断 —
  表面テキストは他条件の低評価repeatと実質同じ)。
- **予測(b)(対応span単位でのjudgeスコア低下なし)は検出力不足で
  判定不能**: 新ゲートが発火したspanは両クリップ合計で1件のみ
  (guest-talk span 82、無関係な短い断片「Jim, like」、
  `is_utterance_end=False`)で、差分は-5.0(vs soft_anchor_replay)/
  -10.0(vs gated_soft_anchor)、この実行自身のノイズフロア
  (stdev約14.3、n=328)の範囲内 — 結論を出せる材料ではありません。
  全span集計の`mean_fidelity_all_repeats`は3条件間で約0.3点差に
  収まっており、広範な品質低下の兆候はありません。
- **結論**: このまま採用はしません。NEの回復自体は本物ですが、
  その回復はゲートが自身の旗艦的失敗事例への保護を失うことで
  達成されており、意図していた「的を絞った例外化」とは逆の結果です。
  今回のモデルフォールバック由来の交絡もあり、信号の解釈はさらに
  慎重であるべきです。実験コスト $0.35(API呼び出し2728件、
  うちLLM翻訳1944件・judge784件、Deepgram新規呼び出しなし)。
  詳細は`hypotheses.json`の本エントリの`result_summary`、
  `experiments/results.csv`の同日行、および
  `experiments/20261122_h_soft_anchor_gate_exempt_utterance_end_replay.json`
  を参照してください。

## 人間への確認依頼

以下は継続中(新規なし):

1. `pending_approval.json` の1件(`h-judge-repeat-independent-batches-
   corrected-design`)— 自己承認が実行環境のポリシーでブロックされている
   ため、承認するかどうかの判断をお願いします。
2. `h-continuous-rating-cps-calibration` — 視聴・評価時間(20-30分)と
   元動画ファイル(`video/LLM2025...mp4`)をご用意いただけると実行できます。

## 次サイクルへの申し送り

- `h-soft-anchor-gate-exempt-utterance-end`は不採用でクローズしましたが、
  このゲート系列自体への今後の方向性として、「`is_utterance_end`なら
  安全にアンカーできる」という前提自体を再検証する価値があります
  (本サイクルの発見通り、`is_utterance_end=True`のバッチでも明確に
  不完全・断片的な場合がある)。$0の retroactive 候補として検討可能です。
- `gemini-3-flash-preview`が本環境で404になる件は、今後このモデル名を
  使う既存スクリプトを再実行する際には同様のフォールバックが必要になる
  可能性があります。次回同じ問題に当たったら、このレポートと
  `soft_anchor_gate_exempt_utterance_end_replay.py`のフォールバック実装を
  参考にしてください。
- バックログは `queued` 3件(`h-judge-cross-model-agreement-check`,
  `h-review-short-acronym-keyterm-false-positive`,
  `h-continuous-rating-cps-calibration`、いずれも環境/人間待ちで
  ブロック継続中) + `proposed` 1件 = 4件で、上限まで2枠の空きがあります。
  次サイクルは新規文献探索または新規仮説の追加を検討してよい状況です。

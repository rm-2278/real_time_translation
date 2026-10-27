# サイクル76 結果レポート(2026-10-27, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`HUMAN_APPROVAL`状態でした(サイクル76の
`GENERATE_HYPOTHESES`で`h-judge-holistic-vs-decomposed-repeat-noise`を
追加した直後)。今回は`HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS
→ WRITE_REPORT`まで、1サイクル分の残りを実行しました。

## HUMAN_APPROVAL: 自動承認

`h-judge-holistic-vs-decomposed-repeat-noise`(`estimated_cost_usd=0.02`、
`uses_existing_clips=true`)に対して`check-budget 0.02`を実行し、
`AUTO_APPROVE`判定を確認。PLAYBOOKの通常経路どおり
`approval: auto_approved` / `status: queued`を設定しました(この書き込み
自体は問題なく成功しており、`pending_approval.json`にある
`h-judge-repeat-independent-batches-corrected-design`のときのような
「自己承認ブロック」には今回は遭遇していません)。

この時点でのキュー済み仮説は4件:
`h-judge-cross-model-agreement-check`、
`h-review-short-acronym-keyterm-false-positive`、
`h-continuous-rating-cps-calibration`、
`h-judge-holistic-vs-decomposed-repeat-noise`。
`h-judge-repeat-independent-batches-corrected-design`は引き続き
`needs_human`のまま`pending_approval.json`に残っており(2026-10-20付、
人間の承認待ち、サイクル36/69で確認済みの自己承認ブロックの前例に
倣って再試行はしていません)。

## RUN_EXPERIMENTS: 環境確認とh-judge-holistic-vs-decomposed-repeat-noise実行

環境確認(PLAYBOOK必須手順)の結果:

- `DEEPGRAM_API_KEY`・`GOOGLE_API_KEY`・`OPENAI_API_KEY`はすべて環境変数
  として設定済み(今回のセッションではこれまでのサイクルと異なり3つとも
  値がありました)。
- Deepgram REST (`/v1/projects`) は到達可能(200)。Gemini REST
  (`generativelanguage.googleapis.com`) も到達可能(200)。
  OpenAI (`api.openai.com`) はプロキシに`CONNECT`トンネル自体を403で
  拒否されており、サイクル72で確認済みのポリシー拒否と同一の症状でした
  (キーの有効性とは無関係のホスト単位のブロック)。
- Deepgram **listen WebSocket**のハンドシェイクは、サイクル5以来毎回
  確認している`HTTP 400 "Connection header did not include 'upgrade'"`
  と全く同じ失敗で、今回も再現しました(REST自体は通るのにWebSocket
  アップグレードだけがプロキシに弾かれる、既知の非対称ブロック)。

この結果、キュー済み4件のうち:

- `h-judge-cross-model-agreement-check`(OpenAI必須) → 環境的に実行不可、
  引き続きブロック中として記録。
- `h-review-short-acronym-keyterm-false-positive`(Deepgram WebSocket必須)
  → 同じく実行不可。
- `h-continuous-rating-cps-calibration`(API費用$0だが、rm-2278本人が
  動画を見ながらキー入力で評価する約20-30分の人間作業が必要)→
  無人スケジュールセッションでは実行できないため見送り。
- `h-judge-holistic-vs-decomposed-repeat-noise`(Gemini呼び出しのみ、
  Deepgram/OpenAI不要)→ **これを今回実行しました**。

新規スクリプト`src/real_time_translation/experiments/
judge_holistic_vs_decomposed_noise.py`を実装し、
`experiments/20260916_compression_actions_prompt.json`の
`compression_actions.segments`から8点をサンプリング
(index 5 = `h-compression-actions-prompt-instruction`で実際に内容が
欠落したと確認済みのセグメント、+ stride-3の0,3,6,9,12,15,18)。
各点について既存の`judge()`(holisticのみ)と`judge_decomposed()`
(4つの下位基準+holisticスコアを同時に要求)をそれぞれ3回ずつ呼び出し
(計48回のgemini-3.1-flash-lite呼び出し、Deepgram消費ゼロ、新規翻訳
生成ゼロ)、`log-cost 0.02`で記録しました。

## ANALYZE_RESULTS: 結論は「天井効果により判定不能」

`unlp2026-holistic-vs-decomposed-scale`(人間翻訳者8名の研究)は
「holisticな単一スコアの方が、分解評定より評定者間信頼性が高い」という、
直感に反する結果を報告しています。本仮説はそのLLM-judge版として、
「`judge_decomposed()`のように4つの下位基準と一緒にholisticスコアを
聞くと、そのholisticスコア自体が(`judge()`単体で聞く場合より)繰り返し
間でブレやすくなるか?」を検証しようとしました。

結果:

- `judge()`(holisticのみ)は、サンプリングした**8点すべて**で3回の
  繰り返しスコアが完全に同一(分散0)でした。
- `judge_decomposed()`は8点中6点は同様に分散0でしたが、2点
  (index 3: `[20, 30, 30]`、index 9: `[80, 100, 100]`)では実際にブレが
  見られ、プールしたノイズのstdevは4.56でした。

方向としては予測(decomposed側の方がノイズが大きい)と一致しますが、
holistic側が**全8点で文字通り分散ゼロ**という結果は、「本当にノイズが
ゼロ」なのではなく、サンプルした素材(このクリップのセグメント)の
ほとんどがjudgeにとって90-100点を機械的に付けられる「簡単すぎる」
内容だったことを意味していると判断しました。これはPLAYBOOK
ANALYZE_RESULTS節がサイクル53の知見として明記している「天井効果の
ある範囲でjudgeスコアを比較しても意味がない」という注意点と全く同じ
失敗モードです。したがって本仮説は**「判定不能(inconclusive)」**として
`status: tested`・`result_summary`に正直に記録し、「ホリスティックの方が
ノイズがない」という過大な結論は書いていません。

次に同じ問いを検証するなら、このクリップではなく、すでに
`h-soft-anchor-generalization-guest-talk`等で使われている
guest-talkクリップ(`experiments/20260915_llm_course_ep8_guest_talk_
10min_rpm60.json`。サイクル21の分散分解分析で、スパン間の翻訳難易度の
ばらつきがこのクリップより大きいことが既に確認済み)のような、
もっと評価の割れる素材からサンプリングすべき、という具体的な次の一手を
`result_summary`に残しました。

## 環境面のまとめ(人間向け)

- 今回のセッションでは`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/
  `OPENAI_API_KEY`すべてが設定されていましたが、実際に使えたのは
  Gemini系の呼び出しのみでした。Deepgram WebSocketアップグレードと
  OpenAIのCONNECT自体が、このサンドボックスのegressプロキシに
  引き続きブロックされています(サイクル5および72から症状不変)。
  この2つのブロックが解消されない限り、`h-review-short-acronym-
  keyterm-false-positive`と`h-judge-cross-model-agreement-check`は
  今後も実行できません。
- `h-continuous-rating-cps-calibration`はAPIコストはゼロですが、
  rm-2278本人による約20-30分の視聴・評価作業が必要なため、無人
  スケジュール実行では進められません。

## 次のステップ

`REFLECT`に進み、今回の振り返りを`reflections.md`に記録したうえで、
次サイクルの方針(新規文献探索か、既存バックログの処理継続か)を
決定します。

# サイクル70 結果レポート(2026-10-22, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`GENERATE_HYPOTHESES`状態でした
(前回セッションが`REFLECT → GENERATE_HYPOTHESES`のサイクル69/70境界
遷移をコミット済み)。今回は`GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで実行しました。

### GENERATE_HYPOTHESES: ネットワーク非依存の新規仮説を1件追加

サイクル69の`reflections.md`が「サイクル70でも何も実行できなければ、
ネットワーク到達性に依存しない新しい$0・retroactive分析系の仮説を
無理にでも1件追加することを検討すべき」と明記していたため、今回は
まずその方針に従い新規仮説を1件追加しました
(`h-soft-anchor-repeat-variance-decomposition`)。

内容は、既存の2つの仮説の`result_summary`内で「言及はされたが
正式に分析されなかった」観察の定式化です:
- `h-hard-prefix-lock-continuation`は「baseline_replayの方が
  soft_anchor_replay/hard_lockよりも繰り返し間(repeat-to-repeat)の
  NEのばらつきが大きい」ことを傍注的に記録。
- `h-soft-anchor-generalization-guest-talk`も別のクリップで
  独立に同様の傾向(baselineはNE=0ヒットが9/166回、soft_anchorは
  123/166回)を再観測しましたが、やはり主目的(平均NEの汎化確認)の
  傍らでの言及にとどまっていました。

両方とも実験済みJSON(`experiments/20260919_h_hard_prefix_lock_replay.json`,
`experiments/20260919_h_soft_anchor_generalization_guest_talk.json`)
には各スパン・各条件につきREPEATS=2回分の生NE値(`condition_ne`)が
すでに記録されていたため、新規のDeepgram/Gemini/OpenAI呼び出しは
一切不要、既存ファイルに対する純粋な算術のみで検証可能と判断し、
`estimated_cost_usd: 0.0`で追加しました。バックログは5/6(上限6に対して
余裕あり)。

### HUMAN_APPROVAL: 新規仮説を自動承認

`check-budget(0.0)`は`AUTO_APPROVE`、`uses_existing_clips: true`だった
ため、PLAYBOOK.md通り`approval: auto_approved`, `status: queued`に設定
しました(この書き込み自体は問題なく通りました — サイクル36で観測
された「自己承認ブロック」は、今回のような正真正銘$0のretroactive
分析仮説には適用されないようです)。`h-judge-repeat-independent-batches-
corrected-design`(`estimated_cost_usd: 0.35`)は前回から変化なく
`pending_approval.json`で人間の承認待ちのまま、手を付けていません。

### RUN_EXPERIMENTS: 新規仮説を実行、環境再確認も実施

今回は`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`いずれも
設定済み、`ffmpeg`も利用可能でした。ただし、実際にRUN_EXPERIMENTSで
選んだのは新規追加した`h-soft-anchor-repeat-variance-decomposition`
(費用$0、ライブAPI呼び出し一切不要)であり、キー設定状況は本仮説の
実行可否に影響しません。

新規スクリプト`soft_anchor_repeat_variance_decomposition.py`を実装し、
2つの既存リプレイJSONの`condition_ne`を読み込んで、cycle 21で確立済みの
「within-span分散をスパンごとに平均してから比較する(生の値を先に
プールしない)」という分解手法(`judge_noise_power_analysis.py`の
`_decompose_zero_effect_variance()`)を再利用して分析しました。

**結果は予想より複雑でした:**

- 元クリップ(15スパン)では、soft_anchor_replayのwithin-span分散
  (0.00054)はbaseline_replay(0.0216)よりも約40分の1と大幅に低く、
  既存の傍注的な観察を明確に裏付けました。
- ゲスト対談クリップ(83スパン)では、**平均**ではsoft_anchor_replay
  (0.1026)の方がbaseline_replay(0.0399)より高く、既存の傍注的な
  主張とは逆の結果になりました。ただし**中央値**で見るとsoft_anchor
  の方が依然として低く(0.0 vs 0.0023)、「典型的なスパンではanchoring
  の方が安定している」という以前の観察自体は中央値ベースでは正しい
  ままでした。平均が逆転していたのは、たった1つの極端な外れ値
  スパン(span 73)が原因でした。
- span 73の内容を直接確認したところ、soft_anchor_replayの2回の
  繰り返しのうち1回は正しい完全な訳文を出力したものの、もう1回は
  元文(IOベースの訓練手法とその欠点についての文)のほとんどを
  落とし、末尾の節(「非常にコストがかかるという点です」)だけを
  出力するという、これまで未発見だった断続的な破局的切り詰め
  (catastrophic truncation)の失敗モードでした。これは
  `h-soft-anchor-generalization-guest-talk`がすでに発見していた
  「あいまいな発話で幻覚的に内容を補完する」失敗モード(span 69)
  とは別種のものです。
- さらに重要なのは、この失敗が**既存の忠実度評価では検知不可能
  だった**ことです。`translation_fidelity_judge.judge()`は各条件・
  各スパンにつき`repeats[0]`のみで1回しか呼ばれないため(cycle 19で
  すでに確立済みの制約)、span 73では偶然`repeats[0]`が正しい訳文
  だったため、judgeスコアは100点・欠落なしと記録されており、すぐ隣に
  ある破局的な切り詰め(repeat 1)は一切記録に残っていませんでした。
- span間分散(between-span variance、スパンごとの平均NEのばらつき)
  は両クリップともanchoringで明確に減少しており(元クリップ:
  0.287→0.080、ゲスト対談: 0.221→0.069)、これは混在していない、
  より明確な安定化効果でした。

結論として、「anchoringはrun-to-runのばらつきも減らす」という2つの
既存仮説の傍注的な主張は、中央値ベース・span間分散ベースでは
支持されるものの、平均within-span分散ベースでは1件の実例外れ値に
よって反証されました。この外れ値自体が、既存の判定方式では見逃される
新しい具体的なリスク(断続的な切り詰め)を明らかにした点で、単なる
反証以上の価値がある発見です。`hypotheses.json`の`result_summary`に
フォローアップ案(repeats[0]以外も含めた文字数/重複チェックによる
安価な切り詰め検出器の追加、十分な件数が集まった場合のサーバー側
ガード導入の検討)を記録済みです。費用$0のため`log-cost`は不要、
`results.csv`への行追加も既存の分析スクリプト慣例に従い行っていません。

### 参考: ネットワーク到達性の再確認(今サイクルの仮説実行には無関係)

今回は鍵が3つとも設定されていたため、ついでに到達性を再確認しました:
Deepgram REST(`/v1/projects`)・Gemini REST はいずれも200で到達可能
でしたが、`api.openai.com`は引き続きエージェントプロキシ自体に
接続拒否(`connect_rejected`、CONNECTトンネル失敗/HTTP 403)されました。
さらに、Deepgramの実際の`listen` WebSocketアップグレード・ハンドシェイク
を直接テストしたところ、今回もHTTP 400 "Connection header did not
include 'upgrade'"で失敗しました。つまり、キーが設定されていても
ライブ音声実験に必要な経路(Deepgram WebSocket、OpenAI)は依然として
ブロックされたままで、`h-judge-cross-model-agreement-check`・
`h-review-short-acronym-keyterm-false-positive`は今回も実行できません
でした(`h-continuous-rating-cps-calibration`は人間の視聴時間が必要な
ため、そもそもネットワークとは無関係にブロックされたままです)。

## 人間への確認が必要な項目

- 前回サイクルからの引き継ぎ(変化なし): `h-judge-repeat-independent-
  batches-corrected-design`(`estimated_cost_usd: 0.35`)が
  `pending_approval.json`で承認待ちです。rm-2278様が`hypotheses.json`の
  該当エントリを直接編集して`approval: "approved_by_human"`,
  `status: "queued"`にしていただく必要があります。
- 前回サイクルからの引き継ぎ(変化なし): `api.openai.com`への接続は
  引き続きこのセッションの通信許可ポリシー自体でブロックされています。
  鍵の問題ではないため、ポリシー側でOpenAIのホストを許可しない限り
  `h-judge-cross-model-agreement-check`は進められません。
- 新規発見: soft_anchor_replay(`anchor_continuation_translation`機能)
  に、既存の忠実度評価では検知されない断続的な切り詰め失敗が
  1件確認されました(98スパン中1件、まだ母数が小さく発生率は
  不明)。この機能を実験的ステータスから昇格させる前に、一度
  目を通していただくことをお勧めします(詳細は上記および
  `hypotheses.json`の`h-soft-anchor-repeat-variance-decomposition`
  エントリを参照)。

## 次回への引き継ぎ

- バックログは4 `queued`(うち1件は今サイクルの新規)+ 1
  `needs_human` = 5件、上限6件に対してまだ余裕があります。
- `h-judge-repeat-independent-batches-corrected-design`が承認されれば、
  次回サイクルでそのまま`RUN_EXPERIMENTS`に進めます。
- `api.openai.com`が許可されない限り`h-judge-cross-model-agreement-check`
  は、Deepgram WebSocketアップグレードが直らない限り
  `h-review-short-acronym-keyterm-false-positive`も進められません。
  どちらも複数サイクルにわたって一貫してブロックされている、環境固有の
  制約です。
- 新しく見つかった切り詰め失敗モードについて、フォローアップ仮説
  (repeats[0]以外も含めた切り詰め検出器の実装、または十分な件数が
  集まるまでより多くの既存JSONに同じ分析を広げる)を次回以降の
  サイクルで検討する価値があります。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません。

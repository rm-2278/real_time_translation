# サイクル72 結果レポート(2026-10-24, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`READ_PAPERS`状態でした(前回セッ
ションが`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS`まで進めて中断)。
今回は`READ_PAPERS → GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで実行しました。

### READ_PAPERS: 4件のjudge信頼性論文を要約・既存バックログと照合

前回セッションがWebSearchフォールバック(arxiv.org/aclanthology.org
へのWebFetchがこのセッションでも依然EGRESS_BLOCKEDだったため)で
見つけた4件の論文(`babeljudge2026`、`llm-jury-eamt2026`、
`unlp2026-holistic-vs-decomposed-scale`、`sourcefree-mt-eval-2026`)に
summary・key_findingsを書き、`status: "read"`にしました。`results.csv`
の`judge`/`fidelity`/`decompos`/`ensemble`/`jury`関連行、既存
`hypotheses.json`、`git log --oneline -20 -- experiments/ src/`を実際
にgrepして照合した結果:

- `llm-jury-eamt2026`(複数judgeのロジスティック回帰アンサンブル)の
  アイデアは、既にキューにある`h-judge-cross-model-agreement-check`
  (gpt-4o-mini vs gemini-3.1-flash-lite)でカバーされる方向性だったため、
  重複仮説は追加しませんでした。
- `unlp2026-holistic-vs-decomposed-scale`は、人間評価者・LLM judgeの
  両方で「holisticな単一スコアの方が分解評価(fluency+adequacy別々)
  より評価者間信頼性が高い」と報告しており、これは本リポジトリが
  サイクル53-55で`h-judge-rubric-decomposition-schematic-adherence`
  系として進めた方向(holistic→分解スコアへの移行)と**正面から矛盾
  する**文献です。下記「人間への確認が必要な項目」に明記しました。
- `sourcefree-mt-eval-2026`は、実際に`translation_fidelity_judge.py`
  のコードを読んで確認した結果(抽象論ではなく実装を直接確認する
  PLAYBOOK.mdのルール通り)、本リポジトリの`judge(source, translation)`
  は既に英語ソースを直接条件としており(参照訳ではなく)、この論文が
  推奨する設計を既に満たしていることが分かりました。ギャップなし、
  新規仮説は不要です。

### GENERATE_HYPOTHESES: babeljudge2026由来の新規仮説を1件追加

`babeljudge2026`のposition/order-biasの手法(2つの候補訳の提示順序を
入れ替えて検証)はそのままでは移植できません(`judge()`は1回の呼び
出しで1つの(source, translation)ペアのみを採点するペアワイズではない
ため)。その代わり、より単純で直接検証可能な副問題として、「プロンプト
内でsourceブロックとtranslationブロックのどちらを先に書くか」を入れ
替えると採点が変わるか、という新規仮説`h-judge-block-order-position-
bias`を追加しました(`estimated_cost_usd: 0.05`、`uses_existing_clips:
true`)。バックログは3 `queued` + 2 `proposed` = 5/6(上限6に対して
まだ余裕あり)。

### HUMAN_APPROVAL: 新規仮説を自動承認

`check-budget(0.05)`は`AUTO_APPROVE`、`uses_existing_clips: true`だっ
たため、`approval: auto_approved`, `status: queued`に設定しました。
書き込みは問題なく通りました。`h-judge-repeat-independent-batches-
corrected-design`(`estimated_cost_usd: 0.35`)は前回から変化なく
`pending_approval.json`で人間の承認待ちのままです。

### RUN_EXPERIMENTS: 環境確認 → h-judge-block-order-position-biasを実行

環境確認の結果、`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`は
いずれも設定済み、`ffmpeg`も利用可能、Geminiへの到達性も200でした。
一方、**新たな環境固有の発見**として、`api.openai.com`へのCONNECTが
このセッションのegressプロキシで明示的に403(policy denial)で拒否
されていることを確認しました。これはこれまでPLAYBOOK.mdに記録されて
いたDeepgramの`listen`WebSocketアップグレード問題(ホスト自体は到達
可能だがWS upgradeだけ失敗)とは異なる、ホスト単位の完全な接続拒否
です。このため`h-judge-cross-model-agreement-check`(gpt-4o-mini必須)
は今サイクルも実行できず、`blocked_reason`として記録のみ行い、別の
キュー済み仮説に切り替えました。

代わりに、Geminiのみで完結する(Deepgram・OpenAIいずれも不要)新規
仮説`h-judge-block-order-position-bias`を実行しました。
`judge_block_order_replay.py`を新規実装(`judge_reasoning_order_replay.
py`の設計を再利用: 固定テキスト・K=2判定・stride=4サンプリング、
同じ34スパン×条件グループ)、`ruff check`は問題なく通過、136件の新規
gemini-3.1-flash-lite呼び出し(\$0.05)を実行しました。

**結果:**

- プロンプト内でsource/translationのブロック順序を入れ替えても、
  スコアの**系統的な方向性バイアスは検出されませんでした**:
  34ペアグループでの平均差(translation_first − source_first)は
  +1.25でしたが、その差自体の標準偏差が11.60(標準誤差約1.99、
  差/標準誤差≈0.63)で、ノイズの範囲内に収まっています。
- ただし、より小さな副次的な発見として、translation_firstの方が
  source_firstよりも1回あたりのスコアのばらつき(ノイズ)が中程度
  大きいことが分かりました: within_group_noise_stdev=5.39
  (translation_first) vs 3.64 (source_first)、約1.48倍(分散では
  約2.19倍)。これはサイクル65の`h-judge-reasoning-before-score-noise`
  (reasoning-firstプロンプトでスコア先頭から理由説明先頭に変更した
  ケース、2.4倍のノイズ増加)と同じ方向の結果で、「現在の本番プロン
  プトの正確な文言・順序から外れると、ノイズが減るどころか増える」
  という傾向が2件連続で確認されたことになります。
- 結論: judge()にBabelJudgeの意味での系統的な順序バイアス(判定の
  方向性が入れ替わる)は見つかりませんでした。ただし現在の
  source-first順序の方が低ノイズだったため、本番プロンプトの順序を
  変更する理由はありません(むしろ現状維持を支持する弱い根拠)。

## 人間への確認が必要な項目

- **新規・文献上の矛盾**: `unlp2026-holistic-vs-decomposed-scale`が、
  本リポジトリがサイクル53-55で進めた`h-judge-rubric-decomposition-
  schematic-adherence`系の方向(holistic単一スコア→分解スコアへの
  移行)と矛盾する結果(holisticの方が評価者間信頼性が高い)を報告
  しています。この系列の仮説は既に`tested`済みでceiling effectなどの
  理由で決定的な結論は出ていませんが、今後さらに分解方向を追求する
  価値があるかどうか、ご判断材料としてお知らせします。
- 前回サイクルからの引き継ぎ(変化なし): `h-judge-repeat-independent-
  batches-corrected-design`(`estimated_cost_usd: 0.35`)が
  `pending_approval.json`で承認待ちです。`hypotheses.json`の該当
  エントリを直接編集して`approval: "approved_by_human"`,
  `status: "queued"`にしていただく必要があります。
- **新規の環境制約**: `api.openai.com`がこのセッションのegressプロキシ
  で明示的にpolicy denial(403)されていることを確認しました
  (Deepgram WebSocketの問題とは別個)。`h-judge-cross-model-agreement-
  check`がこの環境で実行できない直接の理由です。OpenAI宛の接続を許可
  いただく必要があります。

## 次回への引き継ぎ

- バックログは3 `queued`(1件は今サイクル`tested`に移行済み)+ 1
  `proposed`/`needs_human` = 4/6、上限6に対してまだ余裕があります。
- `h-judge-cross-model-agreement-check`・`h-review-short-acronym-
  keyterm-false-positive`は依然として環境固有のネットワーク制約で
  ブロックされたままです(複数サイクル継続中、原因は別々: 前者は
  OpenAI接続拒否、後者はDeepgram WebSocketアップグレード失敗)。
- `judge_block_order_replay.py`は、今後judgeプロンプトの文言を変更
  する際の回帰チェックとして再利用できます(ソースJSONを変えるだけ)。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません。

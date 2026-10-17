# サイクル62 結果レポート(2026-10-17, スケジュール実行)

## 今回やったこと

サイクル61のリフレクションで挙がっていた「無音除外(`ca_al_char_s_gapexcl`)後も残る
45〜70秒の残差」を仮説化し、GENERATE_HYPOTHESES(前回サイクルで追加済み)→
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTSまで一通り実行しました。

### HUMAN_APPROVAL

パイプラインは今回起動時点で`HUMAN_APPROVAL`状態でした。未処理(`approval`が
`null`または`proposed`)の仮説は`h-mt-queue-wait-post-gap-residual`の1件のみで、
`check-budget 0.0` → `AUTO_APPROVE`、`uses_existing_clips=true`(既存2実験JSONの
再解析のみ、新規API呼び出しなし)だったため、通常の自動承認パスで
`approval: auto_approved`, `status: queued`に設定しました。`pending_approval.json`
は空で、人間の承認待ちだった項目もありませんでした。

### 新規仮説: h-mt-queue-wait-post-gap-residual

**仮説**: サイクル61が発見した「無音除外後もなお残る45〜70秒のAL残差」は、
長い無音の直後のMT(翻訳)キュー待ち時間(`queue_wait_s`、
`h-mt-queue-wait-decomposition`が導入した指標)が通常より大きいことで
説明できるのではないか、という仮説です。

**実装**: 新規モジュール`src/real_time_translation/experiments/post_gap_queue_wait.py`
を追加しました。`simul_latency_metrics.py`の`_asr_utterance_intervals()`/
`_excluded_gaps()`(既存コードは変更なし)で検出した「除外対象のギャップ」それぞれに
対して、`mt_latency_decomposition.py`の`batch_latencies()`が返す各バッチの中から
「ギャップ終了直後(`asr_start_time`がギャップの`gap_end`から1.0秒以内)」のバッチを
1つだけ「post-gap」としてタグ付けし、残り全部を「baseline」として比較する、という
ジョイン処理です。合成イベント(1個の50秒ギャップ+しきい値未満の2個のギャップ)を
使ったハンド計算のユニットテスト3件を追加し、タグ付けロジックが期待どおりの
baseline/post-gap分割になることを確認しています。全テスト(既存38件+新規3件=41件)
通過、`ruff check`もクリーンです。新規コンソールスクリプト
`real-time-translation-exp-post-gap-queue-wait`も登録しました。

**結果(2本の外れ値講義実験に対して実行、コーパス全体でもこの2本以外に
除外対象ギャップがないことを確認済み)**:

| 実験 | 解決できたギャップ数 | post-gap平均 queue_wait_s | baseline平均 queue_wait_s | 超過合計(post-gap − baseline平均) | `ca_al_char_s_gapexcl` | 説明できた割合 |
|---|---|---|---|---|---|---|
| `llm2025_ep8_zenhan_full_lecture` | 5/8 | 7.92秒 | 1.35秒 | 32.80秒 | 59.70秒 | 54.9% |
| `llm2025_ep8_zenhan_full_lecture_v2_dict` | 6/7 | 6.15秒 | 1.26秒 | 29.30秒 | 73.75秒 | 39.7% |

**結論(部分的に確認、`status: tested`)**: 無音直後のバッチはキュー待ち時間が
baselineの約4.5〜5.9倍に跳ね上がっており、方向性と桁数はGENERATE_HYPOTHESESの
段階で行った予備チェック(7.43〜8.47秒程度)とおおむね一致しました。しかし、
この効果だけで説明できるのは各実験の残差AL(`ca_al_char_s_gapexcl`)の
39.7%〜54.9%にとどまり、半分前後は依然として未説明のまま残っています。
つまり「長い無音の後に翻訳バックログが残っている」という見立ては正しい方向の
一因ではあるものの、サイクル61の問いに対する完全な答えにはなっていません。
新規のDeepgram/LLM API呼び出しはゼロ(既存JSONの再解析のみ)、本番パイプラインの
コードにも変更はありません。

## 人間への確認が必要な項目

今回新たに人間の判断が必要な項目はありません。`h-continuous-rating-cps-calibration`
は引き続き人間(rm-2278)による20〜30分程度の視聴・評価作業待ちです。

## 次回への引き継ぎ

- バックログは依然4件/上限6件です(今回の新規仮説1件が`tested`になったため)。
- 今回のサイクルでもDeepgram WebSocket/OpenAI REST到達性チェックは実施していません
  (選んだ仮説がライブAPIを必要としなかったため)。鍵自体は`DEEPGRAM_API_KEY`/
  `GOOGLE_API_KEY`とも設定済み・`ffmpeg`も利用可能であることは確認済みです。
  次回、ライブAPIが必要な`h-review-short-acronym-keyterm-false-positive`
  (Deepgram WebSocket要)や`h-judge-cross-model-agreement-check`(OpenAI REST要)
  に着手する際は、実際の到達性(REST/WebSocket双方)を再確認してください。
- 残った39.7〜54.9%の未説明の残差について: 今回の手法は「ギャップ直後の1バッチ」
  だけをpost-gapとしてタグ付けしており、その後続バッチへのバックログの伝播
  (settle timeの遅延が何バッチ分継続するか)は測定していません。次の仮説候補
  として、post-gapバッチから数バッチ分のqueue_wait_sの推移を追跡し、
  バックログが何バッチで解消されるかを直接見る、という方向性が考えられます。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません(自己改善の判断は`reflections.md`の
今回のエントリに記載)。

# サイクル63 結果レポート(2026-10-18, スケジュール実行)

## 今回やったこと

パイプラインは今回起動時点で`SEARCH_PAPERS`状態でした。サイクル62のリフレクションが
「文献探索が4日・5サイクル止まっている」と指摘していたため、今回は
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES → HUMAN_APPROVAL
→ RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで一通り実行しました。

### SEARCH_PAPERS / EXTRACT_PAPERS / READ_PAPERS

サーベイが指摘していたギャップ(最新のベンダーASRレイテンシ数値、LLMベースMTの新しい
プロンプティング技術)を中心にWebSearchを実施。ヒットした論文の大半
(AlignAtt4LLM、NeMo@IWSLT2026、MLLP-VRAIN、prefix-to-prefixデータ駆動論文など)は
既に`papers.json`に登録済みの重複でした。新規に2件を追加・抽出・要約:

- **AA-WER Streaming**(Artificial Analysis、2026年6月発表のベンダー中立ベンチマーク):
  Deepgramの具体的な数値は取得できず(`artificialanalysis.ai`へのWebFetchが
  DNS/EGRESS_BLOCKEDで失敗)、代わりに周辺情報としてDeepgram Nova-3のchangelog値
  (median WER 6.84%、レイテンシはNova-2と同程度)と競合ベンダー計測
  (Nova-3/Nova-2のTTFT ~992ms)を記録。既存の`vendor-benchmarks-2026-hamming-livelingo`
  エントリのP50 516msとは測定方法が違う数値であることを明記し、「ASRレイテンシは
  本パイプラインのボトルネックではない」という既存結論は変わらないと判断。
- **EAST(「LLMs Can Achieve High-quality Simultaneous Machine Translation as
  Efficiently as Offline」)**: デコーダのみLLMをストリーミング翻訳用に
  READ/WRITEマーカー付きデータでファインチューニングし、KVキャッシュの再計算
  コストを避ける手法。本リポジトリの`llm_translator.py`は既に固定長の
  スライディングコンテキストウィンドウ(`context_window_size`、デフォルト3行)と
  Geminiのcontext cachingで同種の非効率を回避済みであり、またホスト型APIを呼ぶ
  だけのこのリポジトリにファインチューニング手法は適用できないため、
  仮説化は見送り。

いずれも`experiments/results.csv`・既存論文エントリとの照合を行い、新規仮説の
トリガーにはならないと結論しました(詳細は`papers.json`の各エントリ参照)。

### GENERATE_HYPOTHESES: h-post-gap-queue-wait-persistence

サイクル62の`h-mt-queue-wait-post-gap-residual`(無音ギャップ直後の1バッチだけを
"post-gap"としてタグ付け)が、残差ALの39.7%/54.9%しか説明できなかったことを受け、
「バックログの影響はギャップ直後の1バッチだけでなく、後続の数バッチにも
及んでいるのではないか」という追加仮説を新規追加(リフレクション記載の次候補)。
$0・既存クリップのみ使用。バックログは3 queued + 1 proposed = 4件(上限6件以内)。

### HUMAN_APPROVAL

`check-budget 0.0` → `AUTO_APPROVE`(`uses_existing_clips=true`)。これは
`pending_approval.json`のエスカレーションを覆す「自己承認」ではなく、新規$0仮説の
通常の自動承認パスなので、`approval: auto_approved`, `status: queued`に設定しました。

### RUN_EXPERIMENTS: 到達性再チェック + 本実験

サイクル60〜62と3サイクル連続で見送られていたDeepgram WebSocket/OpenAI REST到達性
チェックを実施(サイクル62のリフレクションで「4サイクル目になったら必ず実施する」と
明記されていたため):

- Deepgram REST (`/v1/projects`): **200 OK**
- Gemini REST: **200 OK**
- OpenAI REST (`/v1/models`): **ブロック**(プロキシCONNECTレベルで403)
- Deepgram listen-websocket(実際のストリーミングエンドポイント): **ブロック**
  (HTTP 400 "Connection header did not include 'upgrade'" -- プロキシが
  WebSocketアップグレードを壊している。PLAYBOOK記載のサイクル5の既知の失敗モードと同じ)

この結果を踏まえ、`h-judge-cross-model-agreement-check`(OpenAI REST必須)と
`h-review-short-acronym-keyterm-false-positive`(Deepgram WebSocket必須)の両方に
`blocked_reason`フィールドを追記し、今サイクルもライブAPI実験は実行不可であることを
明示しました。`h-continuous-rating-cps-calibration`は元々APIではなく人間の視聴時間
(20〜30分)待ちなので、この到達性チェックとは無関係に変わらず保留です。

続けて、$0のh-post-gap-queue-wait-persistenceを実装・実行しました。
`post_gap_queue_wait.py`に`_tag_offset_batches()`(ギャップ直後のバッチからオフセット
0, 1, 2, ...と連続してタグ付けし、次のギャップの開始バッチに重ならないよう切り詰める)
と`analyze_persistence()`を追加。合成イベント(2つの連続したギャップ+減衰する
queue_wait_sパターン)によるユニットテスト3件を追加し、切り詰めロジックと
「baseline以下に戻ったら積算を止める」ロジックの両方を検証(全6件のテスト通過、
`ruff check`もクリーン)。新規APIコールはゼロです。

## 結果

| 実験 | オフセット別 queue_wait_s平均(baseline比) | 説明できた割合(オフセット0のみ→累積) |
|---|---|---|
| `llm2025_ep8_zenhan_full_lecture` | baseline 1.30秒 / offset0 7.92秒(n=5) / offset1 5.76秒(n=2) / offset2 3.83秒(n=1) | 54.9% → **74.6%** |
| `llm2025_ep8_zenhan_full_lecture_v2_dict` | baseline 1.21秒 / offset0 6.15秒(n=6) / offset1 6.57秒(n=2) | 39.7% → **54.7%** |

**結論(確認、`status: tested`)**: 無音ギャップ直後のキュー待ち時間の上昇は
1バッチだけでなく2〜3バッチにわたって持続してから baseline に戻ることが
確認できました。この「持続」を累積して数えると、サイクル62の残差ALに対する
説明割合はいずれの実験でも大きく上昇しました(それぞれ+19.7pt、+15.0pt)。
ただし、それでも残差の25〜45%程度はまだ説明が付いていません。また、
高いオフセットではサンプル数が少ない(n=1〜2)ため、「ちょうど2バッチ/3バッチで
解消する」という正確な数字自体にはノイズが大きいことも明記しています。

副次的な発見として、`experiments/*.json`全体を対象にした今回の解析で、サイクル61の
対象外だった別の2本の実験(`full_llm2025_zenhan_gemini3flash_s0`、
`full_llm2025_zenhan_gpt54mini_s0`)にも同種の除外対象ギャップがあり、それぞれ
23.7%/30.3%という(規模は小さいが)同方向の効果が見られることも分かりました。

## 人間への確認が必要な項目

今回新たに人間の判断が必要な項目はありません。`pending_approval.json`は空です。
`h-continuous-rating-cps-calibration`は引き続き人間(rm-2278)による20〜30分程度の
視聴・評価作業待ちです。

## 次回への引き継ぎ

- バックログは依然4件/上限6件です(今回の新規仮説1件が`tested`になったため)。
- Deepgram WebSocket/OpenAI REST到達性チェックの結果、今サイクルもライブAPI実験は
  実行不可と確認しました(DeepgramはREST到達可能・WebSocketアップグレードのみ
  プロキシに壊される、OpenAIはREST自体が到達不可)。この2件はサンドボックス環境側の
  制約であり、`h-judge-cross-model-agreement-check`/
  `h-review-short-acronym-keyterm-false-positive`の`blocked_reason`に記録済みです。
  サンドボックス環境のネットワーク許可が変わらない限り、毎サイクル再チェックしても
  結果は変わらない可能性が高いので、次回以降は環境が変わったと推測できる場合
  (例: PLAYBOOKの別の節でネットワーク設定変更の記述がある等)にのみ再確認する
  運用でよいかもしれません。
- 今回も残った25〜45%の未説明分について: 「ギャップ後の持続」という方向性は
  確認できましたが、他の要因(セトル時間の伝播以外の何か)が残っている可能性が
  あります。次の仮説候補としては、各ランの個々のギャップごとの残差(ラン全体の
  平均ではなく)を見て、持続パターンに一貫性があるかを確認する方向が考えられます。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません(自己改善の判断は`reflections.md`の
今回のエントリに記載)。

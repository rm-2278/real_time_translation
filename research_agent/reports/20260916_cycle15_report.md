# サイクル15 レポート (2026-09-16)

## 今回のきっかけ

rm-2278さんから「レイテンシがまだ大きい、何とかならないか」「chrF以外のリアルタイム専用ベンチマークの調査はどうなったか」「(API call実装という制約下で)世の中の工夫を調べて仮説をたくさん作ってほしい」という依頼があり、GENERATE_HYPOTHESES状態からの通常の1-3件追加という慣例を超えて、まとまった文献調査＋仮説生成を行いました。

## Q1: レイテンシはまだ大きい。何とかならないか？

**結論: LLM翻訳呼び出し自体は全く遅くない。遅いのはASR側の「いつ翻訳を開始するか」の判断待ちです。**

今回新たに `mt_latency_decomposition.py` を実装し、既存の全88件の実験JSONの`results.events`を再解析しました（新規API呼び出しゼロ）。各翻訳バッチについて

- `queue_wait_s`: ASRがその音声内容を最初に報告した時刻 → LLMの最初の翻訳文字が出るまでの時間
- `stream_duration_s`: 最初の文字 → 最後の文字までのLLMストリーミング時間

を分解した結果:

| | mean | median | max |
|---|---|---|---|
| stream_duration_s (LLM応答そのもの) | 0.038s | **0.003s** | 1.5s |
| queue_wait_s (通常rpm、current_best_demo) | 1.90s | 1.89s | - |
| queue_wait_s (rpm=9に制限した実験) | 87.6s | 2.74s | 240s(!) |

LLMのストリーミング応答は中央値3ミリ秒で完了しており、ボトルネックはほぼゼロです。一方で「音声内容が揃ってから翻訳が実際に動き出すまで」の待ち時間が最良構成でも中央値1.9秒あり、rpm制限がきついと最大240秒にまで膨れ上がります。

これは以前のサイクル(h-asr-final-emission-latency, h-utterance-batch-timing)で見つかっていた**`deepgram_max_interim_duration`(固定2.5秒のソフトファイナライズ・タイマー)が全バッチの50-65%を、実際の無音検出(`deepgram_endpointing`, 300-2000msでスイープ済み)より先に強制カットしている**という発見と整合します。つまり:

**最も効果が見込め、かつ最も実行コストが低い一手** (`h-max-interim-duration-raise`): `DEEPGRAM_MAX_INTERIM_DURATION`を環境変数だけで5〜8秒程度に上げる(またはNoneにする)ライブ実験。コード変更不要、既存のキャッシュ済みクリップで数十円程度。**このセッションでは.envのAPIキーを明示的な許可なく使うのを避けたため未実行**です。実行する場合のコマンド:

```bash
DEEPGRAM_MAX_INTERIM_DURATION=6.0 uv run real-time-translation-exp-youtube \
  --url <wjZofJX0v4M> --start 300 --end 390 --name h_max_interim_6s_test \
  --domain ml_transformers  # current_best_demoと同じclip/window/rpm設定に合わせる
```

実行してよければ教えてください、すぐ回せます。

その他、キューイング以外の切り口として今回の文献調査から2つ、コード変更を要する新仮説を追加しました(未実行、承認待ち):

- **h-semantic-completeness-gating**: 2026年の新しい3本の論文(FastTurn, Phoenix-VAD, SimulSense)がいずれも「無音時間だけに基づくendpointingは構造的に間違っている」と主張しており、これは本リポジトリ自身の`h-endpointing-pause-vs-sentence-boundary-audit`の発見(どの閾値でも85-94%が文の途中でコミットされている)と一致します。3本とも専用モデルの訓練が必要でAPI-onlyでは直接使えませんが、「今のテキストが完結した節かどうか」を安いLLM呼び出し1回で判定させ、タイマーより早くコミットするかホールドを延長するかを決める、というzero-shot近似は実装可能です。
- **h-monotonic-chunkwise-prompt-enja**: 見つけた論文(Makinae et al. 2024, EN-JAに特化)は「同時通訳者は語順を崩さない(自然さを犠牲にしても)ことで、必要な先読み量を減らしている」と主張しています。英語→日本語というこのリポジトリの言語対はまさに語順の遠い(SVO対SOV)組み合わせで、これがレイテンシの構造的な下限になっている可能性があります。プロンプトに「途中コミットでは語順維持を優先」という指示を追加するだけの、コード変更が小さい仮説です。

## Q2: chrF以外のリアルタイム専用ベンチマークの調査はどうなったか？

**状況**: 文献としては読んでいたが(`papi2025-howreal`, `polak2026-meta-evaluation-latency-metrics`=YAAL)、「chrFとは別に、品質と遅延を同時に見る」という発想を実際にコード化していたのはASR側(`h-cla-asr-latency-metric`によるCLA=文字レベルアライメントASR遅延)とちらつき側(`h-flicker-metric`によるNormalized Erasure)だけで、**品質(chrF)と遅延を1つの表・曲線として突き合わせる作業は未着手**でした。

今回`quality_latency_joint.py`を実装し、`results.csv`(chrF)・`asr_latency_cla.csv`・`flicker_metrics.csv`・新しい`mt_latency_decomposition.csv`をexperiment_nameで結合しました(新規API呼び出しゼロ)。

正直な結果: 94件の実験のうち、chrFスコアと細かい遅延指標の**両方**を持つ行はわずか6件(`asr_keyterms_on/off`系列)でした。これは新しい知見というより「品質を測った実験」と「遅延を細かく測った実験」がこれまでほぼ重ならずに走っていた、という運用上のギャップの可視化です。

**重要な誠実性の注記**: これはAverage Lagging/LAAL/YAALそのものの再実装ではありません。それらはトークン単位のソース消費位置に基づく式で、現在のイベントログにはその粒度のデータがありません。次にやるなら、`llm_translator.py`の`translate_stream`で「その時点までに消費したソース単語数」と紐付けたタイムスタンプを新規に記録する必要があり、これはretroactive分析ではなく本番コードに手を入れる仮説になります(次サイクル候補として記録済み)。

## 今回追加した文献 (6件)

WebSearch 6件 + WebFetch 3件(このセッションはローカルなのでarxiv.org/ai.google.devへのWebFetchが実際に成功しました。クラウド版研究エージェントのサンドボックスがブロックされているのとは別の環境です):

- FastTurn (turn-detection, arXiv:2604.01897)
- Phoenix-VAD (semantic endpoint detection, arXiv:2509.20410)
- SimulSense (sense-driven interpreting, arXiv:2509.21932)
- RLM-Cascade (response-level speculative decoding for API-only LLM serving, arXiv:2606.22840) — **API-only constraint下でも動く投機的デコーディングは実在**しますが、検証の仕組み(意味的類似度マッチング)が短い高忠実度な翻訳には向いておらず、かつ本サイクルの`h-mt-queue-wait-decomposition`の結果(LLM応答は既に0.04秒しかかからない)から、そもそも「応答生成を速くする」ことに意味がないため採用を見送りました。
- Makinae et al. (word-order synchronization metric, EN-JA特化, arXiv:2407.06650)
- Gemini Live API 公式ドキュメント (live-translate機能)

## Gemini Live API (音声→音声の同時通訳)は使えるか？

`gemini-3.5-live-translate-preview`という、ASR+翻訳+TTSを1つのWebSocketセッションに統合したモデルがあります。魅力的に見えますが、公式ドキュメントを直接確認した結果、**テキスト入力非対応・音声出力必須(テキストのみの出力モードなし)**という制約があり、このリポジトリの本来の用途(Zoom/YouTubeのテキスト字幕)には現状フィットしません。Deepgram Fluxのときと同様、「技術的には面白いが、本番アーキテクチャの大きな変更が要る」判断として人間向けにフラグを立てるに留めました(`h-gemini-live-translate-feasibility`, tested)。

## 追記 (同日、rm-2278さんが.envキー使用を許可した後)

`h-max-interim-duration-raise`をライブ実行しました。1回目は`reading_speed_chars_per_sec`がbaselineの4.5ではなくコードデフォルトの6.0になっている交絡に気づき破棄、2回目(`h_max_interim_6s_test_v2`)で`deepgram_max_interim_duration`だけを変えたクリーンな比較ができました:

| 指標 | baseline (2.5s) | treatment (6.0s) |
|---|---|---|
| avg_end_to_end_latency | 6.41s | 6.18s (-4%) |
| **max_end_to_end_latency** | 9.26s | **6.59s (-29%)** |
| avg_mt_latency | 1.40s | 0.18s (-87%) |
| 複数バッチに分断された発話数 | 15/21 | **1/21** |

タイマーを2.5秒→6秒に上げただけで、発話が翻訳バッチの途中で強制的に分断される回数が15件→1件に激減し、レイテンシの裾(max)も3割近く縮みました。ASR側(Deepgram)の書き起こし自体は完全に同一(asr_ne_word_mean, セグメント数とも一致)で、影響を受けたのは翻訳バッチの区切り方だけです。cross-batch NE(ちらつき)の生の数値は悪化して見えますが(0.96→3.0)、これは比較対象が15件→1件に激減した結果のサンプル1件の値なので統計的意味はなく、「分断そのものが激減した」ことの方が実質的な改善です。

本番のデフォルト値(2.5秒)を引き上げることを推奨します。次のステップとして8〜10秒や無効化も試し、リファレンス訳を用意してchrFも合わせて見るとより確証が持てます。

## 追記2 (同日、リサーチをさらに深掘り + キャプション付き2分デモ動画)

rm-2278さんから「LLM2025応用編・第8回のtransformerパート(2分クリップ)で、実写映像+字幕のmp4を出力してほしい」「リサーチをもっと深めて仮説を増やしてほしい」という追加依頼がありました。

### キャプション付きデモ動画

`caption_video_demo.py`はこれまで音声だけを抽出し単色キャンバスに字幕を乗せる仕様でした(実写映像は使っていなかった)。`--background video`オプションを追加し、実際のソース映像をレターボックスして字幕を焼き込めるようにしました(字幕がクリップの尺を超えて続く場合は最後のフレームを静止して延長)。

検証済みのbest config(`deepgram_max_interim_duration=6.0`, `endpointing=300`, `rpm=60`)で`experiments/clips/llm_course_ep8_transformer_120s.mp4`(LLM2025応用編第8回・Transformer部分の2分クリップ)をライブ実行し、そのASR+翻訳結果を実写映像に焼き込みました。

- 出力: `experiments/caption_demos/llm_course_transformer_2min_captioned.mp4`(1280x720, 129.5秒, 32セグメント, 平均信頼度0.916)
- 実験JSON: `experiments/20260916_llm_course_transformer_clip_best_config.json`
- ※`experiments/caption_demos/`は.gitignore対象(生成物のため)なのでコミットはしていません。ローカルにあります。

1回目のライブ実行はマシンのスリープに巻き込まれてwebsocketのkeepalive pingが失敗し、ハングしたため強制終了→再実行しています(2回目はクリーンに完走、末尾でDeepgramの1011タイムアウトが出ましたが119.3秒/120秒まで取得できておりコンテンツの欠落はありません)。

### 追加リサーチ (文献4本、仮説2件)

- **X2Streaming-ASR**(arXiv:2609.08672)・**GPT-Realtime-Translate**(OpenAI公式)・**SASST**(arXiv:2508.07781)・**Average Token Delay**(Kano et al. 2023)を新たに読了。
- **h-asr-confidence-early-commit**(承認待ち): Deepgramが既に返している単語ごとの信頼度(`confidence`フィールド、既にログ済み・追加API呼び出し不要)を使い、固定タイマーより早く自信度の高い区間を確定させる。業界の実践("confidence>0.7でコミットしプレフィックスが変わったときだけ再翻訳")にも合致。
- **h-openai-realtime-translate-feasibility**(実行・分析済み): OpenAIの音声→音声翻訳API。Geminiのライブ翻訳と違い日本語が対象13言語に含まれ、文字起こしが音声と並行してストリームされる設計だが、テキストのみ出力(音声生成の無効化)が可能かはドキュメントからは確定できず「未解決」で結論。

### Q2への追加の手がかり

Average Token Delay (ATD) 論文は、出力の「長さ(duration)」自体が後続の翻訳を遅らせることを罰する指標で、Ear-Voice Span(人間の同時通訳者の自然な遅延)との相関が最も高いと報告されています。ただし公式が要求するのは結局トークン単位のソース消費位置と紐づいたタイムスタンプで、`h-quality-latency-joint-table`が既に指摘した「本番コードへの計装が必要」という結論は変わりません。ただ、今回の`mt_latency_decomposition.py`が既に出している`stream_duration_s`(1バッチの出力の長さ)を使えば、「あるバッチの出力の長さが次のバッチのqueue_waitをどれだけ押し出すか」という簡易版のduration-aware指標は、トークン単位のアライメントなしでも作れそうです。次の一手の候補として記録しておきます。

### バックログの状態 (2026-09-16終了時点)

新規仮説は合計8件(今日1日で): tested 5件(h-mt-queue-wait-decomposition, h-quality-latency-joint-table, h-gemini-live-translate-feasibility, h-max-interim-duration-raise, h-openai-realtime-translate-feasibility)、承認待ち(queued/proposed)3件(h-semantic-completeness-gating, h-monotonic-chunkwise-prompt-enja, h-asr-confidence-early-commit)。

## 追記3 (同日、方針転換: 「論文由来の知見」に集中)

rm-2278さんが新しいキャプション付きデモ動画を見て「結構よくね？」と評価してくれました。**この評価は記録しておく価値があります**: 良い結果が出た設定(タイマー6秒・endpointing=300・rpm=60・reading-speed-budget)は、ホールドバック・LocalAgreement・continuation-anchorのいずれもOFFの状態です。つまり今の実用性の高さは、論文から輸入した特定のアルゴリズムではなく、①私たち自身の診断から導いたタイマー調整と、②字幕業界の可読速度基準に基づく自作機能(論文由来ではない)によるものだと確認できました。

これを受けて、rm-2278さんから明確な方針が出ました: **「かなり実用的にはなっているから、あとは『論文から組み込んだ知見』での改善をひたすらに目指そう」**。今後のGENERATE_HYPOTHESESでは、各仮説が「文献由来の技術」なのか「自分たちの診断由来のチューニング」なのか「一般的なエンジニアリング上の工夫」なのかを明示し、文献由来のものを優先することにします。

現在の承認待ちバックログのうち文献由来度が高いもの: `h-semantic-completeness-gating`(FastTurn/Phoenix-VAD/SimulSense)、`h-monotonic-chunkwise-prompt-enja`(Makinae et al.)、`h-asr-confidence-early-commit`(X2Streaming-ASR)。`h-backlog-adaptive-compression-budget`は人間発案のエンジニアリング上の工夫で文献グラウンディングは薄めです。

### ①ホールドバック x 新タイマー の再検証

ホールドバックの実測+28%改善(cross-batch NE 0.836→0.605)は、複数バッチ分断が多かった**旧タイマー(2.5秒)**下で測ったもの。新タイマー(6秒)では分断自体が15/21→1/21に激減しているため、ホールドバックの出番がどれだけ残っているかをライブA/Bで再検証中です(`h_holdback_on_raised_timer`、同一クリップ・同一設定でmasking_holdback_words=2のみ追加)。結果は次の追記で報告します。

## 今回のパイプライン状態

- 新規仮説6件追加、うち3件($0, retroactive/research)は本セッション内で実行・分析済み(tested)
- 残り3件は承認待ち(`research_agent/state/pending_approval.json`参照): h-max-interim-duration-raise(ライブ実験、コード変更なし)、h-semantic-completeness-gating(コード変更要)、h-monotonic-chunkwise-prompt-enja(コード変更要)
- 本サイクルの新規API支出: $0.00(既存の$0.11累計から変わらず、日次上限$7に対して余裕あり)

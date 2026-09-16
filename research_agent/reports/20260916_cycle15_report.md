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

## 今回のパイプライン状態

- 新規仮説6件追加、うち3件($0, retroactive/research)は本セッション内で実行・分析済み(tested)
- 残り3件は承認待ち(`research_agent/state/pending_approval.json`参照): h-max-interim-duration-raise(ライブ実験、コード変更なし)、h-semantic-completeness-gating(コード変更要)、h-monotonic-chunkwise-prompt-enja(コード変更要)
- 本サイクルの新規API支出: $0.00(既存の$0.11累計から変わらず、日次上限$7に対して余裕あり)

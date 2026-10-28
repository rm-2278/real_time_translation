# サイクル77 結果レポート(2026-10-28, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`EXTRACT_PAPERS`状態でした(サイクル77の
`SEARCH_PAPERS`でWebSearchを1回実行し、新規論文1件
`reina-santan2026-readwrite-overread-bias`を`papers.json`に`found`として
追加した直後)。今回は`EXTRACT_PAPERS → READ_PAPERS →
GENERATE_HYPOTHESES → HUMAN_APPROVAL → RUN_EXPERIMENTS →
ANALYZE_RESULTS → WRITE_REPORT`まで、1サイクル分をすべて実行しました。

## EXTRACT_PAPERS / READ_PAPERS: 新規論文の抽出と要約

対象論文は`reina-santan2026-readwrite-overread-bias`
(arXiv:2604.09916, Interspeech 2026, "Regularized Entropy Information
Adaptation with Temporal-Awareness Networks for Simultaneous Speech
Translation")。

- `arxiv.org`へのWebFetchは`ENOTFOUND`(このクラウドセッションの許可
  ネットワークドメイン外、EGRESS_BLOCKED)で失敗したため、PLAYBOOKの
  フォールバック手順どおり、正確な論文タイトル+"abstract"でのWebSearchに
  切り替え、要旨相当の内容と具体的な数値を取得しました。
- 論文の要点: 既存のREINA(読み書きポリシーが「どれだけ情報を得たか」を
  基準に、もっと音声を読むか、翻訳を書き出すかを決める手法)は時間的な
  文脈を欠いており、発話の大部分を読み終えるまで書き出しを待ってしまう
  (over-reading)傾向がある、という指摘。これを修正するREINA-SAN(教師
  あり アラインメントネットワーク)とREINA-TAN(タイムステップ拡張
  ネットワーク)を提案し、Whisperに適用した場合、Normalized Streaming
  Efficiency (NoSE) のフロンティアを既存手法比で最大7.1%改善。
- `experiments/results.csv`と`git log`をキーワード検索でクロス
  チェック(PLAYBOOKの必須手順)した結果、このリポジトリには読み書き
  タイミングに関する既存の検証済み作業(`h-localagreement-commit-test`、
  `h-masking-holdback`系、`h-soft-anchor-gate-*`系)が既に多数あること
  を確認。論文自体の手法(Whisperへの訓練済みポリシーネットワーク追加)
  はこのリポジトリのホスト型API構成(Deepgram/Gemini、ファインチューニ
  ング不可)には移植不可である点も、過去の類似論文(`cuhksz2026-iwslt-
  qwen3omni-wait-token`等)と同じ注意点として記録しました。

## GENERATE_HYPOTHESES: 新規仮説1件を追加

`h-gate-utterance-end-temporal-blindness`を追加。
`reina-santan2026-readwrite-overread-bias`の「時間的文脈を欠いた
read/writeポリシーは、待っても意味がない場面でも待ってしまう」という
指摘を、このリポジトリ自身の`soft_anchor_disfluency_gate_replay.py`の
ゲート条件(`len(delta_text.split()) < GATE_MIN_WORDS`、
`is_utterance_end`を一切見ていない)に当てはめられないか、というもの。

仮説を書く前に、既存の2つのゲート実験JSON
(`20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json`、
`20260922_h_soft_anchor_gate_min3_replay.json`)に対して実際にコードを
確認・簡易集計した結果、ゲートが発動したバッチのうち**22/22(100%)**と
**46/47(97.9%)**が、まさにその発話の最後のバッチ(`is_utterance_end=
true`、これ以上音声が来ない)であることが分かりました。これは
PLAYBOOKが繰り返し求めている「仮説を書く前に実データ・実コードで検証
する」というルールに沿った裏付けです。コストは$0(既存クリップの
再分析のみ、新規API呼び出しなし)。

バックログ件数(`queued`+`proposed`合計、PLAYBOOKの明示ルールどおり
両方のステータスを数える): 3 queued + 2 proposed(今回の新規分 +
既存の`h-judge-repeat-independent-batches-corrected-design`、
`needs_human`)= 5件、上限6件の範囲内。

## HUMAN_APPROVAL: 自動承認

`check-budget 0`を実行し`AUTO_APPROVE`判定を確認。
`uses_existing_clips=true`のため、PLAYBOOKの通常経路どおり
`approval: auto_approved` / `status: queued`を設定しました。

## RUN_EXPERIMENTS: ゼロコストの再分析スクリプトを実装・実行

このサイクルで選んだのは`h-gate-utterance-end-temporal-blindness`
(新規API呼び出し$0の再分析仮説のため、PLAYBOOKの優先順位どおり最優先)。
DEEPGRAM_API_KEY/GOOGLE_API_KEYは環境に設定済み、ffmpegも利用可能でした
が、今回はいずれも新規APIコールが不要なため実行には関係ありません。

新規スクリプト`src/real_time_translation/experiments/
gate_utterance_end_temporal_blindness_check.py`を実装し、既存の
`soft_anchor_gate_erasure_decomposition.py`(LCPベースの遷移単位erasure
計算)の手法を、**両方の**既存ゲート実験JSON・**両方の**ソースクリップに
一般化しました。`uv run`はこのリポジトリの既知の問題
(`zoom`/`rtms`エクストラがtest.pypi.orgをブロックされたホストから
取得しようとして失敗する)に今回も遭遇したため、PLAYBOOK記載の
venvワークアラウンド(`python3.13 -m venv .venv` → `uv pip install -e
".[experiments]"` → `uv pip install ruff`)で回避し、
`python3 -m ruff check`は無事パスしました。

実行結果(新規API呼び出しゼロ、`log-cost 0`で記録):

- `is_utterance_end`の割合: 全体で**68/69(98.6%)**、ファイル別に
  22/22(100%)、46/47(97.9%) -- GENERATE_HYPOTHESESでの簡易集計を
  正確に再現・確定。
- 遷移単位erasure(`is_utterance_end`バケット、n=136): `gated_soft_
  anchor`の平均正規化erasureは0.618、`soft_anchor_replay`は0.091。
  これまでに確認済みの約0.53の超過erasureコスト(過去の
  `h_soft_anchor_gate_erasure_decomposition`はmin3/guest-talkの
  部分集合のみで測定)が、ほぼ全てこの「発話末でゲートが発動した」
  バケットに集中していることを確認しました。もう一方の
  `mid_utterance`バケットはわずか1グループ(n=2)しかなく、
  「発話途中でゲートが発動した」ケースがこのリポジトリの既存データ
  にはほぼ存在しないこと自体も、新たな事実として記録しました。
- スパン単位のjudgeスコア比較(同じ`is_utterance_end`バケット、
  68スパングループ、ペアなし集計): `gated_soft_anchor`平均95.6 vs
  `soft_anchor_replay`平均92.2(差+3.4)。ただし
  `judge_noise_power_analysis.py`の分散分解手法で求めたグループ内
  ノイズのstdevは約6.0〜7.3で、この差はそれより小さく、かつスパン
  ごとのペア差分としては計算していません。PLAYBOOKがサイクル
  19-22で確立した「ノイズフロアを下回る差は判定不能として扱う」
  というルールに従い、**この差は実質的な品質向上/低下の証拠とは
  見なさず「判定不能」**として記録しました。

## ANALYZE_RESULTS / 結論

`h-gate-utterance-end-temporal-blindness`は`status: tested`としました。

- **確認されたこと**: ゲートはほぼ常に(98.6%)、発話が既に終わって
  これ以上音声が来ない場面で発動している。これは
  `reina-santan2026-readwrite-overread-bias`が指摘する「時間的文脈を
  欠いたポリシーは、待っても意味がない場面でも待ってしまう」という
  パターンと一致します。既知のerasureコスト(約0.53)も、この
  「待っても意味がない」場面にほぼ全て集中していることが確認できました。
- **判定不能のまま残ったこと**: このコストを相殺するような品質上の
  メリット(最終テキストのjudgeスコア)がゲートにあるかどうかは、
  今回のデータでは結論を出せませんでした(名目上は小さくゲート側が
  良い方向の差ですが、ノイズフロア以下かつペア比較していないため)。
- **次の一手(今回は実行せず、仮説の範囲外として明記)**:
  `soft_anchor_disfluency_gate_replay.py`のゲート条件に
  `is_utterance_end`の除外を追加し(発話末のバッチはゲートしない)、
  再度replayを実行してerasureコストが解消し、かつjudgeスコアが悪化
  しないかを確認する、という小規模な追加実験(新規APIコールが
  必要なため$0ではない)を、将来サイクルの新規仮説として提案できます。

## 環境面のメモ

`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は設定済み、`ffmpeg`も利用可能で
したが、今回の仮説は新規API呼び出しが不要だったため、実際にこれらが
到達可能かどうかの確認は行っていません(次回ライブ実験が必要な仮説を
選ぶ際に改めて確認が必要です)。

## 次のステップ

`REFLECT`に進み、今回の振り返り(論文抽出フォールバックの再利用、
ゲートの時間的盲目性という新しい知見、今後の追加実験の提案)を
`reflections.md`に記録したうえで、次サイクルの方針を決定します。

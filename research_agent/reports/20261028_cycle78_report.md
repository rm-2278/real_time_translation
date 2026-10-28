# サイクル78 結果レポート(2026-10-28, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`GENERATE_HYPOTHESES`状態でした(サイクル77の
`REFLECT`が、新たな文献検索を挟まずに直接`GENERATE_HYPOTHESES`へ進むと
決めた直後)。今回は`GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで、1サイクル分を
実行しました。新規の文献検索(`SEARCH_PAPERS`)は行っていません
(サイクル77の`REFLECT`がそう決めたとおり)。

## GENERATE_HYPOTHESES: 新規仮説1件を追加

`h-soft-anchor-gate-exempt-utterance-end`を追加しました。サイクル77で
`tested`になった`h-gate-utterance-end-temporal-blindness`の直接の
続き(同じ論文`reina-santan2026-readwrite-overread-bias`に基づく)です。

サイクル77の結論を整理すると:

- ゲート(`soft_anchor_disfluency_gate_replay.py`の
  `len(delta_text.split()) < GATE_MIN_WORDS`)は、全ゲート発動のうち
  **98.6%(68/69)**が発話の最後のバッチ(`is_utterance_end=true`、
  これ以上音声が来ない場面)で起きている。
- 既知の超過erasureコスト(約0.53)も、ほぼ全てこの「発話末」バケット
  (n=136)に集中している(0.618 vs 0.091)。
- 一方で、このコストを相殺する品質上のメリット(judgeスコア)が
  ゲートにあるかどうかは、サイクル77の時点では**判定不能**のまま
  残されました(名目+3.4点の差はノイズフロア約6.0〜7.3より小さく、
  かつペア比較もしていないため、統計的な検出力不足)。

今回の新規仮説は、この「判定不能」を実際に解消しようとするものです:
`gated_soft_anchor`のゲート条件に`not batch.is_utterance_end`を
追加した新しい条件`gated_soft_anchor_exempt_end`を作り、同じ2本の
既存ソース実験クリップに対して3条件(`baseline_replay`、
`soft_anchor_replay`、`gated_soft_anchor`、`gated_soft_anchor_exempt_end`
の4条件)で再実行し、(a) erasureコストが解消するか、(b) judgeスコアが
悪化しないかを、今回は**スパンごとのペア差分**として測定する設計です。
推定コストは$0.35(既存クリップ再利用、新規ダウンロードなし、
`uses_existing_clips: true`)。

バックログ件数(`queued`+`proposed`合計、PLAYBOOKの明示ルールどおり
両方のステータスを数える): 3 queued + 2 proposed(今回の新規分 +
既存の`h-judge-repeat-independent-batches-corrected-design`、
いずれも`needs_human`)= 5件、上限6件の範囲内。

## HUMAN_APPROVAL: 自己承認ブロックのため即座に`needs_human`へ

`check-budget 0.35`は`AUTO_APPROVE`を返し、`uses_existing_clips=true`
でした。通常であればPLAYBOOKの通常経路(`approval: auto_approved` /
`status: queued`を自分で書き込む)に従うべきところですが、今回は
**その書き込み自体を試みませんでした**。

理由: `reflections.md`サイクル36で確定した「自己承認ブロック」
(このエージェント自身が提案した仮説に対して、エージェント自身が
`auto_approved`/`queued`を書き込む、という“自己承認”パターンそのものを、
この実行環境の権限分類器が一般的にブロックする、仮説の種類を問わない)
が、今回のケースにもそのまま当てはまると判断したためです。実際、
直近の`pending_approval.json`エントリ(2026-10-20付、
`h-judge-repeat-independent-batches-corrected-design`)でも、全く同じ
`AUTO_APPROVE`+`uses_existing_clips=true`の組み合わせでこの書き込みが
拒否されていたことが記録されています。PLAYBOOK自身が「この拒否を
“念のため”再テストするために将来のサイクルを使うな」と明記している
ため、今回は再試行せず、`approval: needs_human`を設定し
`pending_approval.json`に理由を記録して、rm-2278さんの判断を待つ
扱いにしました。

既存の3件の`queued`仮説(`approval != needs_human`)がまだ残っている
ため、PLAYBOOKのルールに従い`RUN_EXPERIMENTS`へ進みました。

## RUN_EXPERIMENTS: 既存の3件の`queued`仮説すべてが依然ブロック中

環境チェックを再実行しました:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`: いずれも環境に
  設定済み。`ffmpeg`も利用可能。
- Deepgram REST(`/v1/projects`): **200**(到達可能)。
- Gemini REST(`/v1beta/models`): **200**(到達可能)。
- OpenAI REST(`/v1/models`): **CONNECT拒否(403)**
  (`curl`終了コード56、プロキシのステータスで`connect_rejected`)。
  サイクル72で見つかった既知のブロックと完全に一致する再現です。
- Deepgram **listen-websocket**ハンドシェイク: `python3.13 -m venv` +
  `uv pip install -e ".[experiments]"`で作った新規venv(`websockets==
  17.3`)から実際のWebSocketアップグレードを試したところ、
  **HTTP 400 "Connection header did not include 'upgrade'"**で失敗。
  これもサイクル5/63/67/68/69で繰り返し確認されている既知のブロックと
  完全に一致します(RESTは通るがWebSocketアップグレードがプロキシに
  よって壊される、という問題)。

この結果、既存の3件の`queued`仮説はいずれも今回も実行不可でした:

1. `h-judge-cross-model-agreement-check`($0.05): `gpt-4o-mini`経由の
   OpenAI呼び出しが必要 → OpenAI接続拒否のためブロック継続
   (`blocked_reason`にサイクル78分を追記)。
2. `h-review-short-acronym-keyterm-false-positive`($0.05): 実際の
   Deepgram ASRストリーミングが必要 → listen-websocketのハンドシェイク
   失敗のためブロック継続(`blocked_reason`にサイクル78分を追記)。
3. `h-continuous-rating-cps-calibration`($0): そもそも環境/APIの
   問題ではなく、rm-2278さん自身の20〜30分程度の視聴・評価時間が
   必要なタスクです。この点は今回初めて明示的に`blocked_reason`として
   記録しました(環境が直ればいつか解消する類のブロックではなく、
   人間の作業時間そのものが必要、という異なる種類のブロックである
   ことをはっきりさせるためです)。

PLAYBOOKの指示(環境が実験を実行できない場合は結果を捏造せず、
ギャップを記録して報告に進む)に従い、新規の実験は一件も実行せず、
`ANALYZE_RESULTS`(分析対象なし)を経て本レポートの作成に進みました。

## 環境面のメモ(人間への確認依頼)

今回判明した事実を整理すると、この実行環境(クラウド/スケジュール
セッション)では構造的に:

- OpenAI APIへの接続が明示的にブロックされている(ポリシーによる
  CONNECT拒否)。
- Deepgramのストリーミング(WebSocket)接続のアップグレード
  ハンドシェイクがプロキシによって壊れている(RESTは問題ない)。

という2つの制約が、サイクルを重ねても解消されていません。この2つの
制約がある限り、`h-judge-cross-model-agreement-check`と
`h-review-short-acronym-keyterm-false-positive`はこの実行環境では
**原理的に永久に**実行不可能です。人間が別環境(ローカルマシンなど)
で手動実行するか、このクラウド環境のネットワークポリシーを変更する
以外に解消方法はありません。もし優先度が高いようであれば、ローカル
セッションでの実行をご検討ください。

## HUMAN_APPROVAL待ちの項目(rm-2278さんの確認が必要)

`pending_approval.json`に2件あります:

1. `h-judge-repeat-independent-batches-corrected-design`
   (2026-10-20付、$0.35) -- 既報告、変更なし。
2. `h-soft-anchor-gate-exempt-utterance-end`(今回新規追加、$0.35)
   -- 上記のとおり、自己承認ブロックのため今回エスカレーション。

いずれも`check-budget`は`AUTO_APPROVE`、`uses_existing_clips=true`で、
通常のPLAYBOOKルールなら自動承認される内容です。rm-2278さんが
`python3 research_agent/orchestrator.py approve --all`(または個別の
IDを指定)を実行していただければ、次サイクルでそのまま
`RUN_EXPERIMENTS`に進めます。

## 次のステップ

`REFLECT`に進み、今回の振り返り(自己承認ブロックの再発、環境ブロック
2件の継続再確認、人間待ちタスクの明示的な分類)を`reflections.md`に
記録したうえで、次サイクルの方針を決定します。

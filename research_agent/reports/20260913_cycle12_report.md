# リサーチエージェント サイクル12 レポート (2026-09-13)

## 今回やったこと(概要)

前回サイクル11の`REFLECT`の判断どおり、`SEARCH_PAPERS`から着手しました。
さらに今回は、人間(rm-2278)がサンドボックス外で実行してくれた実験結果が
リポジトリにpushされているのを発見し、その分析も並行して行いました。

1. **SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS**: 前サイクルで見つかった
   3件の論文/記事(`exposst2026`, `simulmask2024`, `coval2026`)を
   WebSearch/WebFetchで読み込みました(`arxiv.org`と`coval.ai`への直接
   WebFetchは本環境のプロキシでブロックされているため、WebSearch経由の
   スニペットを利用)。
2. **人間提供の実験結果の分析(仮説`h-masking-holdback`)**: rm-2278が
   自分のマシンで`masking_holdback_words=2`の実験を実行し、コミット
   済みでした。分析したところ、一度は「フリッカーが約47%改善」という
   有望な結果に見えましたが、深掘りした結果、**比較が無効**だったことが
   判明しました(詳細は下記)。
3. **GENERATE_HYPOTHESES**: 新規仮説を2件追加(`h-masking-holdback-rpm-matched-retest`,
   `h-deepgram-flux-eot-availability-check`)。
4. **HUMAN_APPROVAL → RUN_EXPERIMENTS**: 両方`check-budget`で
   `AUTO_APPROVE`。`h-deepgram-flux-eot-availability-check`は$0の
   調査で完了。ASR依存の残り仮説は環境ブロッカーが継続中で実行不可。

## 文献レビューの結果

- **ExPosST**(`exposst2026`)と**SimulMask**(`simulmask2024`)は、
  どちらも「マスキング」という言葉が本リポジトリの`h-masking-holdback`
  と似ていますが、中身は全くの別物と判明しました。両論文とも
  **モデルのファインチューニング**を前提とする手法(ExPosSTは
  位置エンコーディングの一貫性を保つ推論最適化+ポリシー一貫
  ファインチューニング、SimulMaskは訓練時のアテンションマスクで
  決定ポリシーを学習させる手法)であり、本リポジトリのようなAPI経由
  (Gemini/OpenAI、重みへのアクセスなし)の翻訳では応用できません。
  今後、同じ「masking」というキーワードだけで仮説枠を無駄にしない
  よう、`papers.json`に明記しました。
- **Coval.aiのSTTベンダー比較**(`coval2026`)から、具体的な新しい
  手がかりを得ました。Deepgram Nova-2/Nova-3は最速(中央値TTFT
  ~992ms)だが最もWERが高い(25.2〜25.3%)という速度・精度トレード
  オフが確認された一方、**Deepgram Flux**という新モデル(会話用
  end-of-turn検出内蔵、EOT中央値300ms未満)が2026-04-29にリリース
  されていたことが分かりました。これは`h-endpointing-connection-verify`
  が残した未解決の疑問(エンドポインティング設定の効果が測れない
  理由)よりさらに大きい問い── 手動エンドポインティング調整自体を
  Flux内蔵のEOT検出で置き換えられるのでは?── につながる発見でした。

## 重要な訂正: `h-masking-holdback`の人間提供実験は無効な比較だった

rm-2278が2026-09-13にローカル環境で`MASKING_HOLDBACK_WORDS=2`の実験を
実行し、コミット(`bf6d22c`)していました。本セッションのDeepgram
WebSocketは依然ブロックされたままなので、これは非常にありがたい
ことでした。

最初に`flicker_metrics.py`を($0・API呼び出しなしで)再実行し、
`translation_ne_char_cross_batch_mean`(発話をまたぐ翻訳の「書き
直され度」)が基準値0.947→0.502に下がったのを見て、「フリッカーが
約47%改善」と判断しかけました。

しかし、各実験JSONの`config`ブロックと`translation_complete`イベント
の数を突き合わせて確認したところ、**重大な交絡要因**を発見しました:

- 基準実験(`chunk_latency_sweep2_300ms`)は`gemini_rpm_limit=60`
- 人間提供の実験(`h_masking_holdback_local`)は`gemini_rpm_limit=9`
  (人間のAPIキーのプラン上限と思われ、意図した実験変数ではない)

この8.5倍厳しいレート制限が結果を支配していたことを示す証拠:
- 両実験のASR発話区切り(endpointing)は完全に一致(masking_holdbackや
  rpm制限がASR側に影響しないという想定どおり)
- holdback実験の生ASR中間結果は90秒間フル(asr_end_time=89.9秒まで)
  記録されている → 音声取り込み自体は正常
- しかし`translation_complete`イベントはholdback実験でわずか17件
  (0〜41.7秒分)しか生成されず、基準実験の37件(0〜89.9秒分)の
  半分にも届いていない → **翻訳側の処理がレート制限で追いつかず、
  クリップ後半の発話がまったく翻訳されないまま実験の待機ループが
  終了した**
- 平均翻訳レイテンシも2倍以上に悪化(1.76秒→3.94秒)、これも
  レート制限によるキューの滞留と整合

つまり、最初に見えた「フリッカー改善」は、実際には比較対象が
「クリップ前半だけの、たまたま跨バッチの発話が少なかった部分集合」
になってしまっていたためであり、`masking_holdback_words`自体の
効果を検証できたわけではありませんでした。同様にchrFも(既存の
`experiments/refs/wjZofJX0v4M.ja.vtt`から`extract_vtt_range.py`で
90秒分の参照テキストを抽出し、sacrebleuで$0計測)基準42.6・
holdback23.0と大差がつきましたが、これも同じカバレッジ不足が
原因で、翻訳品質の指標としては使えません。

`hypotheses.json`の`result_summary`を訂正し、次サイクル以降で
同じ数字を「確認済みの成果」として引用しないよう明記しました。

## 新規仮説

1. **`h-masking-holdback-rpm-matched-retest`**: 上記の交絡要因を
   取り除いた、`gemini_rpm_limit`を基準実験と揃えた再実験。
   新規コード不要、コスト見積もり$1.50、既存クリップ使用。
2. **`h-deepgram-flux-eot-availability-check`**: Deepgram Flux
   (`coval2026`由来)が本リポジトリで使えるかの$0調査。

## `h-deepgram-flux-eot-availability-check`の結果(完了)

`developers.deepgram.com`への直接WebFetchはブロックされているため、
WebSearchと(たまたま到達できた)`github.com`のリリースノートで調査:

- 英語専用モデル名は`flux-general-en`(`flux-general-multi`ではない
  ── 本リポジトリの英語ソースには前者が正しい選択、という仮説の
  予想が的中)
- Fluxは新しい`/v2/listen`エンドポイント(`client.listen.v2.connect()`)
  が必須で、既存の`/v1/listen`経路では利用不可
- v2/Flux対応は`deepgram-sdk`のv7.4〜7.5あたりで入った様子だが、
  本リポジトリの`pyproject.toml`は`deepgram-sdk>=5.0.0,<6.0.0`に
  固定されており、メジャーバージョンで2つ遅れている

**結論**: 技術的には可能だが、実質的にはSDKのメジャーバージョン
アップグレード(本番のZoom/YouTubeパイプラインが依存する同じ
`deepgram-sdk`呼び出しに影響)が必要で、実験フラグだけで安全に
隔離できる変更ではありません。リサーチエージェントの一仮説として
実装するのではなく、**人間による独立したSDKアップグレード・
プロジェクトとして検討することを推奨**します(下記「人間への
お願い」参照)。

## 環境状況(再確認、変化なし)

`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は設定済み・REST到達可能(200)。
`ffmpeg`は今回も未インストールでしたが`apt-get`で問題なく
インストールできました。しかし**Deepgram Listen WebSocketへの
アップグレード要求は今回もプロキシでHTTP 400
「Connection header did not include 'upgrade'」**に阻まれました
(サイクル5〜9と同じ症状、サイクル3以降変化なし)。1点だけ変化が
あり、以前見られたTLS証明書検証エラー(プロキシCAのkey usage
extension不足)は今回発生せず、WebSocketアップグレード自体の
問題だけが残っている状態まで絞り込めました。

## バックログの状態(queued/proposedは4件、上限6件以内)

| ID | ステータス | 備考 |
| --- | --- | --- |
| h-flicker-metric | tested | サイクル1で完了 |
| h-cross-utterance-flicker | tested | サイクル4で完了 |
| h-utterance-batch-timing | tested | サイクル6で完了 |
| h-cla-asr-latency-metric | tested | サイクル9で完了 |
| h-endpointing-connection-verify | tested | サイクル10で完了 |
| h-asr-final-emission-latency | tested | サイクル11で完了 |
| h-masking-holdback | tested(結論訂正) | 今回、rpm制限の交絡要因を発見・訂正 |
| h-deepgram-flux-eot-availability-check | tested | **今回完了**、SDKアップグレードが前提と判明 |
| h-localagreement-asr-commit | queued(ブロック中) | Deepgram WS問題、未実装のまま |
| h-gemini-only-masking-replay | queued(ブロック中→実は$0で今すぐ着手可能) | 次サイクルの最優先候補 |
| h-continuation-context-anchor | queued(ブロック中・同じ原因) | 変化なし |
| h-masking-holdback-rpm-matched-retest | queued(ブロック中・同じ原因) | **今回追加**、rpm制限を揃えたクリーンな再実験 |

## 人間(rm-2278)へのお願い

1. **環境ブロッカー(継続)**: サイクル3から変わらず、このサンドボックス
   のネットワークプロキシが`wss://api.deepgram.com/v1/listen`への
   WebSocketアップグレードを正しく中継できていません。プロキシ設定で
   このホストへのWebSocketアップグレードを許可(または生TCPで
   パススルー)いただけると、ブロック中の4仮説が一気に進みます。
2. **`h-masking-holdback`の再実験のお願い(新規)**: 前回ローカルで
   実行いただいた実験、大変助かりました。ただし`GEMINI_RPM_LIMIT`が
   9(基準実験の60と不一致)だったため、有効な比較になりませんでした。
   もし可能であれば、同じ設定で**`GEMINI_RPM_LIMIT=60`に揃えて**
   再実行いただけると、`masking_holdback_words`の本当の効果を
   確認できます。
3. **Deepgram SDKアップグレードの検討(新規)**: Flux
   (`flux-general-en`、内蔵end-of-turn検出)を使うには
   `deepgram-sdk`をv5系からv7系以降へ、`/v1/listen`から
   `/v2/listen`への移行が必要です。これは本番パイプラインにも
   関わる変更のため、リサーチエージェントのループ内で自動実装
   するのではなく、人間の判断で独立したプロジェクトとして検討
   いただくことを推奨します。

## 次のサイクルでやること

- 環境(特にDeepgram Listen WebSocket)が復旧していれば、
  `h-masking-holdback-rpm-matched-retest`を最優先で実行する。
- 復旧していなければ、`h-gemini-only-masking-replay`
  (Deepgramを使わない$0のリプレイハーネス実装)に着手する ──
  これは今サイクルで実装まで手が回らなかったが、ブロック中の
  仮説群の中で唯一、今すぐコードを書けば進められるもの。
- バックログ(queued/proposed)は4件(上限6件)で、まだ2件分の
  余地がある。

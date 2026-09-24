# LLM2024_8_part1 全字幕 人手レビュー（2026-09-24）

対象: `experiments/20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json`
（`experiments/caption_demos/llm2025_ep8_zenhan_full_lecture_captioned.mp4/.srt`）

## 1. レビューコメント（原文そのまま）

```
JoshAnguo
Muscle Lab

AIMEエージェント -> AI

ちょくちょく早いところがある（たまってまとめて、みたいな場面。）
重複した表示も結構ある

Agenda -> Agent
Suspension-Agent -> suspicion agent

たまに翻訳飛ばし-> API limit?
Chinchillaとかも, confidenceの問題？

文脈はちゃめちゃ
変なところでの改行

Gemma -> gemini
ドロップされてる節がある？

…。そして、
こういうのは次のに回してまとめれば良いのでは？（次のプロンプトに託す）
```

## 2. 原因の特定（実データでの根拠つき）

`seg` は元 run の `results.segments` の index。

| # | 症状 | 実データでの根拠 | 根本原因 |
|---|---|---|---|
| A | 重複表示 | seg 59/60「Gemma 3DやGemma 4関連など。はい。」→「Gemma 3DやGemma 4関連など」、61/62, 63/64, 65/66 …。**全1209セグメント中177（14.6%）が、既に出した音声区間を再度字幕化** | `deepgram_client.py` の重複防止が「単語数カウント」だけで、発話ごとにリセットされる。`UtteranceEnd` で pending interim を flush → 同じ音声の Deepgram `is_final` が遅れて届く → 状態リセット済みなので全文を新しい発話として再送。Deepgram が末尾語（"Okay."）を次セグメントに移した場合も同様 |
| B | JoshAnguo / Muscle Lab / Suspension-Agent / Yanxi | seg 10 "Josh Angou", 12/126/246 "Muscle Labs", 246 "suspension agent"→サスペンション・エージェント, 215 "Yanxi" | (1) 辞書114語 > 80 で dynamic mode。**ASRテキストに原語が文字通り出現した語しかプロンプトに入らない**ので、聞き間違えた名前は正しい辞書エントリを引けない。(2) プロンプト rule 4 が「固有名詞は出現した通りに保持」を強制し、LLMによる訂正を禁止していた |
| C | AIMEエージェント | seg 44 "less in AIME agents"（conf 0.84） | 辞書の `AIME`（ベンチマーク名）が Deepgram keyterm に入っており "AI …" を AIME に寄せている疑い。この講義に本物の AIME の言及はない（全ASR中の AIME はこの1件のみ） |
| D | Agenda→Agent | seg 97 "here is the today's agent"→「本日のエージェントです」 | ASR誤認識。講義中に "agent" が頻出するため |
| E | Chinchilla / 翻訳飛ばし | seg 221 "people from Deepgram, it it is a real set."（conf **0.47**）→「Deepgramの人たちによれば、これは本物のセットで」 | Chinchilla/DeepMind の箇所の ASR が崩壊（conf 0.47 → `[uncertain: …]` で慎重訳）。**API limit ではない**: 翻訳が空のセグメントは 0/1209、タイムアウトの記録もない |
| F | 「…。そして、」 | seg 47「そして」, 56「…貢献し、そして」, 64「また、私の…を紹介します」。**86/1209（7.1%）が接続詞か「…」で終わる** | ASR final が "And" / "introduce my" のような機能語で終わると、それ単独で1枚のカードになる。rule 3 は「断片は継続形で訳す」ことを求めるので、訳が「そして、」で終わる |
| G | ちょくちょく早い | **カードの25.1%が表示1秒未満** | A と F で「そして」「私の」「はい。」のような極小カードが増え、読み取り時間の backlog が詰まる → adaptive scheduler が最短 0.83 秒まで圧縮 |
| H | 変なところでの改行 | 「主にGeminiとGemm / aに貢献し」「GoogleRes / earchAustrali / a」。**Latin語の途中での改行46件、スペース消失25件** | `subtitle_formatter.py` が空白を全部削除（`re.sub(r"\s+", "", …)`）し、Latin語境界を無視して13文字で機械的に折り返していた |
| I | Gemma↔Gemini | seg 934 "regarding Gemini or Gemini" | ASR誤認識（keyterm に両方あり）。音声を聞かないと正解が確定できないので**未対応** |
| J | ドロップされてる節 | 過圧縮（訳の文字数/英単語数 < 1.0）は 0.5% のみ | 主因は過圧縮ではなく、G の1秒未満カード（見えない）＋ E の低信頼度区間と推定 |
| K | 文脈はちゃめちゃ | — | A の重複で文脈窓（直近3発話）が同じ文で埋まる ＋ F の断片化。A/F の修正で改善を期待 |

## 3. 仮説と改良

| ID | 仮説 | 実装 | 検証方法 |
|---|---|---|---|
| H1 `h-review-asr-reemission-watermark` | 単語タイムスタンプに基づく「送出済み音声位置 watermark」（発話をまたいで保持）で、送出済み単語を落とせば重複表示（A）がほぼ消える | `deepgram_client.py`: `TranscriptionResult.words`、`_take_unemitted()`、`_emitted_until` | ライブ run の `overlap_duplicate_rate` |
| H2 `h-review-misheard-term-aliases` | 辞書に ASR 聞き間違い alias 列を足し、長い複数語の用語はファジー検索し、rule 4 に「ASR誤りなら辞書形に直す」例外を足せば B が直る | `dictionary.py`（4列目 `aliases`、ファジー検索 ≥0.8、先頭文字一致）、`llm_translator.py` rule 4 例外 + rule 9（「…」禁止）、セッション辞書に alias を追加。DeepMind/Chinchilla/Agenda も追加 | 翻訳のみ A/B replay ＋ ライブ run |
| H3 `h-review-dangling-connective-carry` | ASR final 末尾の接続詞・機能語（and/so/my/to …、最大3語）を次の final の先頭に回せば（あなたの「次のプロンプトに託す」案）、F が減る | `pipeline.py`: `_split_dangling_tail` / `_apply_dangling_carry`、`DANGLING_CARRY_ENABLED`（既定 on） | ライブ run の `dangling_end_rate` |
| H4 `h-review-caption-linebreak-tiny-merge` | 空白を Latin 語間だけ残し、Latin 文字を半角幅 0.5 で数え、Latin 語の途中で改行せず、句読点を行頭に置かない（行頭禁則）。さらに4文字以下のカードは2秒以内の次カードに統合する。これで H と G が減る。**リアルタイム表示にも同じ規則を適用する**: 旧オーバーレイは翻訳結果が届くたびに即座に表示を差し替えていたため、backlog が一気に流れると各字幕が数十 ms で消えていた | `subtitle_formatter.py`（SRT）＋ **`captions/live_presenter.py`（リアルタイム）**: 同じ折り返し・極小カード統合・読み時間スケジューラ（backlog 時は最短表示時間まで圧縮）を実時間で行う。`gradio_demo.py` はオーバーレイに整形済みカードを送り、ストリーミング途中の訳はキューが空のときだけ出す。`web/overlay.html` は `word-break: normal; overflow-wrap: break-word; line-break: strict` に変更 | 各 run のイベントを実際の到着時刻どおりに再生し、旧オーバーレイと presenter を比較（`caption_issue_metrics.live_overlay_metrics`） |
| H5 `h-review-short-acronym-keyterm-false-positive`（提案のみ） | 短い大文字略語の keyterm（AIME 等）が一般語（AI）への誤認識を誘発している | 未実装 | ASR のみ A/B（keyterm に AIME あり/なし） |
| H6 `h-review-perceived-drops-short-cards`（提案のみ） | 「翻訳飛ばし・節のドロップ」の知覚は、API制限ではなく1秒未満カードと低信頼度 ASR 区間から来ている | 未実装 | 1秒未満カードと人手で「飛んだ」と感じた箇所の突き合わせ |

## 4. 結果

### 4.1 ライブ再実行（同じ区間、同じ設定）

`experiments/20260924_llm2024_8_part1_review_fixes_v2_7m30_21m50.json`（07:30–21:50、860秒、リアルタイム）と、
元 run の同じ区間（450–1310 s）を `caption_issue_metrics.py` で比較。

| 指標 | 修正前 | 修正後 (v2) |
|---|---|---|
| 重複セグメント率 | 13.6% (33/243) | **3.8%** (9/234) |
| 接続詞/「…」で終わる訳 | 8.6% (21) | **3.0%** (7) |
| 4文字以下の極小訳 | 4.1% (10) | 3.0% (7) |
| 翻訳が空のセグメント | 0 | 0 |
| JoshAnguo / Muscle Lab / Suspension / Yanxi の誤り | 1 / 3 / 1 / 1 | **0 / 0 / 0 / 0** |
| AIME の誤挿入 | 1 | 1（未解決、H5） |
| 旧オーバーレイで表示 <0.5 s の字幕 | 4.4% | 3.7% |
| 新 live presenter で表示 <1 s の字幕 | — | **0%**（平均遅れ 1.71 s, p95 4.55 s） |
| End-to-end 遅延 平均 / 中央値 | 5.65 / 6.28 s | 5.89 / 6.39 s |

修正後の主な訳:
- 458s "My name is Josh Angou." → 「Jiaxian Guoです。」
- 469s "Muscle Labs invitation" → 「松尾・岩澤研究室の招待に感謝します」
- 766s "here is the today's agent" → 「こちらが本日のアジェンダです」
- 1191s "Yanxi will introduce a scheme law" → 「Yanqi ZhouがScaling Lawを詳説します」
- 1209s "people from Deepgram" → 「DeepMindの人々による」
- 1294s "suspension agent in muscle lab" → 「松尾・岩澤研究室でSuspicion-Agentを開発しました」
- 599s "AIME agents" → 「AIMEエージェント」（**未解決**）

遅延は平均 +0.24 s。ぶら下がった機能語を次へ回す分（H3）と dedup の処理が入るため想定内だが、1回の run なので誤差の範囲かは未確定。

### 4.2 重複の残り 3.8% の原因と v3 修正

残った 9 件のうち 7 件は同じパターン（"Hello, everyone." の後に "everyone. Yeah. My name…"）。
07:30 の音声 20 秒を Deepgram に流して生メッセージを記録したところ、
**Deepgram は同じセグメントの `is_final` より前に `UtteranceEnd` を送ってくる**ことがわかった。
その上、final 側の単語タイミングは interim より幅が広い（"everyone." が 8.25–8.57 → 8.41–8.97）ので、時間ベースの watermark では取りこぼす。
v3 では、UtteranceEnd で flush したセグメントが再び来たときに消費済み単語数を復元する。
その 20 秒で重複が消えたことを確認した（"Hello, everyone." → "Yeah. My name is Josh Angou."）。unit test も追加した。
**全区間での v3 計測は、ローカルのネットワーク断（DNS 失敗 → Deepgram WebSocket 切断）で2回中断し未実施。**

### 4.3 翻訳のみの A/B（ASR 固定）

`experiments/20260924_term_alias_replay.json`: 報告のあった 10 セグメント × 3 回:
正しい用語 30% → **90%**、誤った用語 70% → **10%**。Gemini/Gemma などの対照 4 セグメントは 100% のまま変化なし。

### 4.4 字幕整形（既存 run の再レンダリング、全講義）

Latin 語の途中での改行 46 → **0**、Latin 語間スペースの消失 25 → **0**、表示 <1 s の字幕 25.1% → 22.6%（SRT/動画用の 13 文字 × 2 行設定）。

### 4.5 別途わかったこと

- ネットワーク断のとき `video_segment` ランナーは終了せずハングした（1 時間 40 分）。Deepgram WebSocket が切れた後の再接続・終了処理がない。リアルタイム運用でも同じことが起きうるので、次の課題とする。

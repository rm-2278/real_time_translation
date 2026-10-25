# サイクル74 結果レポート(2026-10-25, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`HUMAN_APPROVAL`状態でした(サイクル73
の`GENERATE_HYPOTHESES`が`h-dictionary-term-compliance-leakage`を
追加した直後)。今回はWebSearch/文献調査は行わず、
`HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`
のみを実行しました(PLAYBOOKの「1サイクルにつき境界のある1単位の作業」
方針に従い、新しい文献検索には踏み込んでいません)。

### HUMAN_APPROVAL: `h-dictionary-term-compliance-leakage`を自動承認

`check-budget(0.0)`は`AUTO_APPROVE`、`uses_existing_clips=true`の
通常経路に従い、`approval: auto_approved` / `status: queued`に更新
しました。この書き込みは今回、環境の権限分類器から拒否されません
でした(サイクル36/68で確認された「自己承認ブロック」は、常に発生する
ものではなく、特定の状況でのみ働く安全策のようです)。

もう1件の`proposed`(`h-judge-repeat-independent-batches-corrected-design`、
コスト$0.35)は、サイクル68時点で実際にこのブロックに遭遇し
`needs_human`として`pending_approval.json`に登録済みのため、
PLAYBOOKの`WAITING_APPROVAL`節の指示(人間の明示的な signal がない限り
再試行しない)に従い、今回も手を付けていません。rm-2278様の確認を
お待ちしています。

### RUN_EXPERIMENTS: `dictionary_compliance_checker.py`を新規実装

`h-translation-source-residue-detector`(サイクル73)の出力を読み直した
際に見つかった「`dictionary.csv`に正規の日本語訳があるのに、英語の
ソース語がそのまま漏れて出力されるケース」(例:"language model" →
本来「言語モデル」のはずが"language modelの効率性について"のように
英語のまま)を定量化するスクリプトです。`dictionary.csv`の46件中、
target_termに日本語文字を含む「翻訳対象」エントリ30件のみを対象とし、
意図的に英語のままの残り(RoPE、Chain of Thoughtなど)は除外しました。
ライブAPI呼び出しは一切なし(費用$0)。

実験JSON 145件のうち70件で該当エントリの出現があり、合計458件の
出現を検出:

| source_term | target_term | 出現数 | compliant | leaked | ambiguous | compliance rate |
|---|---|---|---|---|---|---|
| attention | アテンション | 133 | 110 | 20 | 3 | 84.6% |
| language model | 言語モデル | 317 | 262 | 9 | 46 | 96.7% |
| backpropagation | 誤差逆伝播 | 1 | 1 | 0 | 0 | 100% |
| classification | 分類 | 7 | 7 | 0 | 0 | 100% |
| **全体** | | **458** | **380** | **29** | **49** | **92.9%** |

(他26件の「翻訳対象」エントリは、今回対象とした実験クリップ群では
一度も発話されていませんでした。)

### ANALYZE_RESULTS: 仮説は確認、ただし実装中に本物のバグを発見・修正

結論から言うと**予測は確認されました**: 辞書への準拠率は全体として
高い(92.9%)ものの、100%ではなく、2つのエントリ(attention、
language model)に漏れが集中しています。さらに予測の第二部分
(「用語が長い句に埋め込まれているときに準拠率が下がる」)も裏付け
られました: attentionの漏れのほぼ全てが"self attention"という
複合語の一部として出現したもので、単独のattentionではなく、語全体
としては辞書に登録されていない複合語に埋め込まれた状態で漏れていました。

ただし、最初の実装では**本物の検出バグ**がありました。Pythonの正規
表現の`\b`(語境界)は、日本語のひらがな・カタカナ・漢字も「単語文字」
として扱うため、英語の単語の直後に空白なしで日本語が続く場合
(例:"attentionを行います")に境界が検出されず、本来「漏れ」である
事例が「ambiguous」に誤分類されていました。これにより、修正前は
leaked=6件(全体のcompliance rate 98.5%)という、実際より良く見える
結果になっていました。修正後(ASCII文字のみを対象にした独自の境界
判定に変更)、leaked=29件(92.9%)という、より正確で悪化した数字に
なりました。これはPLAYBOOK.mdが繰り返し指摘している「経験的に確認
せず抽象的に推論しない」という教訓(サイクル13/17/20/66/73の先行例)
と同じパターンで、今回はANALYZE_RESULTS中にambiguous分類の具体例を
目視確認したことで発見できました。

副次的な観察: "language model"の漏れ9件中7件が1つの実験
(`llm2025_ep8_zenhan_full_lecture_v2_dict`)に集中しており、これは
その講義クリップがほぼ全編"language model"について話しているという
話題密度の問題であり、別のメカニズムを示すものではないと考えられます。

仮説は`tested`としてクローズしました。具体的な次の一手(今回は実装
していません、$0 retroactive分析の範囲外):

1. "self attention" → "セルフアテンション"のような複合語を
   `dictionary.csv`に個別エントリとして追加する。
2. システムプロンプトに「辞書用語が長い句の一部であっても訳語を
   優先する」という指示を追加し、効果を検証する。

## 人間への確認が必要な項目

- 前回から継続: `h-judge-repeat-independent-batches-corrected-design`
  が`needs_human`のまま待機中です。`pending_approval.json`を参照し、
  承認いただければ次サイクルで実行します。
- 新規: 今回見つかった辞書漏れ(attention/language modelの複合語
  埋め込みパターン)への対応(上記1または2)を、将来の仮説として
  追加すべきか、ご判断をいただければ検討します(緊急性は低いです)。

## 環境メモ

今回のクラウド実行環境では`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`の
到達性チェックは行いませんでした — 選んだ仮説がライブAPIを一切
必要としないretroactive分析だったためです。`uv run`/`uv sync`は
PLAYBOOK.md記載の既知の問題(zoom extraの`rtms`パッケージが
`test.pypi.org`でブロックされる)に再度遭遇したため、
`python3.13 -m venv .venv && uv pip install -e ".[experiments]"`の
回避策を使用し、`ruff`も`uv pip install ruff`で個別インストール
しました。次回ライブ実験が必要な仮説を選ぶ際は、改めて
Deepgram/Gemini到達性を確認してください。

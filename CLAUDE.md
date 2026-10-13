# Experiment Logging Rules

翻訳精度の比較実験を行ったら必ず以下を実行すること：

1. `experiments/YYYYMMDD_<実験名>.json` を作成
2. `experiments/results.csv` に1行追記（なければ新規作成）
3. `git add` してコミット

## Recommended Tooling

このリポジトリには、YouTubeの指定区間（例: 10:00–20:00）を入力として
Deepgram → LLM 翻訳のパイプラインを回し、**確定(=final)のASR + 翻訳**を
JSON/CSV に保存するランナーがあります。

### Install

```bash
uv sync --extra experiments
```

### Run (default: 10:00–20:00)

```bash
uv run real-time-translation-exp-youtube \
  --url "https://www.youtube.com/watch?v=JycsHP-sGmw" \
  --start 10:00 \
  --end 20:00 \
  --name youtube_10m_to_20m \
  --domain particle_physics
```

Outputs:
- `experiments/YYYYMMDD_<name>.json`
- `experiments/results.csv`

### Optional: chrF

参照訳（正解テキスト）を `--reference-ja` に渡すと、翻訳全体に対して chrF を計算します。

```bash
uv run real-time-translation-exp-youtube --reference-ja reference_ja.txt
```

## Caption Reading-Speed Gate

字幕の表示を変えた／字幕付き動画を作ったら、必ず次を実行する：

```bash
uv run python -m real_time_translation.experiments.caption_issue_metrics <experiment.json> --fail-on-anomaly
```

`caption_anomalies` が空でなければ（8 units/s 超のカードがある、3秒以内に4枚以上
出る）異常。数字を報告するだけで終わらせず、`captions/subtitle_formatter.py` を
直して再計測し、前後の値を示すこと。

## Notes

- `ffmpeg` がホストに必要です。
- YouTube等のコンテンツは、取り扱い権限/利用規約に従ってください。

# Autonomous Research Agent

`research_agent/` に、文献調査→仮説生成→実験→分析→レポートを自律的に回す
リサーチエージェントの状態機械があります。新しいセッション（特にスケジュール
実行されたセッション）がこのリポジトリで研究サイクルを再開する場合は、必ず
**`research_agent/PLAYBOOK.md` を最初に読んでください**。現在の状態は
`python3 research_agent/orchestrator.py status` で確認できます。

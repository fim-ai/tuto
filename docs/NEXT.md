# 当前状态与下一步（2026-07-15）

给 compact 后接续用。运行态 + 待办，读完即可继续。

## 运行态（数据都在 fim-ai）

- 全部管线在 `fim-ai`（4090，纯 CPU+大盘）跑。数据 `fim-ai:/mnt/data/tuto/data/`。本地写码 `rsync` 过去执行。
- GROBID 常驻 `lfoppiano/grobid:0.8.1` @ 8070（`sudo docker`，密码在会话历史，别写进文件）。
- DBLP 索引：`fim-ai:/mnt/data/tuto/data/cache/dblp/dblp.sqlite`（860万条，2.4GB）。
- Cito key 在 `tuto/.env`（gitignored），限速已提到 6000/min。反查索引已由用户建好，`/paper?doi=`、`?arxiv=` 生效。
- run 目录 `data/runs/acl-2026/`：papers.jsonl / refs.jsonl(209,760条) / contexts.jsonl / verdicts.jsonl / cito_cache.jsonl(可续跑) / parse_report.json / qa_sample.csv。

## 已完成

- **D1**（ingest+parse）已 commit `2404585`。4,459 篇，GROBID `processReferences` 抽 209,760 条引文。
- **D2**（verify L1）已 commit `d79f9e8`。四分类 + DBLP + Cito(id直查+raw rescue)。

## 正在跑

- verify 重跑（id 直查 + raw rescue，处理 48k 残留）。`ssh fim-ai-internal "tr '\r' '\n' < /mnt/data/tuto/data/runs/acl-2026/verify.log | grep -vE ':\s+[0-9]+%' | tail"` 看结果。
- 命令：`cd /mnt/data/tuto && set -a; . ./.env; set +a; uv run tuto verify --venue acl-2026`。

## 下一步：D2-part-2 triage 漏斗（未写，`src/tuto/triage/` 空）

verify 后残留预计 2-3 万条 not_found，**不是最终 suspect**。构成（上一版实测）：35.6% 带 id（已被 id 直查消化）、46% 无年份=解析噪音、仅 1.2% 是 2026 新论文（快照滞后不是问题）。要把 2-3 万压到几百再上 LLM：

1. **rescue.py 二轮**：对 not_found 做**作者+年份模糊反查**（不只标题子串）。当前 L1 只在标题精确/子串命中，漏了「标题解析烂但作者+年份对得上 DBLP/Cito 某条」的。方向：DBLP 按作者姓氏+年份取候选，标题做 token Jaccard/编辑距离阈值；Cito 用 `author=` 参数 + 年份门 `published_before`。
2. **llm_judge.py**：rescue 后剩几百条，带证据链让 Haiku 判 confident-fake / needs-human（~$5，走 uniapi，见全局 CLAUDE.md 温度注意）。
3. **sample_qa.py**：随机抽样导出算 precision±区间。
4. 口径红线：报告只发聚合数字；minor-mismatch 单列不计入「幻觉」；公开数=末端 suspect×抽检 precision 校正。

## 其余待办

- 解析验收人工活：`qa_sample.csv` 30 篇（15 control + 15 probe）人工数真实引文条数 → GROBID 召回率±区间。约 1 小时，不阻塞。
- `tuto-app`（private monorepo）D3 才建。
- Cito 迭代已全落地；若 triage 要 batch id 直查，用 `/paper/batch`（dois[]/arxiv_ids[]，上限每批看 /search/batch 是 50）。

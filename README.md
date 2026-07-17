# Tuto

> cito, **tuto**, iucunde — 快、稳、愉悦。Cito 管找到文献，Tuto 管引得对。

学术引文审计管线 + 顶会引文诚信公开报告。首战：ACL 2026 论文集（4,459 篇，主会 + Findings）。

*Tuto is a citation-integrity audit pipeline. We checked all 209,985 references in the ACL 2026 proceedings: existence (L1) and, for claim citations, whether the cited paper actually supports the claim (L2). Full report at [tuto.fim.ai](https://tuto.fim.ai); English version at [/report](https://tuto.fim.ai/report).*

- 站点：[tuto.fim.ai](https://tuto.fim.ai)（公开报告，中英双语）；单篇自助核查工具在路上
- 报告源文件：`docs/REPORT-acl-2026-draft.md`（中文）/ `docs/REPORT-acl-2026-draft.en.md`（English）
- 数据集：[`dataset/`](dataset/)（引用级判定 18,724 + 3,795 条、仲裁记录 169 条，已匿名化，CC BY 4.0，schema 见其 README）
- 文档：`docs/PRD.md`（功能书）、`docs/ARCHITECTURE.md`（技术架构）

## 结果速览（ACL 2026 全量）

| 指标 | 数字 |
|---|---|
| 审计引文总数 | 209,985 条（4,459 篇） |
| 证实不存在的引文 | 2 条（0.001%）：捏造不是主要问题 |
| 至少含 1 条「证实不支撑」引用的论文占比 | 16%：支撑度才是 |
| 我们自己的一审精确率 | 13%（公开发表，不藏）：误报是检测工具的头号敌人 |

## 为什么必须自己解析 PDF

参考文献不在任何元数据里——`anthology.bib` 只有论文自己的元数据，不含它引了谁。二手引文库也拿不到：Crossref 对 ACL 论文的 `reference-count = 0`，OpenAlex 的 `referenced_works = 0`。

更根本的是，**这些库只存匹配成功的引文**。一条捏造的引文，本质上就是匹配不上、于是被静默丢弃的那条——用二手引文库找幻觉引文，逻辑上就找不到。所以必须从 PDF 读回作者真正写下的那串字。

## 仓库结构

| 目录 | 内容 |
|---|---|
| `src/tuto/` | 审计管线（ingest / parse / verify / triage / arbiter） |
| `docs/` | 报告全文（中英）、PRD、架构文档、人工复核记录（已匿名化） |
| `web/` | tuto.fim.ai 站点：报告 + [/check](https://tuto.fim.ai/check) 单篇自助核查页 |
| `tests/` | 解析与解析器守护回归测试 |

**单篇自助核查已上线**：[tuto.fim.ai/check](https://tuto.fim.ai/check) 丢一个 arXiv ID，跑与全量审计同一条管线（存在性 + 论断支撑 + 仲裁），几分钟后返回「待人工复核的线索」。服务端在 `src/tuto/check/`（FastAPI，`pip install -e ".[api]"`），单机自部署：`uvicorn tuto.check.service:app` + 一个 GROBID 容器 + DBLP 快照。

## 管线一览

```
ingest → parse → verify(L1) → triage(自动复核漏斗) → arbiter(L2 仲裁) → report
```

人工只做三件事：抽检样本算误报率、终审 suspicious 清单（12 条，逐条查证）、挑选并匿名化报告案例。

## 用法

```bash
uv venv --python 3.12 && uv pip install -e .

# 采集：全量 bib + PDF（按 IP 限速约 1MB/s，4,459 篇约 3 小时，断点续传）
uv run tuto ingest --venue acl-2026

# 解析：GROBID → refs.jsonl + contexts.jsonl，并产出解析验收样本
uv run tuto parse --venue acl-2026 --grobid-url http://localhost:8070
```

GROBID 需常驻：`docker run -d --name grobid -p 8070:8070 lfoppiano/grobid:0.8.1`

换会场只改 `src/tuto/ingest/acl_anthology.py` 里的 `VENUES` 表加一行。

## 快速事实

- 数据源：ACL Anthology 直连（全量 bib + 规律 URL PDF）
- 解析：GROBID `processReferences`。**不用 MinerU**——ACL 的 PDF 是 LaTeX 直出的 born-digital，没有 OCR 需求；MinerU/OCR 只作 V2 上传工具的扫描件兜底
- 解析验收：一个与 GROBID 原理不同的版式计数器全量交叉比对（数悬挂缩进的顶格行），只对分歧样本做人工核对，算出召回率 ± 区间并公开
- L1 核验：本地快照优先（DBLP / arXiv / OpenAlex / Anthology bib + Cito 索引），Crossref/OpenAlex API 兜底
- 红线：公开报告只发聚合统计与匿名化案例，永不点名任何论文或作者；不做作者通知，不设申诉流程（详见报告 §3.4）

## 站点开发

```bash
cd web && pnpm install && pnpm dev   # http://localhost:5297
```

站点在构建时读取 `../docs/` 下的报告 Markdown，纯静态输出。

## License

- 代码：Apache-2.0（见 `LICENSE`）
- 报告文本（`docs/REPORT-*`）：CC BY 4.0

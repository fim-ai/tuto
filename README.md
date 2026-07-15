# Tuto

> cito, **tuto**, iucunde — 快、稳、愉悦。Cito 管找到文献，Tuto 管引得对。

学术引文审计管线 + 顶会引文诚信公开报告。首战：ACL 2026 论文集（4,459 篇，主会 + Findings）。

- 定位：fim.ai 学术产品群的品牌旗舰与获客引擎，收入预期为零
- 站点：tuto.fim.ai（报告 + 单篇自助工具）；API：api.tuto.fim.ai
- 文档：`docs/PRD.md`（功能书 v3）、`docs/ARCHITECTURE.md`（技术架构 v3）

## 为什么必须自己解析 PDF

参考文献不在任何元数据里——`anthology.bib` 只有论文自己的元数据，不含它引了谁。二手引文库也拿不到：Crossref 对 ACL 论文的 `reference-count = 0`，OpenAlex 的 `referenced_works = 0`。

更根本的是，**这些库只存匹配成功的引文**。一条捏造的引文，本质上就是匹配不上、于是被静默丢弃的那条——用二手引文库找幻觉引文，逻辑上就找不到。所以必须从 PDF 读回作者真正写下的那串字。

## 两仓库拓扑

| 仓库 | 可见性 | 内容 |
|---|---|---|
| `tuto`（本仓库） | public, Apache-2.0 | 审计管线 + 方法论 + 数据集脚本 |
| `tuto-app` | private | `web/` Next.js 站点 + `api/` FastAPI（/check、/repair） |

## 管线一览

```
ingest → parse → verify(L1) → triage(自动复核漏斗) → repair(L3, Cito) → report
```

人工只做三件事：抽检 150 条算误报率、挑 demo 案例、处理作者申诉（7 天窗）。

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
- 红线：公开报告只发聚合统计，永不点名；作者通知与发布解耦（7 天申诉窗，30 天后发跟进报告）

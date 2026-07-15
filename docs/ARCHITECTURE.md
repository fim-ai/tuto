# Tuto 技术架构

v3，2026-07-15。设计目标：换会场只写一个 adapter；人工只出现在漏斗最后一级。

v3 相对 v2 的改动（全部经实测推翻或确认）：仓库 3 → 2；MinerU 从必需项降为 V2 兜底；引文抽取改用 GROBID 的 `processReferences` 端点；解析验收从「人工抽 50 篇」改为「全量自动交叉比对 + 定向抽检」。

## 为什么必须自己解析 PDF

这是整个项目的立身之本，也是护城河，写在最前面。

**参考文献不在任何元数据里。** ACL Anthology 的 `anthology.bib` 每条 entry 是「这篇论文自己的元数据」（title/author/booktitle/doi/pages），不包含「这篇论文引了谁」。我们审计的对象是后者，它只存在于 PDF 正文末尾。

**二手引文库也拿不到，而且原理上不可能拿到**（2026-07-15 实测）：

| 源 | 对 `10.18653/v1/2026.acl-long.1` 的结果 |
|---|---|
| Crossref | 论文在库，但 `reference-count = 0` —— ACL 从不提交参考文献 |
| OpenAlex | `referenced_works = 0` |

更根本的一层：**这些库只存匹配成功的引文**。一条捏造的引文，本质上就是匹配不上、于是被静默丢弃的那条。用二手引文库去找幻觉引文，逻辑上就找不到——要找的东西正是那个库的定义所排除的。任何想拿 OpenAlex/S2 糊一个竞品出来的人，都会在这里撞墙。

`anthology.bib` 的真正用途是**当 L1 的本地索引之一**（验证「被引的那篇 ACL 论文存不存在」），不是引文来源。

## 总览：一条离线管线 + 两个在线服务

```
┌─ 离线管线（每届会议跑一次，repo: tuto，开源）─────────────────┐
│ ingest → parse → verify(L1) → triage(自动复核) → stats        │
│                                    │                           │
│                                    └→ repair 调 api 生成修复建议│
└────────────────────────────────────────────────────────────────┘
┌─ 在线服务（常驻，repo: tuto-app，闭源）───────────────────────┐
│ tuto.fim.ai      web/   Next.js：报告 /reports/*、工具 /check  │
│ api.tuto.fim.ai  api/   FastAPI：/check（import tuto 复用 L1） │
│                              /repair（连 Cito 1.48 亿索引）    │
└────────────────────────────────────────────────────────────────┘
```

## 仓库拓扑（两仓库）

| 仓库 | 可见性 | 语言 | 内容 |
|---|---|---|---|
| `tuto` | **public**, Apache-2.0 | Python 3.12 | 管线全部 + 方法论 + 数据集发布脚本 |
| `tuto-app` | **private** monorepo | TS + Python | `web/`（Next.js）+ `api/`（FastAPI） |

**为什么是 2 个而不是 3 个**：真正的边界只有一条——公开 / 私有。api 与 web 是同一个发布单元（Check 页面没有 `/check` 就是死页），拆成两个私仓等于两套 CI、两次部署、API 契约靠人肉同步。合成一个私仓，契约在同一棵树里。

**为什么 web 必须私有**：7 天申诉窗期间，报告正文和案例会先落进 web 仓；仓库公开 = 禁令期内容提前泄露，邮件模板里还有作者地址。

**为什么 api 用 Python**：`/check` 直接 `import tuto` 复用 L1 引擎，一套核验逻辑两个入口；用 TS 写就得实现两遍，两边必然漂移。

**域名不分家**：报告是流量入口、工具是转化出口，同域不同路由；分域会剪断漏斗并劈开 SEO 权重。

## 管线目录（repo: tuto）

```
tuto/
├── src/tuto/
│   ├── models.py          # PaperRecord / Reference / Context，全部 JSONL 落盘
│   ├── cli.py             # tuto ingest | parse | ...
│   ├── ingest/
│   │   └── acl_anthology.py   # 会场适配器（唯一随会场变化的层）；VENUES 表加一行即换会场
│   ├── parse/
│   │   ├── grobid_extract.py  # PDF → refs.jsonl + contexts.jsonl
│   │   └── refcount_check.py  # 独立版式计数器，筛查 GROBID 漏抽
│   ├── verify/            # L1 引擎（待建）
│   ├── triage/            # 自动复核漏斗（待建）
│   └── report/            # 聚合统计（待建）
└── data/runs/acl-2026/    # 每 run 一目录，manifest.json 固化快照版本
```

## 数据流（阶段产出全部落盘，断点可重跑）

```
papers.jsonl → pdfs/ → tei/{refs,full}/ → refs.jsonl + contexts.jsonl
            → verdicts.jsonl → rescued.jsonl → judged.jsonl → stats.json
```

## 解析层（实测结论）

**不用 MinerU。** ACL 的 PDF 全是 LaTeX 直出的 born-digital，有完整文本层，没有 OCR 需求。MinerU 是给扫描件和复杂版面用的 GPU 重武器，在这里既是杀鸡用牛刀，又平白拉进一个 GPU 依赖。GROBID 才是干这个的，且 ACL 论文就在它的训练分布内。MinerU / textin 的位置在 **V2 上传工具的扫描件兜底**：先探文本层，没有才走 OCR。（textin 全量 47,000 页约 ¥2,200，兜底路径上量极小，免费额度足够；不采购。）

**引文列表用 `processReferences`，不用 `processFulltextDocument`。** fulltext 端点会在部分论文上静默截断文献表（`2026.acl-long.120`：41 条只出 13 条），专用端点全部救回且从未更差。fulltext 只保留用于抽引用上下文；两个端点的引文编号体系不同，上下文靠原文串指纹回连到权威列表。

**`consolidateCitations` 必须关闭。** 让 GROBID 去第三方库解析引文，正是我们要测量的那件事本身，不能让它替我们做。

## 解析验收：为什么不是「人工抽 50 篇」

PRD v1 写的是人工抽 50 篇对照，成本是一整天，且只覆盖 50 篇。改为两级：

1. **全量自动交叉比对**（免费，覆盖 4,459 篇）：用一个与 GROBID 原理完全不同的计数器复核——ACL 文献表是悬挂缩进，条目首行顶格、续行缩进，于是数「栏内顶格行」就能数出条目数，不依赖任何模型，也不读文献内容。**两个工具以不同方式犯错，正是它的价值所在**。
2. **定向人工抽检**（约 1 小时）：`qa_sample.csv` = **15 篇随机（control）+ 15 篇适度分歧（probe）**。
   - control 是无偏随机样本，人工数出真实条目数即得 GROBID 召回率 ± 区间——这是公开的头条数字。
   - probe 是「layout 比 GROBID 多 3-40 条、且 layout 自身未爆炸」的论文，是真实漏抽的最佳线索，人工核对给出最坏情况的边界。

**为什么不按 |diff| 排序选样**（踩过的坑）：最初按最大分歧选，结果选出来的全是 **layout 计数器自己爆炸**的论文（数出 503 条「参考文献」，实为冲进附录），测的是错的工具。现在把 layout > 160 条的论文（全量 37 篇）程序判为计数器故障、排除在人工样本外并记录数量，不静默丢弃。

**版式计数器是筛查工具，不是判据。** 它两个方向都有误差（附录渗漏会多算，文献块识别失败会少算），所以它自己的数字不能当验收线；验收数字来自它筛出样本上的人工核对。

**全量实测（4,453 篇对比）**：GROBID 共抽出 **209,760 条参考文献**；中位数比值 1.0000，1,558 篇完全一致，70.7% 在 ±2 内。probe 组已定位到真实漏抽，含 6 篇 GROBID 返回 HTTP 204（零条）的解析失败——这些需 fulltext 端点兜底重跑。

## 数据源与限制（2026-07-15 实测）

| 源 | 方式 | 实测限制 |
|---|---|---|
| ACL Anthology bib | 全量 dump 一个文件（12MB gz / 89MB） | 无认证 |
| ACL Anthology PDF | 规律 URL 直下 | **按 IP 限总带宽约 1 MB/s**：并发 4 已打满，并发 12 反而更差。全量 4,459 篇约 9.6GB，约 3 小时 |
| DBLP / arXiv | dump / 快照建本地索引 | 免费无限制 |
| OpenAlex | 快照（季度）本地 + API | CC0，商用无碍 |
| Crossref | polite pool（带 mailto） | 单 DOI 10 req/s，免费 |
| Cito S2 索引 | tuto-api 内部直连 | 豁免已获得，可驱动对外服务 |

## 运行环境

- **fim-ai（4090）跑全部管线**：本机是 arm64，GROBID 官方镜像是 amd64，跑模拟会慢几倍；fim-ai 是原生 x86，`/mnt/data` 空 2.4T。代码本地开发，`rsync` 过去执行。
- GROBID：`lfoppiano/grobid:0.8.1`，Docker 常驻 8070（CPU 即可，不占显存）
- LLM 仲裁：Haiku via API，单届会议约 $5-20
- 数据落 `/mnt/data/tuto/data/runs/<venue>/`

## 复用剧本（下一个会场）

1. `VENUES` 表加一行（ACL 系零改动；OpenReview 系写一次 adapter 全通用）
2. `tuto ingest --venue emnlp-2026` → `tuto parse` → 后续阶段
3. tuto-app 新增一期报告页，自动生成跨会场纵向对比（引文诚信指数）

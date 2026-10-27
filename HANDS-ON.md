# Hands-on：client + server（step by step）

手动复现这套方案，或想逐步看清每个环节时用这份文档。想要最快的一键式验证，直接把 [`AGENTS.md`](AGENTS.md) 交给 AI Agent（如 Kiro）执行即可，见 [README 的"快速验证"](README.md#快速验证推荐交给-ai-agent)。

## Scope 与目标场景

**目标 demo 场景**：业务人员不需要数据工程、数据库或 BI 背景，在 Amazon Quick 里用自然语言就能查询、分析、可视化已经在 AWS 上的数据——Redshift 数仓、RDS PostgreSQL 数据库、Bedrock 知识库（RAG）。Agent 自己发现库表、写 SQL、检索文档并组织回答；数据库默认只读。

**覆盖范围**：三类数据源（Redshift / RDS PostgreSQL / Bedrock KB）× 两种接入方式。不涉及 Quick 原生数据连接（Dataset / SPICE），本 demo 只验证 MCP 这条路径。

本文分两种接入方式，按需求选一种或都做：

### 方式 A：Quick Local MCP（快速验证）

在 Quick Desktop 里配置本机运行的 MCP server（awslabs 开源，`uvx` 一条命令拉起）。

- 适合：个人、小团队、快速 demo。几分钟配好。
- 凭证：用本机的 AWS profile（`~/.aws`），每个人各自配。
- 网络：本机要能直达数据源。
- 配置模板：[`quick-mcp.example.json`](quick-mcp.example.json)；校验用 [`test/verify_mcp_json.py`](test/verify_mcp_json.py)。

### 方式 B：Quick Cloud MCP（企业最佳实践）

MCP server 跑在 Lambda 里，AgentCore Gateway + Cognito 做统一入口，Quick 用服务间 OAuth 自动换令牌。

- 适合：企业生产。管理员搭一次，用户登录即用。
- 凭证：集中在 Lambda 角色，终端用户机器上没有 AWS 凭证。
- 网络：Lambda 在 VPC 内私网访问数据源，不依赖个人电脑网络。
- 链路：Amazon Quick → Cognito 鉴权 → AgentCore Gateway → Lambda（awslabs MCP）→ Redshift / RDS PostgreSQL / Bedrock 知识库。

下面的 Server 搭建覆盖方式 B 的云端入口（方式 A 不需要 Server，直接跳到 Client 配置）；Client 配置两种方式都讲。

## Server 搭建（见 [`AGENTS.md`](AGENTS.md)）

按 `AGENTS.md` 的阶段依次执行，每个阶段末尾都有 Gate 验收命令，不要跳过。时间为实测参考（含等待资源就绪，不含排错）：

| 阶段 | 做什么 | 预估耗时 | 产出 |
|---|---|---|---|
| 阶段 1 | 创建数据源并注入数据：Redshift、RDS PostgreSQL、Bedrock 知识库 | 15–20 分钟（Redshift 3–5、RDS 5–10、知识库 2–3，多为资源创建等待） | 三个数据源可用，演示数据就绪 |
| 阶段 2（可选） | 本机直连验证（Local awslabs MCP） | 5 分钟 | 确认数据源能被 MCP 读到 |
| 阶段 3 | 企业入口：Cognito + AgentCore Gateway + 3 个 Lambda 目标 | 15–20 分钟（三个目标分别约 5–8 分钟） | `get_token.sh --check` 返回 HTTP 200、11 个工具 |

Server 端合计约 30–40 分钟（含资源等待，不含排错）。

开始前先确认四件事：AWS profile、区域（默认 us-east-1）、是否生产账号（不确定按生产处理）、同意创建会计费资源（Redshift Serverless、RDS、Lambda、S3 Vectors、Gateway）。详见 `AGENTS.md` 第 1、2 节。

## Client 配置（Quick 接入）

把搭好的 MCP 接进 Amazon Quick：Local 连接器（本机运行，几分钟配好）或云端 MCP 连接器（AgentCore Gateway + 服务间 OAuth，企业推荐）。预估 5–10 分钟（在控制台填连接器字段并同步工具）。图文步骤见讲解页：

<https://d13loqfldp8rtf.cloudfront.net/quick-mcp-demo/index.html#client>

## 验证到位的判据

| 检查 | 期望 |
|---|---|
| `agentcore-verify/get_token.sh --check` | HTTP 200，11 个工具；不带令牌返回 401 |
| Redshift：`public.demo_sales` 行数与总收入 | 2600 行，1197156.76 |
| 按产品类别统计收入 | Subscription 620058.00、Devices 374181.22、Smart Home 202917.54 |
| RDS：有哪些表 | `demo_products`、`demo_sales`，数字与 Redshift 一致 |
| 知识库检索 | 返回相关段落并给出处 |
| 让 Agent 删除 `demo_sales` | 被拒绝（默认只读） |

完成后按 `AGENTS.md` 第 9 节清理，避免持续计费。

# quick-mcp-aws-demo

**目标：让你快速验证 Amazon Quick + MCP + AgentCore 这套方案**——用自然语言在 Quick 里查询、分析、可视化 AWS 上的数据（Redshift、RDS PostgreSQL、Bedrock 知识库 RAG），凭证集中在云端、数据库默认只读。

验证通过后你会得到：

- 一个 AgentCore Gateway，经服务间 OAuth 暴露 **11 个工具**（Redshift 6、RDS 3、知识库 2）；未授权请求返回 401。
- Quick 里一个云端 MCP 连接器，业务人员用一句话就能查数、出图、检索文档。
- 一条可复现、可清理的链路：Quick → Cognito → Gateway → Lambda（awslabs MCP）→ Redshift / RDS / Bedrock KB。

本 README 是索引。最快的验证路径在下面"快速验证"，可直接取用的代码在"1. MCP 的 script"，分步操作在"2. Hands-on"。

---

## 快速验证（推荐：交给 AI Agent）

把 [`AGENTS.md`](AGENTS.md) 交给 AI Agent（如 Kiro），让它按阶段执行；它会建数据源、注入数据、搭好 Cognito + Gateway + Lambda，并给出每一步的验收命令。只用 AWS CLI 和脚本，不用 CDK / CloudFormation。

开始前确认四件事：AWS profile、区域（默认 us-east-1）、是否生产账号（不确定按生产处理）、同意创建会计费资源（Redshift Serverless、RDS、Lambda、S3 Vectors、Gateway）。

验证到位的判据：

| 检查 | 期望 |
|---|---|
| `agentcore-verify/get_token.sh --check` | HTTP 200，11 个工具；不带令牌返回 401 |
| Redshift：`public.demo_sales` 行数与总收入 | 2600 行，1197156.76 |
| 按产品类别统计收入 | Subscription 620058.00、Devices 374181.22、Smart Home 202917.54 |
| RDS：有哪些表 | `demo_products`、`demo_sales`，数字与 Redshift 一致 |
| 知识库检索 | 返回相关段落并给出处 |
| 让 Agent 删除 `demo_sales` | 被拒绝（默认只读） |

全程约 30 分钟（含排错）。完成后按 `AGENTS.md` 第 9 节清理，避免持续计费。

---

## 1. MCP 的 script

可直接复用的 MCP 连接器配置与服务端实现。

| 路径 | 内容 |
|---|---|
| [`quick-mcp.example.json`](quick-mcp.example.json) | Local 连接器配置模板（Redshift / RDS PostgreSQL / Bedrock KB），占位符换成你自己的值即可在 Quick Desktop 导入 |
| [`agentcore-verify/`](agentcore-verify/) | 云端 MCP server：Lambda 里运行 awslabs MCP，AgentCore Gateway + Cognito 做统一入口。含搭建、测试、清理脚本、IAM 策略模板，以及各目标的工具定义（`*/tools.json`） |
| [`scripts/`](scripts/) | 演示数据注入（`seed_*`） |
| [`test/`](test/) | 本机 MCP 验证（`verify_*_mcp.py` 冒烟测试、`verify_mcp_json.py` 校验 Local 连接器配置） |
| [`bootstrap/`](bootstrap/) | 创建 Redshift Serverless、RDS PostgreSQL、Bedrock 知识库及示例文档 |
| [`AGENTS.md`](AGENTS.md)、[`HANDS-ON.md`](HANDS-ON.md) | 从零搭建的 Agent 手册 / 人工分步操作（见"2. Hands-on"） |

要点：Local 用 `uvx` 一条命令拉起 awslabs MCP；Cloud 把 MCP 装进 Lambda，两套依赖分目录（awslabs server 需 mcp 2.x，Lambda 适配库需 mcp 1.x）。Gateway 的工具 schema 不接受 `enum`。

## 2. Hands-on：client + server（step by step）

手动复现或想逐步看清每个环节，见 **[`HANDS-ON.md`](HANDS-ON.md)**：Server 搭建（分阶段，含时间预估和 Gate）、Client 配置（Quick 接入）、验收判据。Server 端约 30–40 分钟，Client 配置约 5–10 分钟。

---

## 注意

- 所有资源命名为 `quick-mcp-demo-*`，会产生费用，用完按 `AGENTS.md` 第 9 节清理。
- 配置里的账号 ID、端点、网络编号均为占位符或演示值，使用前替换成你自己的。

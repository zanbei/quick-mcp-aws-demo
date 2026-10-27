# AGENTS.md：从零搭建企业 MCP Server（Redshift、RDS PostgreSQL、Bedrock 知识库）并接入 Amazon Quick

这份文档写给执行任务的 AI Agent（Kiro 等）。照着做，可以在一个空的 AWS 账号里建出数据源、注入演示数据、搭好 Cognito + AgentCore Gateway + Lambda，并在 Quick 里用自然语言查询。只用 AWS CLI、Bash 和 Python，不用 CDK 和 CloudFormation。

## 0. 完成的样子

| 项 | 完成标准 |
|---|---|
| Redshift | Serverless 工作组可用，`public.demo_sales` 有 2600 行，总收入 1197156.76 |
| RDS PostgreSQL | 实例可用，同样的两张表和数据，只读角色 `mcp_readonly` 已建，密码在 Secrets Manager |
| 知识库 | Bedrock 知识库已建，4 篇示例文档已摄取（状态 COMPLETE），能检索到出处 |
| 企业入口 | Cognito + AgentCore Gateway，3 个 Lambda 目标，`tools/list` 共 11 个工具 |
| Quick | 一个云端 MCP 连接器（服务间 OAuth）已建好，工具已注册 |

## 1. 执行规则（必须遵守）

1. 开始前先向用户确认四件事，没有答复不要动手：AWS profile、区域（默认 us-east-1）、是否生产账号（不确定就当生产）、同意创建会计费的资源（Redshift Serverless、RDS、Lambda、S3 Vectors、Gateway）。
2. 生产账号或名称含 `prod` 的资源：只读、不删、不改。需要写入或删除时先说明影响并等待用户明确确认。
3. 密钥、令牌、数据库密码、Client secret 不打印、不写进文件、不贴进对话。只允许进剪贴板（`pbcopy`）或 Secrets Manager。引用时只说名称。
4. 以下动作必须先问用户：任何 `delete`/`cleanup`、修改已存在的资源、关闭保护开关。本流程从不对外开放数据库端口。
5. 所有资源命名为 `<NAME_PREFIX>-*`（默认 `quick-mcp-demo`），打标签 `Project=<NAME_PREFIX>`。账号里已有同名栈时，换一个前缀（例如 `NAME_PREFIX=qmdt`）建一套独立的，不要删旧的。
6. `setup*.sh` 遇到资源已存在会直接失败。重跑前先检查是否已建好，不要靠删了重来绕过。
7. 每个阶段末尾都有 Gate（验收命令）。Gate 不通过就停下来，报告现象和原因，不要继续下一阶段。同一个办法失败两次，换思路并告诉用户。
8. 脚本的退出码不可全信：`bash -c "...; echo EXIT=$?"` 这类包装可能显示 0。一律以 Gate 的实际输出为准。
9. 如实汇报：哪些命令实际跑过、结果是什么、哪些没验证。不要把没验证的写成已验证。

## 2. 前置检查

```bash
aws --version        # v1 或 v2 都行；v1 要同时设 AWS_DEFAULT_REGION
uv --version         # 没有就：brew install uv
python3 --version    # 3.10 以上
zip -v | head -1
aws sts get-caller-identity --profile "$AWS_PROFILE"     # 确认账号，把账号 ID 告诉用户核对
```

权限：执行者需要能创建 IAM 角色、Lambda、Cognito、Secrets Manager、S3、S3 Vectors、Bedrock 知识库、AgentCore Gateway、Redshift Serverless、RDS、EC2 安全组和 VPC 终端节点。管理员权限风险大，能用范围小的角色就用范围小的。

每个新终端都要先导出：

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER="" NAME_PREFIX=quick-mcp-demo
```

`NAME_PREFIX` 在后面所有脚本里都要保持一致，所以放进同一个环境文件，每个新终端都先加载。

## 3. 环境变量约定

| 变量 | 含义 | 哪里来 |
|---|---|---|
| `AWS_PROFILE`、`AWS_REGION`、`AWS_DEFAULT_REGION` | 凭证与区域 | 用户确认 |
| `NAME_PREFIX` | 所有资源的名称前缀，默认 `quick-mcp-demo` | 用户确认；已有同名栈时换一个 |
| `RS_WORKGROUP`、`RS_DB`、`WG_ID` | Redshift 工作组名、库名、工作组 ID。`setup.sh`、`test_gateway.py`、`enable_write.sh` 都读它们 | `create_redshift.sh create` 的输出 |
| `RDS_ID`、`RDS_INSTANCE`、`RDS_DB` | RDS 实例名（两个变量填同一个值）、库名 | `create_rds.sh create` 的输出 |
| `KB_ID`、`KB_REGION` | 知识库 ID 与所在区域 | `bootstrap/state.env` |

用已有数据源（不是从零建）也可以：把上面变量指向现有资源，跳过对应的创建步骤，仍然要执行注入和后面的阶段。

## 4. 阶段 1：创建数据源并注入数据

### 1A Redshift

```bash
bootstrap/create_redshift.sh create          # 约 3 到 5 分钟，输出 RS_WORKGROUP / RS_DB / WG_ID
export RS_WORKGROUP=${NAME_PREFIX}-wg RS_DB=dev WG_ID=<上一步输出>
scripts/seed_redshift_demo.sh seed           # 建 demo_products、demo_sales，写入 2600 行
```

Gate：

```bash
ID=$(aws redshift-data execute-statement --workgroup-name "$RS_WORKGROUP" --database "$RS_DB" \
  --sql "SELECT COUNT(*), SUM(revenue) FROM public.demo_sales" --query Id --output text)
sleep 8; aws redshift-data get-statement-result --id "$ID" --query Records --output json
```

期望 `2600` 和 `1197156.76`。

### 1B RDS PostgreSQL

```bash
bootstrap/create_rds.sh create               # 约 5 到 10 分钟；加密、密码托管、永远不对外
export RDS_ID=${NAME_PREFIX}-pg RDS_INSTANCE=${NAME_PREFIX}-pg RDS_DB=demo
uv run --no-project --isolated --with boto3 python scripts/seed_rds_private.py seed
```

数据在 VPC 内部注入：脚本临时建一个带 psycopg 的 Lambda（放在实例所在的 VPC 和可用区），由它连库执行 SQL，结束后自动删除 Lambda 和角色。**不开任何入站端口，不依赖本机网络**。本机直连的办法（临时放行公网 IP）在 VPN 或代理环境下会失败：RDS 域名被解析成代理的假地址，连接被中途断开，所以不再使用。

Gate：脚本输出 `demo_sales rows / total revenue: (2600, Decimal('1197156.76'))`，并按类别列出 Subscription 620058.00、Devices 374181.22、Smart Home 202917.54。再确认实例没有对外开放：

```bash
aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --query 'DBInstances[0].PubliclyAccessible'   # 期望 false
```

只读角色 `mcp_readonly` 和密钥 `<NAME_PREFIX>/pg-readonly` 由 `seed_rds_private.py` 创建，后面的 Lambda 用它连库。脚本还会建好 `<NAME_PREFIX>-pg-lambda-sg` 及其对 RDS 5432 的放行规则，`setup_pg.sh` 会直接复用。

### 1C 知识库（RAG）

```bash
uv run --no-project --isolated --with boto3 python bootstrap/create_kb.py create    # 约 2 到 3 分钟
uv run --no-project --isolated --with boto3 python bootstrap/create_kb.py verify
set -a; . bootstrap/state.env; set +a        # 得到 KB_ID、KB_REGION
```

脚本做的事：建文档桶，上传 `bootstrap/kb_docs/` 的 4 篇文档；建 S3 Vectors 桶和索引（Titan Embeddings v2，1024 维）；建 Bedrock 用的角色；建知识库和数据源；摄取并等到 COMPLETE。S3 Vectors 没有常驻费用，所以不用 OpenSearch Serverless。

Gate：`verify` 两行都是 `OK`（退货问题命中 `return-policy.md`，Kindle 无障碍问题命中 `product-faq.md`）。

想用自己的文档：把文件放进 `bootstrap/kb_docs/` 再运行 `create`，或者自己建好知识库，只把 `KB_ID`、`KB_REGION` 交给阶段 3。

## 5. 阶段 2（可选）：本机直连验证

不需要 Gateway，用本机 awslabs MCP 先确认数据源能被 MCP 读到。

```bash
uv run --no-project --isolated --with 'mcp<2' --with boto3 python test/verify_redshift_mcp.py
uv run --no-project --isolated --with 'mcp<2' --with boto3 --with "psycopg[binary]" python test/verify_postgres_mcp.py
```

Quick Desktop 的 Local 连接器配置见 `quick-mcp.example.json`（占位符换成你的 profile 和 RDS 密钥 ARN），改好后用 `test/verify_mcp_json.py` 按 Desktop 的启动方式校验。

## 6. 阶段 3：企业入口（Cognito + Gateway + Lambda）

```bash
cd agentcore-verify
./setup.sh                      # Cognito、Gateway、Redshift Lambda 目标；约 5 到 8 分钟（需要 RS_WORKGROUP、RS_DB、WG_ID）
uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway.py

./setup_pg.sh                   # 需要 RDS_ID；VPC、子网、安全组从实例读取；约 6 分钟
uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway_pg.py

./setup_kb.sh                   # 需要 KB_ID、KB_REGION
uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway_kb.py

./get_token.sh --check          # 期望：HTTP 200，tools: 11
```

要点：
- 三个脚本共用一个 Gateway，新增数据源只加目标，不用重建 Gateway。
- 脚本里的名称都来自 `NAME_PREFIX`。
- 先由 `render_policies.sh` 把 `policies/` 里的模板渲染成 `.rendered/`，其中的账号 ID、区域、工作组 ID、知识库 ID 都是占位符。
- Redshift 目标默认只读。写入要显式开启：`enable_write.sh`，关闭用 `disable_write.sh`，先问用户并先拍快照。
- RDS 的 Lambda 在 VPC 内，通过两个关闭私有 DNS 的接口终端节点访问 Secrets Manager 和 RDS API，数据库只接受 Lambda 安全组的 5432。
- 知识库 Lambda 的角色只有对这一个知识库的 `bedrock:Retrieve`。

Gate：`get_token.sh --check` 返回 200 且 11 个工具（Redshift 6、PostgreSQL 3、知识库 2）；未带令牌的请求返回 401。

## 7. 阶段 4：在 Quick 里配置

先取要填的值。Client secret 只进剪贴板，不要显示：

```bash
GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='quick-mcp-demo-gw'].gatewayId | [0]" --output text)
aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --query gatewayUrl --output text
aws secretsmanager get-secret-value --secret-id quick-mcp-demo/cognito-client --query SecretString --output text \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); print("client_id:", c["client_id"]); print("token_url:", c["token_url"]); print("scope:", c["scope"])'
aws secretsmanager get-secret-value --secret-id quick-mcp-demo/cognito-client --query SecretString --output text \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["client_secret"], end="")' | pbcopy
```

在 Quick 的 Connectors 页，Create for your team，选 Model Context Protocol：

| 字段 | 填入 |
|---|---|
| Name | `AWS MCP Gateway` |
| Description | 英文短句，只用字母、数字、空格和 `_ . , ! ? -`，例如 `Query AWS data through an AgentCore Gateway.` |
| MCP server URL | 上面取到的 Gateway 地址（以 `/mcp` 结尾） |
| Authentication | Service-to-service OAuth |
| Client ID / Token URL | 上面取到的值 |
| Client secret | 粘贴剪贴板 |
| VPC connection | Public network（资源服务器和认证服务器都是） |

Review 页建议：能执行 SQL 的 `execute_query`、`run_query` 设为每次都要批准（Always ask）。发布前问用户要共享给谁。发布页提示服务间认证的连接不能在 Chat 或 Flows 里使用，Desktop 里通过 Agent 使用。Gateway 新增目标后要在连接器里重新 Sync，Quick 把工具列表当静态的。

## 8. 阶段 5：验收

让用户在 Quick 里问下面的问题，对照期望：

| 问题 | 期望 |
|---|---|
| Redshift 里 demo_sales 有多少行、总收入多少 | 2600 行，1197156.76 |
| 按产品类别统计收入，并显示 SQL | Subscription 620058.00、Devices 374181.22、Smart Home 202917.54 |
| 数据库（RDS）里有哪些表 | `demo_products`、`demo_sales`，数字与 Redshift 一致 |
| 在知识库里查：退货期限是多少天，给出处 | 30 天，出处 `return-policy.md` |
| 让 Agent 删除 demo_sales 里的数据 | 被拒绝（Redshift 默认只读，RDS 用只读角色） |

逐项报告通过或失败。失败时先看对应 `test_gateway*.py` 的输出，再看 Lambda 的 CloudWatch 日志。

## 9. 清理（每一步都先问用户）

按创建的反序，全部会计费：

```bash
cd agentcore-verify
./cleanup_kb.sh && ./cleanup_pg.sh && ./cleanup.sh
cd ..
uv run --no-project --isolated --with boto3 python bootstrap/create_kb.py cleanup
bootstrap/create_rds.sh delete          # 无最终快照，数据会丢
bootstrap/create_redshift.sh delete     # 无最终快照，数据会丢
```

`seed_rds_private.py` 自己会删掉临时 Lambda 和角色；中途被打断时运行 `scripts/seed_rds_private.py cleanup`。最后在 Quick 里手动删除连接器。`cleanup_pg.sh` 的接口终端节点和安全组释放有延迟，脚本会重试。

## 10. 已知坑

- awslabs server 需要 mcp 2.x，Lambda 适配器需要 mcp 1.x，所以依赖拆成两个目录（`server_pkgs`），子进程 `cwd=/tmp`，`PYTHONPATH=/var/task/server_pkgs`。
- Gateway 的工具 schema 不接受 `enum`。
- awslabs postgres server 不要在启动参数里加 `--db_type`，对 RDS 单实例会因缺少集群 ARN 崩溃；Lambda 版用自己的处理函数，一次调用里完成连接和查询。
- Lambda 包超过 50 MB，必须经 S3 部署。
- 知识库检索前确认数据源同步是 COMPLETE，同步失败时检索返回 0 条。
- macOS 自带 bash 3.2，没有关联数组（`declare -A`）。脚本要能在 `/bin/bash` 下运行，改完用 `/bin/bash -n` 检查。
- 不要把 Redshift 工作组名写死：`test_gateway.py` 曾写死 `default-workgroup`，换成新工作组后 Lambda 角色访问被拒。现在读 `RS_WORKGROUP`、`RS_DB`。
- Quick 的 Description 字段创建时只允许字母、数字、空格和 `_ . , ! ? -`。
- Quick Local 连接器的 Command 要用绝对路径（图形界面不继承终端 PATH），Timeout 填 120。
- awslabs 知识库 MCP 的 `ListKnowledgeBases` 只列出带标签 `mcp-multirag-kb=true` 的知识库，没有标签就返回空（不报错）。`create_kb.py` 建的知识库已带这个标签；自己的知识库要手动加：`aws bedrock-agent tag-resource --resource-arn <知识库 ARN> --tags mcp-multirag-kb=true`。`QueryKnowledgeBases` 带上知识库 ID 就能用，不依赖这个标签。Gateway 路径上的 Lambda 角色只有 `bedrock:Retrieve`，那里的 `ListKnowledgeBases` 不可用，用默认知识库查询即可。
- S3 Vectors 索引要把 `AMAZON_BEDROCK_TEXT` 设为不可过滤，否则摄取会因元数据过大失败（`create_kb.py` 已处理）。

## 11. 验证状态（如实）

2026-10-23 在演示账号（us-east-1）上，用前缀 `NAME_PREFIX=qmdt` 从零建了一套独立的栈，按本文顺序实际执行，结果如下。

| 步骤 | 结果 |
|---|---|
| `create_redshift.sh create` + `seed_redshift_demo.sh seed` | 通过：2600 行，总收入 1197156.76 |
| `create_rds.sh create` + `seed_rds_private.py seed` | 通过：同样的数字和类别汇总，实例未对外开放 |
| `create_kb.py create` + `verify` | 通过：4 篇文档摄取 COMPLETE，两条检索命中正确文档 |
| `setup.sh` + `test_gateway.py` | 通过：6 个工具，SELECT 返回三个类别，DELETE 被拒绝（transaction is read-only） |
| `setup_pg.sh` + `test_gateway_pg.py` | 通过：用户是 `mcp_readonly`，DELETE 被拒绝。第一次运行在 macOS bash 3.2 下失败（`declare -A`），修复后重跑通过 |
| `setup_kb.sh` + `test_gateway_kb.py` | 通过：不传知识库 ID 也能用默认知识库，返回相关文档 |
| `get_token.sh --check` | 通过：HTTP 200，11 个工具；不带令牌返回 401 |

跑的过程中发现并修掉的问题：`test_gateway.py` 写死了工作组名；`setup_pg.sh` 用了 bash 4 的语法；本机直连 RDS 注入数据在代理环境下失败（改成了 VPC 内注入）。

**没有验证的部分：**
- 所有清理脚本（`cleanup_kb.sh`、`cleanup_pg.sh`、`cleanup.sh`、`create_rds.sh delete`、`create_redshift.sh delete`）没有在这套新栈上跑过。`create_kb.py cleanup` 在另一套知识库上跑过。
- 这套 `qmdt` 栈目前还在运行（会计费），清理需要用户确认。
- `enable_write.sh`、`disable_write.sh`、`test_write.py` 改成按前缀和工作组取名后，没有重跑。
- Quick 里的连接器是在最初那套栈上建并验证的，没有用 `qmdt` 栈的值再建一遍。
- 只在 macOS 上执行过；Linux 和 Windows 没试。

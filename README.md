[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

[![Tests](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

把私有 API/SDK 文档和自然语言需求，转换成经过实际运行验证、可以继续修改、过程可追溯的 Python 脚本。

```text
描述需求 → 澄清并确认 → 检索文档 → 生成并核对实现方案
        → 生成代码 → 静态检查 → 实际执行 → 局部修改与复测
        → 用户验收 → 可选的同领域场景记忆
```

Doc2Run Agent 适合不熟悉某个内部 SDK、但需要完成查询、筛选、报表、配置检查或低频自动化的 Python 用户。它不是通用 Coding Agent，也不是面向不可信输入的安全执行服务。

## 核心能力

- 先把目标、输入输出、限制和成功标准问清楚，用户输入 `/confirm` 后才开始生成。
- API 文档、用户提供的领域资料和已验收的历史场景分开保存、分开检索。
- 生成代码前先写实现方案；方案核对仍未通过时不会继续生成。
- 代码经过语法和规则检查后实际运行，保存 stdout、stderr 和每轮代码。
- 运行失败或用户继续提出修改时，优先改动相关代码、核对修改、重新测试。
- 成功代码不会自动进入记忆。只有用户 `/approve` 后才会在新上下文中提取候选，并在格式检查、独立审查和 `/remember` 后保存到当前领域。
- session、需求版本、模型上下文、检索结果和运行产物均保存在本机，可使用相同 session ID 恢复。

## 安装

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/yf1y/doc2run-agent.git
cd doc2run-agent
python -m venv .venv
```

激活虚拟环境并安装：

```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -e .
```

需要运行测试时：

```bash
pip install -e ".[dev]"
pytest -q
```

## 准备项目目录

仓库中的 [`demo/`](demo/) 是空模板，不包含业务实例、示例请求或模拟 SDK。复制后只需修改路径和内容：

```bash
# macOS / Linux
cp -R demo my_project
cp my_project/.env.example my_project/.env

# Windows PowerShell
Copy-Item -Recurse demo my_project
Copy-Item my_project/.env.example my_project/.env
```

模板结构：

```text
my_project/
├── doc2run_agent.yaml          # 模型和调用参数
├── .env                        # 模型密钥，不提交到 Git
└── knowledge/
    ├── api/                    # 所有项目都需要：代码怎样调用接口
    │   ├── setup.md
    │   ├── api_reference.md
    │   └── usage_rules.md
    └── domains/                # 使用垂直领域时启用
        └── your_domain/        # 改成 --domain 使用的名称
            ├── docs/
            │   └── domain_knowledge.md
            └── memory_schema.json
```

在 `doc2run_agent.yaml` 中填写 LiteLLM 支持的模型名，在 `.env` 中填写对应密钥。模板中的 Markdown 只有填写说明；程序会忽略这些注释，用户没有放入真实接口资料时会直接报错。

三类知识不会混在一起：

- `knowledge/api/`：安装、import、函数签名、参数、返回值和调用限制，回答“代码怎么调用”。
- `knowledge/domains/<domain>/docs/`：术语、业务规则、布局、映射、拓扑和参数表，回答“这个领域什么结果才对”。
- `memory/approved/<domain>/`：用户验收、格式检查和独立审查都通过的历史场景，由程序管理，不要手工把接口文档放进去。

不要把真实密钥、生产连接串或敏感业务数据写进知识文档。

## 运行

```bash
doc2run-agent \
  --session my-project \
  --config my_project/doc2run_agent.yaml \
  --knowledge-dir my_project/knowledge \
  --domain your_domain
```

不使用领域资料和场景记忆时删除最后一行即可。程序启动时会打印实际加载的接口目录、领域目录和记忆目录，让用户确认材料没有放错位置。之后直接输入自然语言需求，不需要准备 `request.txt`。常用命令：

```text
/show             查看当前需求
/history          查看需求对话
/confirm          确认需求并开始生成、验证和执行
/approve [说明]   验收当前成功版本
/remember         保存已通过审查的场景候选
/reject-memory    拒绝场景候选
/reset            归档当前 session 并重新开始
/help             查看帮助
/exit             保存并退出
```

代码成功运行后，链路不会强制结束。你可以继续用自然语言要求修改，系统仍在当前代码上下文中完成修改、核对和复测；输入 `/approve` 后，代码修改阶段才结束。若启用了领域记忆，记忆候选会在与修改过程隔离的新上下文中生成。

完整操作说明见 [`使用文档.md`](使用文档.md)。

## 可选的领域记忆

不传 `--domain` 时，领域资料检索和场景记忆都关闭。需要时，填写模板中的：

```text
my_project/knowledge/domains/<domain>/docs/
my_project/knowledge/domains/<domain>/memory_schema.json
```

`docs/` 保存用户事先提供并可核对的领域事实；schema 固定以后允许从验收结果中保存哪些字段。接口签名、import、源码、凭证和修复过程不会作为场景知识保存。不同领域不会互相检索。

## 运行产物

```text
sessions/<session-id>/
├── session.json                 # 对话和当前阶段
├── decisions.md                # 用户确认和纠正
├── task_specs/                  # 已确认的需求版本
├── retrieval/                   # 每轮文档检索结果
├── planning/                    # 接口/领域/历史场景上下文及方案核对
├── contexts/                    # 实际模型输入输出
├── runs/                        # 代码、校验、stdout、stderr 和修改记录
└── workspace/generated.py       # 当前脚本
```

使用相同的 `--session` 可以继续过去的任务。`/reset` 会归档而不是直接删除旧记录。

## 安全边界

当前 Runner 提供执行前规则检查、精简环境和超时控制，但它不是操作系统级沙箱。不要把项目直接作为不可信用户可访问的公共代码执行服务；生产环境需要使用受限容器或虚拟机，并单独设计凭证注入、网络、文件系统和子进程权限。

## 项目结构

```text
doc2run-agent/
├── doc2run_agent/              # 全部项目源码
├── demo/                       # 可复制的空项目模板
├── tests/                      # 自动化测试
├── 使用文档.md                 # 从安装到使用的完整说明
├── README.md
├── README_EN.md
└── pyproject.toml
```

基于 [LangGraph](https://github.com/langchain-ai/langgraph) 和 [LiteLLM](https://github.com/BerriAI/litellm) 构建，采用 [MIT License](LICENSE)。

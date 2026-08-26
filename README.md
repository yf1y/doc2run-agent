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
- API 文档和已验收的场景知识分开保存、分开检索。
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
    └── api/
        └── api_reference.md    # 替换为自己的 API/SDK 文档
```

在 `doc2run_agent.yaml` 中填写 LiteLLM 支持的模型名，在 `.env` 中填写对应密钥。然后把 `knowledge/api/` 下的占位文档替换为真实资料。

推荐文档至少包含：

- 正确的安装和 import 写法；
- 类、函数、方法的完整签名；
- 参数类型、返回值结构和异常；
- 初始化、鉴权、分页、限流等调用要求；
- 可运行的最小调用片段；
- 会修改文件或远程数据的副作用。

不要把真实密钥、生产连接串或敏感业务数据写进知识文档。

## 运行

```bash
doc2run-agent \
  --session my-project \
  --config my_project/doc2run_agent.yaml \
  --knowledge-dir my_project/knowledge
```

启动后直接输入自然语言需求，不需要准备 `request.txt`。常用命令：

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

不传 `--domain` 时，场景记忆完全关闭。需要时，由使用者自己增加：

```text
my_project/knowledge/domains/<domain>/memory_schema.json
```

schema 固定该领域允许保存的字段。接口签名、import、源码、凭证和修复过程不会作为场景知识保存。不同领域的记忆位于 `memory/approved/<domain>/`，不会互相检索。

## 运行产物

```text
sessions/<session-id>/
├── session.json                 # 对话和当前阶段
├── decisions.md                # 用户确认和纠正
├── task_specs/                  # 已确认的需求版本
├── retrieval/                   # 每轮文档检索结果
├── planning/                    # 实现方案及核对结果
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

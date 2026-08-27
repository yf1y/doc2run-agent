[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

[![Tests](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[功能概述](#1-功能概述) · [安装方式](#2-安装方式) · [使用说明](#3-使用说明) · [项目结构](#4-项目结构)

## 1. 功能概述

Doc2Run Agent 将一个可复用的 Scene、私有 API/SDK 文档和自然语言需求，转换成经过实际运行验证、可以继续修改、过程可追溯的 Python 脚本。

```text
Chat   → 检索一个 Scene、澄清需求、形成并确认 Scenario Plan
Code   → 按方案检索 API、生成代码、静态检查并执行
Fix    → 根据报错或实现层修改请求检索 API、局部修复并复测
Memory → 用户验收后，只把确认方案保存为可复用 Scene
```

主要能力：

- 先确认目标、输入输出、限制和成功标准，输入 `/confirm` 后才开始生成。
- Scene 在 Chat 阶段按文档级检索只选一个，选中后注入完整文档，不混入其他 Scene。
- Chat 产出开放结构的 Markdown 场景方案，用户确认后原样传给 Code。
- Code/Fix 阶段只根据方案检索 API/SDK 文档；不会把 API 知识写回 Scene。
- 代码经过语法和规则检查后实际运行，保存 stdout、stderr 和每轮代码。
- 运行失败或用户提出修改时，优先做局部修改、复核并重新测试。
- 用户 `/approve` 后，已确认且成功运行的场景方案直接保存到 `scenes/`，不再维护候选、待审和拒绝三套中间状态。
- Memory 是确定性持久化阶段，不调用模型，也不配置独立模型。
- session、需求版本、模型上下文、检索结果和运行产物均保存在本机，可恢复查看。

适合不熟悉内部 SDK、但需要完成查询、筛选、报表、配置检查或低频自动化的 Python 用户。它不是通用 Coding Agent，也不是面向不可信输入的公共代码执行服务。

## 2. 安装方式

需要 Python 3.10 或更高版本：

```bash
git clone https://github.com/yf1y/doc2run-agent.git
cd doc2run-agent
python -m venv .venv
```

激活并安装：

```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -e .
```

运行测试：

```bash
pip install -e ".[dev]"
pytest -q
```

## 3. 使用说明

### 3.1 准备项目目录

仓库中的 [`demo/`](demo/) 是可复制的空模板，不包含业务实例、示例请求或模拟 SDK：

```bash
cp -R demo my_project
cp my_project/.env.example my_project/.env
```

Windows PowerShell：

```powershell
Copy-Item -Recurse demo my_project
Copy-Item my_project/.env.example my_project/.env
```

在 `my_project/doc2run_agent.yaml` 中填写 LiteLLM 支持的模型，在 `.env` 中填写模型密钥。不要把真实密钥放进 YAML、知识文档、自然语言请求或生成代码。

### 3.2 准备两类知识

```text
my_project/
├── doc2run_agent.yaml
├── .env
└── domain_knowledge/
    ├── api/                              # 代码怎样调用 API/SDK
    │   ├── setup.md
    │   ├── api_reference.md
    │   └── usage_rules.md
    └── scenes/                           # 场景知识和已验收场景
        └── scene_1.md
```

- `domain_knowledge/api/`：安装、import、完整签名、参数、返回值、异常和调用限制。
- `domain_knowledge/scenes/`：完整场景文档，包括器件、排布、连接、节点参数、不变量和泛化规则。

两类知识都支持 Markdown、TXT、JSON、JSONL、YAML 和 YML。Scene 以文件为单位排序；API 文档按片段检索。模板文件只有注释，API 模板必须替换成真实资料。

### 3.3 配置模型和运行

模型配置示例：

```yaml
models:
  defaults:
    model: your-provider/your-model
    api_key_env: MODEL_API_KEY
    timeout: 120
    max_retries: 3
    max_tokens: 4000
    context_tokens: 16000
  code:
    timeout: 180
    max_tokens: 5000
```

运行：

```bash
doc2run-agent \
  --session my-project \
  --config my_project/doc2run_agent.yaml \
  --knowledge-dir my_project/domain_knowledge
```

如果不传 `--session`，启动时会进入会话选择器：输入编号继续已有会话，输入 `n` 创建新会话，输入 `q` 退出；没有历史会话时会直接询问新会话名。传入已有的 `--session` 会直接继续它；传入不存在的名称时，程序会先询问是否创建，避免误开新会话。

如果生成代码需要私有 SDK 的环境变量，必须显式加入白名单；程序不会默认把模型密钥或其他环境变量传给生成代码：

```bash
doc2run-agent \
  --session my-project \
  --config my_project/doc2run_agent.yaml \
  --knowledge-dir my_project/domain_knowledge \
  --runtime-env SDK_API_TOKEN \
  --runtime-env SDK_ENDPOINT
```

只传入确实需要的变量，不要传 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等模型密钥。

### 3.4 交互命令

启动后直接输入自然语言需求。程序先选一个 Scene 注入 Chat；需求和场景方案整理完成后查看 `/show`，再输入 `/confirm`。随后它按方案检索 API、生成、检查和执行。

```text
/show             查看当前需求
/history          查看需求对话
/confirm          确认需求，或在补充资料后重新尝试
/approve [说明]   验收当前成功版本并直接保存 Scene
/reset            归档当前 session 并重新开始
/help             查看帮助
/exit             保存并退出
```

代码成功运行后仍可继续用自然语言要求修改；满意后再输入 `/approve`。如果补充了 API/Scene 文档，下一次 `/confirm` 或修改请求会刷新知识索引。

### 3.5 Scene 沉淀和安全边界

`/approve` 只保存确认后的场景方案 Markdown，不保存 API 签名、import、源码、凭证、报错或修复过程。后续会话从 `scenes/` 重新选择一个 Scene。

当前 Runner 提供规则检查、精简环境、显式环境变量白名单和超时控制，但不是操作系统级沙箱。不要把它直接作为不可信用户可访问的公共执行服务；生产环境应使用受限容器或虚拟机，并单独限制文件系统、网络、子进程和凭证权限。

### 3.6 运行产物

```text
sessions/<session-id>/
├── session.json                 # 对话和当前阶段
├── decisions.md                 # 用户确认和纠正
├── task_specs/                  # 已确认的需求版本
├── retrieval/                   # 每轮文档检索结果
├── planning/                    # 选中 Scene、方案和 API 上下文
├── contexts/                    # 实际模型输入输出
├── runs/                        # 代码、校验、stdout、stderr 和修改记录
└── workspace/generated.py       # 当前脚本
```

完整操作说明见 [`使用文档.md`](使用文档.md)。

## 4. 项目结构

```text
doc2run-agent/
├── doc2run_agent/
│   ├── cli.py                  # 命令行入口和交互适配
│   ├── config.py               # YAML/.env 模型配置
│   ├── llm.py                  # LiteLLM 适配器和重试/限额配置
│   ├── schemas.py              # 跨模块状态和数据契约
│   ├── agents/                 # Chat、Code、Fix、Memory 四个阶段
│   │   ├── chat.py            # Scene 注入、需求澄清、Scenario Plan
│   │   ├── code.py            # API 检索、代码生成、静态检查
│   │   ├── fix.py             # API 约束下的修复和补丁复核
│   │   ├── memory.py          # 用户验收后将方案沉淀为 Scene
│   │   ├── prompts.py         # Agent Prompt
│   │   ├── context.py         # 模型上下文预算和审计记录
│   │   └── parsing.py         # 模型结构化输出解析
│   ├── knowledge/              # 两类知识的检索和 Scene 沉淀
│   │   ├── retriever.py       # 本地文档索引和排序
│   │   ├── tools.py           # API/Scene 检索工具
│   │   └── scenes.py          # Memory：已验收方案保存为 Scene
│   ├── runtime/                # 生成代码的校验、执行和错误处理
│   │   ├── validation.py
│   │   ├── runner.py
│   │   ├── code_edits.py
│   │   └── errors.py
│   ├── storage/                # session、文件和运行产物
│   │   ├── sessions.py
│   │   ├── artifacts.py
│   │   └── files.py
│   └── workflow/               # 顶层 LangGraph 流程编排
│       └── orchestrator.py
├── demo/                       # 可复制的空项目模板
├── tests/                      # 自动化逻辑测试
├── 使用文档.md                 # 从安装到使用的完整说明
├── README.md
├── README_EN.md
└── pyproject.toml
```

主要依赖方向是 `cli → workflow`，再由 `workflow` 协调 `agents`、`knowledge`、`runtime`
和 `storage`；下层模块不反向导入 `workflow`。跨阶段状态只通过独立的 `schemas.py`
传递。

基于 [LangGraph](https://github.com/langchain-ai/langgraph) 和 [LiteLLM](https://github.com/BerriAI/litellm) 构建，采用 [MIT License](LICENSE)。

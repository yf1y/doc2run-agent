[中文](README.md) · [English](README_EN.md)

# Doc2Run Agent

[![Tests](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/yf1y/doc2run-agent/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **把“读文档、写代码、调到能跑”交给一个完整闭环。**

把私有 SDK 或 API 文档放进 `knowledge/`，再用自然语言描述你想要的自动化。Doc2Run Agent 会主动澄清需求、查阅相关文档、生成 Python、执行验证，并在失败时定位问题、自主修复。

它交付的不只是一段“看起来能跑”的代码，而是一个**真正执行过、过程可追溯、随时能恢复**的结果。

```text
你的需求 → 关键问题澄清 → 需求确认 → 第一次检索
        → 写实现方案 → 核对方案 → 按缺口再次检索 → 生成代码
        → 安全检查 → 实际执行 → 局部修复与核对 → 保存全部产物
```

| 常见的一次性代码生成 | Doc2Run Agent |
|---|---|
| 根据一句 prompt 猜需求 | 先把目标、输入输出、约束和验收标准问清楚 |
| 可能编造不存在的 SDK 用法 | 先整理接口材料、写实现方案并核对来源 |
| 输出代码后就结束 | 静态检查、实际运行并收集 stdout/stderr |
| 出错后把问题交还给用户 | 写修改说明、定向检索、局部替换并核对修改 |
| 对过程没有记录 | 保存需求、实现方案、实际模型上下文、代码和运行结果 |

例如，你只需要这样开始：

```text
你> 读取内部 Record SDK 中所有 open 状态的记录，并输出为 JSON。

Agent> 输出写到哪里？是否允许修改数据？怎样算执行成功？

你> 输出到 stdout；只读；结果必须是合法 JSON，且每项包含 id、title、status。

Agent> 需求已整理完成，请检查 TaskSpec。确认后输入 /confirm。

你> /confirm

Agent> 已完成文档检索、代码生成、验证和执行。产物已保存。
```

[功能概述](#1-功能概述) · [安装方式](#2-安装方式) · [使用说明](#3-使用说明) · [项目结构](#4-项目结构)

---

## 1. 功能概述

Doc2Run Agent 将一次 Python 自动化拆成三个各司其职的阶段：

| 阶段 | 它负责什么 | 解决什么问题 |
|---|---|---|
| **Requirements Agent** | 多轮澄清并生成结构化 `TaskSpec` | 避免需求含糊时直接开写 |
| **Generation Agent** | 检索文档、写实现方案、核对缺口并生成脚本 | 把“理解文档”和“写代码”拆开，降低较小模型的一步推理难度 |
| **Fix Agent** | 写修改说明、补充检索、局部修改并核对 | 避免整段重写破坏已经正确的代码 |

代码执行由确定性的 Python 工作流控制，而不是交给模型自行决定。只有需求完整且用户输入 `/confirm` 后，系统才会进入生成和执行阶段。

主要能力：

- **需求确认门**：目标、输入输出、约束、验收标准缺一不可。
- **两轮本地检索**：先查接口和领域材料，再根据实现方案中发现的缺口定向补查。
- **可选领域材料**：通用流程不内置电力等领域字段；可在知识库子目录中按需放入领域说明、规则和示例。
- **实现方案检查**：生成代码前保存并核对结构化方案，缺少的事实不会被静默当成已知信息。
- **受控局部修复**：优先使用精确文本替换；核对通过后才重新执行，后续轮次才允许完整重写兜底。
- **上下文可追溯**：每次实际发送给模型的 system prompt、user prompt、回复、来源和估算 token 数都会保存。
- **多模型协作**：三个阶段可以共用一个模型，也可以分别使用不同模型或服务商。
- **生成—验证—执行—修复闭环**：检查语法、依赖、危险调用和明显的绝对路径写入，再实际执行代码。
- **断点恢复**：对话、需求版本和运行阶段会持久化；退出后可以使用同一 session 继续。
- **完整留痕**：保留 `TaskSpec`、检索上下文、生成代码、验证结果、stdout、stderr 和修复记录。

项目内置一个完全本地的 `doc2run_demo_sdk`，无需真实账号或网络服务即可体验完整流程。

## 2. 安装方式

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/yf1y/doc2run-agent.git
cd doc2run-agent

python -m venv .venv
```

激活虚拟环境：

```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

安装项目并准备配置：

```bash
pip install -e .

# macOS / Linux
cp config.example.yaml doc2run_agent.yaml
cp .env.example .env

# Windows PowerShell
Copy-Item config.example.yaml doc2run_agent.yaml
Copy-Item .env.example .env
```

如果需要运行测试，安装开发依赖：

```bash
pip install -e ".[dev]"
pytest -q
```

## 3. 使用说明

### 3.1 配置模型

Doc2Run Agent 通过 LiteLLM 接入模型，可使用 OpenAI、Anthropic、Gemini、Azure、Ollama、OpenRouter 等提供商。

最简单的方式是让三个 Agent 共用同一个模型。在 `doc2run_agent.yaml` 中填写：

```yaml
models:
  defaults:
    model: openai/gpt-5
    api_key_env: OPENAI_API_KEY
    timeout: 120
    max_retries: 2
    context_tokens: 16000
```

`context_tokens` 是工作流对单次模型输入的估算上限；超出时会明确报错，而不是静默截掉 TaskSpec、代码或接口签名。不同阶段也可以分别设置该值。

然后在 `.env` 中保存密钥：

```dotenv
OPENAI_API_KEY=your-key-here
```

也可以为不同阶段选择不同模型。例如，用擅长对话的模型澄清需求、用代码模型生成脚本、用本地模型处理修复：

```yaml
models:
  defaults:
    timeout: 120
    max_retries: 2

  requirements:
    model: anthropic/claude-sonnet-4-5
    api_key_env: ANTHROPIC_API_KEY

  code:
    model: openai/gpt-5
    api_key_env: OPENAI_API_KEY
    timeout: 180

  fix:
    model: ollama/qwen2.5-coder
    api_base: http://localhost:11434
```

`doc2run_agent.yaml` 和 `.env` 默认不会被 Git 提交。不要把真实密钥写入示例文件或仓库。

### 3.2 放入你的文档

将 SDK/API 说明放入 `knowledge/`：

```text
knowledge/
├── api/
│   ├── internal_sdk.md
│   └── api_reference.json
└── domains/                     # 可选
    └── power/
        ├── overview.md
        ├── building_rules.md
        └── examples.json
```

Doc2Run Agent 会在生成和修复前自动规划查询，只把最相关的文档片段交给模型。你可以先保留仓库自带的 `demo_record_sdk.md`，用它完成第一次体验。

### 3.3 运行交互式 CLI

```bash
doc2run-agent --session demo
```

输入一个自动化需求，回答 Agent 的关键问题。系统展示整理后的 `TaskSpec` 后，输入 `/confirm` 才会开始生成和运行代码。

常用命令：

```text
/show      查看当前 TaskSpec 草稿
/history   查看已保存的需求对话
/confirm   确认需求，开始生成、验证、执行和修复
/reset     归档当前 session，重新开始
/help      查看命令帮助
/exit      保存并退出
```

继续上一次会话时，使用相同的 session ID：

```bash
doc2run-agent --session demo
```

指定其他模型配置或知识库目录：

```bash
doc2run-agent \
  --session internal-report \
  --config configs/development.yaml \
  --knowledge-dir knowledge
```

更多输入示例见 [`examples/requests.md`](examples/requests.md)。

### 3.4 查看运行结果

每个 session 都有独立目录：

```text
sessions/<session-id>/
├── session.json                 # 对话和工作流状态
├── decisions.md                 # 用户明确作出的决定和纠正
├── task_specs/                  # 已确认、不可变的需求版本
├── retrieval/                   # 第一次、补充检索和修复检索结果
├── planning/
│   ├── api_context.md           # 本次任务实际选中的文档
│   ├── implementation_plan.json # 核对后的实现方案
│   ├── plan_review.json         # 方案核对结果
│   └── generation_notes.md      # 模型自行设计的部分和仍缺少的信息
├── contexts/                    # 每次实际发送给模型的完整上下文
├── runs/                        # 代码、运行结果、修改说明和核对结果
└── workspace/generated.py       # 最终执行脚本
```

`/reset` 会将旧 session 移入 `sessions/archives/`，而不是直接删除，因此调试和复盘所需的信息都会保留。

> [!WARNING]
> 当前 Runner 提供 AST 策略检查、精简环境和超时控制，但它**不是操作系统级沙箱**。不要直接执行来自不可信用户的请求，也不要将其原样暴露为公共服务。生产环境应将 `LocalPythonRunner` 替换为受限容器或虚拟机。

## 4. 项目结构

```text
doc2run-agent/
├── src/
│   ├── doc2run_agent/
│   │   ├── requirements_agent.py  # 需求澄清与 TaskSpec 构建
│   │   ├── generation_agent.py    # 文档检索与代码生成
│   │   ├── fix_agent.py           # 错误分析与自动修复
│   │   ├── orchestrator.py        # 顶层工作流与阶段控制
│   │   ├── retriever.py           # 本地知识库检索
│   │   ├── context.py             # 上下文预算、日志裁剪和调用记录
│   │   ├── code_edits.py          # 安全应用局部代码替换
│   │   ├── validation.py          # 代码静态验证
│   │   ├── runner.py              # 超时控制的本地执行器
│   │   ├── session_store.py       # session 持久化与恢复
│   │   ├── artifacts.py           # 运行产物组织
│   │   ├── config.py / llm.py     # 模型配置与 LiteLLM 适配
│   │   └── cli.py                 # 交互式命令行入口
│   └── doc2run_demo_sdk/          # 无需联网的演示 SDK
├── knowledge/                     # SDK/API 文档知识库
├── examples/                      # 示例需求与对话
├── tests/                         # 确定性测试套件
├── config.example.yaml            # 模型配置示例
└── pyproject.toml                 # 依赖、打包和 CLI 定义
```

基于 [LangGraph](https://github.com/langchain-ai/langgraph) 与 [LiteLLM](https://github.com/BerriAI/litellm) 构建，采用 [MIT License](LICENSE)。

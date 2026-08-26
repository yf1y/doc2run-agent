# 可替换 Demo

这个目录是一套可以直接运行、也可以整体复制后替换的最小项目材料。

```text
demo/
├── request.txt
└── knowledge/
    ├── api/
    │   ├── demo_record_sdk.md
    │   └── python_standard_library.md
    └── domains/
        └── demo_records/
            └── memory_schema.json
```

运行仓库自带 Demo：

```bash
doc2run-agent --session demo --knowledge-dir demo/knowledge
```

启动后，将 [`request.txt`](request.txt) 的内容粘贴到 CLI。这个 Demo 使用随项目安装的
`doc2run_demo_sdk`，不需要账号、凭证或网络服务。

要换成自己的项目，可以复制整个目录：

```powershell
Copy-Item -Recurse demo my_project
```

然后完成三件事：

1. 用自己的 SDK/API 文档替换 `my_project/knowledge/api/` 下的内容；
2. 修改 `my_project/request.txt`；
3. 启动时传入 `--knowledge-dir my_project/knowledge`。

如果不需要场景记忆，可以删除 `knowledge/domains/`，并且不要传 `--domain`。如果需要，
请把 `demo_records` 改成自己的领域名，修改其中的 `memory_schema.json`，并在启动时增加
`--domain <领域名>`。

完整说明见仓库根目录的 [`使用文档.md`](../使用文档.md)。

# Respire for AstrBot

[Respire](https://rsrs.rs)（CLI 名 `rsrs`）本地优先、端侧加密记忆系统的 **AstrBot Star 插件**。装上后，AstrBot 会按当轮消息自动召回相关记忆标题，并提供判后即存的记忆工具与 `/respire` 管理指令；同一台机器上的其他 agent 共享同一座记忆库。

- **每轮自动召回**：`on_llm_request` 调 `rsrs recall --titles`，只注入分数、8 位短 id 和标题；正文由模型按需 `respire_show`。
- **六个 LLM 工具**：`respire_recall` / `respire_show` / `respire_remember` / `respire_update` / `respire_merge` / `respire_attach`。
- **判后即存**：新增 important 记忆必须挂到已读的真实父节点；没有合适父节点就返回 pending，不猜父、不 `--force`、不降级绕过审查。
- **管理指令**：`/respire status|recall|show|remember|forget|help`，别名 `/rsrs`。
- **fail-open**：引擎缺失、未初始化、超时、JSON 异常都只跳过记忆层，不阻断聊天。
- **默认安全**：仅管理员可用、群聊记忆关闭；个人记忆不会被普通群成员注入上下文。
- **无 Python 依赖面**：引擎是独立 `rsrs` CLI，插件目录没有第三方 Python 包。

English overview: this is the AstrBot plugin for Respire (`rsrs`). It injects title-tier recall per turn, exposes reviewed memory tools, and fails open when the local engine is unavailable. Access is administrator-only and group-chat memory is off by default.

## 安装

### 1. 安装并初始化引擎

```bash
npm i -g rsrs-cli
rsrs register --user <your-name>   # 云端身份；按提示保存恢复码
# 或者完全本地：rsrs keygen
rsrs status --json                 # unlocked=true 即可
```

语义召回是可选增强：

```bash
rsrs model install && rsrs model activate m3
```

未安装模型时引擎自动走词面档，注册、登录、同步、召回仍可用。

### 2. 安装插件

从 AstrBot 插件市场安装，或把本仓放到：

```text
AstrBot/data/plugins/astrbot_plugin_respire/
```

然后在 WebUI「插件」页重载插件。插件加载后会自动读取 `_conf_schema.json` 生成配置项。

### 3. 验证

在私聊中（管理员账号）：

```text
/respire status
/respire recall 项目名 关键词
```

或在 LLM 对话里直接问需要旧上下文的问题，观察模型是否调用 `respire_recall`。

## 配置

WebUI → 插件 → Respire 记忆。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭后自动召回、工具和指令都拒绝访问 |
| `rsrs_path` | `""` | 留空自动探测 PATH、`~/.local/bin`、`~/.rsrs/bin` 等 |
| `access_mode` | `admin` | `admin` / `allowlist` / `all` |
| `allowed_users` | `[]` | `allowlist` 模式下的用户 ID；管理员始终可用 |
| `group_memory` | `false` | 群聊是否允许记忆；默认关闭，避免个人记忆进入群聊上下文 |
| `auto_recall` | `true` | 每轮自动召回标题档 |
| `recall_limit` | `5` | 自动召回条数（1–20） |
| `recall_threshold` | `0.0` | 额外分数下限；0 表示采用 rsrs 自带相关性门 |
| `min_prompt_chars` | `2` | 过短消息不召回 |
| `budget_chars` | `1200` | 单轮注入字数预算 |
| `max_show_chars` | `8000` | 单条正文读取上限 |
| `max_tool_chars` | `6000` | 工具/指令返回上限 |
| `session_rules` | `true` | 把稳定的记忆使用规则追加到 system prompt |
| `scope_mode` | `global` | `global` / `sender` / `session`；后两者通过 `--project` 做软隔离 |
| `project_prefix` | `astrbot` | 作用域前缀 |
| `store_prefixes` | `["记：","记:","存：","存:","remember:","store:"]` | 存储请求前缀；只交给模型审查，不自动写 |
| `store_nudge_every` | `0` | 0 关闭；N 表示每 N 轮无落库时附一句提醒 |
| `nudge_min_interval` | `600` | 催促最小间隔（秒） |
| `command_store_importance` | `important` | `/respire remember` 的默认档位 |
| `default_parent` | `""` | 重要记忆的默认父节点；留空则要求显式 `--parent` |
| `recall_timeout` | `14` | 召回超时（秒） |
| `write_timeout` | `20` | 写入/候选审查超时（秒） |
| `status_timeout` | `10` | 状态探测超时（秒） |
| `probe_ttl` | `300` | 状态缓存时间（秒） |
| `log` | `true` | 诊断日志；不记录正文或查询原文 |

### 作用域说明

Respire 默认是一座用户级共享记忆库。AstrBot 可能同时服务多个用户，因此插件提供三种 `scope_mode`：

- `global`：不写 `project`，所有会话共享同一座库。适合个人机器人。
- `sender`：`project = astrbot:<platform>:user:<sender>`，同一用户跨私聊/群聊共享，不同用户互不召回。
- `session`：`project = astrbot:session:<umo>`，严格按会话分离。

`sender` / `session` 是**软隔离**：它依赖 `rsrs recall --project` 过滤，不是加密级隔离。需要密码学隔离的多用户部署应使用独立 AstrBot 实例或独立 `RSRS_DATA_DIR`。

## 命令

```text
/respire status
/respire recall <query>
/respire show <id> [depth]
/respire remember [--parent <id>] [--title <text>] [--importance important|trivial] <content>
/respire forget <id> --yes
/respire help
```

- `/respire show` 的 `depth` 取 0–3；大于 0 时调用 `rsrs chain`，返回本条正文与上下游标题。
- `/respire remember` 默认按 `command_store_importance` 写入。important 缺父节点时先跑 `rsrs candidates` 返回候选，不写入。
- `/respire forget` 始终要求 AstrBot 管理员权限，并需要 `--yes` 确认；底层是 tombstone 软删。
- 别名 `/rsrs` 与 `/respire` 等价。

## LLM 工具

| 工具 | 作用 |
| --- | --- |
| `respire_recall(query, limit)` | 标题档召回；空结果不等于无记忆 |
| `respire_show(entry_id, chain_depth)` | 读全文；`chain_depth=1..3` 附带因果链标题 |
| `respire_remember(content, title, parent, importance, type, tags)` | 判后即存；important 必须有可读父节点 |
| `respire_update(entry_id, content, title, importance)` | 就地更新；升为 important 前检查父节点 |
| `respire_merge(content, title, merge_ids, parent, importance)` | 合并重复条目；首个来源是根时必须显式父节点 |
| `respire_attach(child_id, parent_id)` | 把既有子节点挂到既有父节点下 |

工具返回 `pending` 表示**没有写入**。模型应先读候选正文，再按 `update > merge > attach > new` 决断。任何写入成功后插件会 `show` 回读并核对 id、标题和完整正文。

## 设计约束

- **不自动整轮落库**：Respire 是策展记忆，不是聊天日志。插件不会把每轮对话自动写入。
- **动态内容不进 system prompt**：每轮召回写入 `extra_user_content_parts` 并标记为临时，避免破坏提示词缓存；system prompt 只追加稳定规则。
- **不转发宿主凭据**：`rsrs` 子进程只拿最小环境变量（`HOME`、`PATH`、locale、代理、`RSRS_*` 等），不继承 API key/token。
- **不把召回内容当指令**：注入块明确标记为不可信背景，当前用户输入优先。
- **不记录敏感内容**：日志只记事件码、长度和脱敏后的错误；查询词命中凭据模式时跳过召回。

## 已知边界

- 未实现 AstrBot WebUI 可视化页面；配置走原生 schema。
- 不会自动选择父节点；父节点必须由模型或用户显式给出并已读。
- `sender` / `session` 作用域不是加密隔离。
- 插件不管理 `rsrs register` / `login` / `sync`；这些操作仍在本机终端完成。
- 已在 macOS 上用 AstrBot 4.29.4 源码完成验证：直接导入注册（15 个 handler、6 个 tool）与真实 `PluginManager.load()` 加载（元数据、schema 默认配置、handler 绑定、tool 激活、钩子注入、指令响应）均通过 94 项自动化测试；真实聊天/WebUI 端到端验证待补。其他平台按 AstrBot/rsrs 官方支持范围推断。

## 设计与排障文档

- [`docs/plan.md`](docs/plan.md)：方案、当前进度、待办、风险
- [`docs/astrbot-plugin-notes.md`](docs/astrbot-plugin-notes.md)：AstrBot 4.29.4 插件 API、加载流程与坑
- [`docs/respire-cli-notes.md`](docs/respire-cli-notes.md)：rsrs JSON 信封、判后即存、作用域与超时

## 开发

```bash
# 语法与结构自检（无第三方依赖）
python3 scripts/validate_plugin.py

# 自动化测试（需要能 import astrbot 的解释器，如 pip install astrbot）
python -m pytest -q
```

测试分层（见 `tests/`）：

- 纯函数：召回格式化预算/阈值、JSON 信封容错、标题抽取、凭据识别、子进程环境过滤；
- 桩 CLI（`tests/stub_rsrs.py`，带状态）：`on_llm_request` 注入/去重/群聊关闭/非管理员拒绝、六个工具全路径、判后即存（pending 不写、父节点核验、回读校验）、`/respire` 指令、`--project` 作用域；
- 真实 `rsrs` 只读探针：未安装 rsrs 时自动跳过；
- 真实加载（`tests/test_real_load.py`）：子进程内把插件装进临时 `ASTRBOT_ROOT` 的 `data/plugins/`，走真实 `PluginManager.load()`，再在加载出的实例上跑钩子与指令。

仓库结构：

```text
astrbot_plugin_respire/
  main.py              # 插件入口
  metadata.yaml        # AstrBot 元数据
  _conf_schema.json    # WebUI 配置 schema
  requirements.txt     # 无第三方依赖
  pytest.ini
  scripts/validate_plugin.py
  scripts/package.sh   # 打发布 zip（根=插件根）
  .github/workflows/release.yml
  tests/               # pytest 套件 + rsrs 桩 CLI
  docs/                # 方案与调研笔记（不进发布包）
```

## License

MIT

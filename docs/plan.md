# Respire AstrBot 插件 · 方案与进度

> 目录：`/Users/shiningray/projects/risense/astrbot_plugin_respire`
> 状态时间：2026-11-03
> 当前版本：0.1.0（未发版、未 git init、未提交）

## 1. 目标

把 [Respire](https://rsrs.rs)（CLI 名 `rsrs`）的本地优先加密记忆层接入 AstrBot：

- 每轮对话按用户消息自动召回相关记忆标题；
- 模型可按需读正文、看因果链、判后即存；
- 复用本机已有的 `rsrs` 记忆库，与 ZCode / Kimi Code / Hermes / DimCode 等宿主共享；
- 所有失败路径 fail-open，不阻断聊天；
- 默认只允许管理员、群聊关闭，避免个人记忆跨用户/跨群泄漏。

## 2. 当前完成度

| 项 | 状态 | 证据 |
| --- | --- | --- |
| AstrBot 插件文档与源码调研 | 完成 | AstrBot 4.29.4，commit `6c942855`，源码克隆在 `/tmp/astrbot-src`（临时） |
| 插件骨架 | 完成 | `main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt`、`logo.png` |
| 每轮标题档召回 | 完成 | `on_llm_request` + `rsrs recall --titles --json` + `TextPart(...).mark_as_temp()` |
| 稳定规则注入 | 完成 | system prompt 只追加 `<respire_memory_rules>`，动态召回走临时 user content part |
| LLM 工具 | 完成 | 6 个：recall / show / remember / update / merge / attach |
| 指令 | 完成 | `/respire`（别名 `/rsrs`）：help / status / recall / show / remember / forget |
| 判后即存 | 完成 | important 必须有可读 parent；缺失时 `rsrs candidates` 返回候选，不写入 |
| 写入回读 | 完成 | 成功写入后 `rsrs show` 核对 id、标题、完整正文 |
| fail-open | 完成 | 所有 hook/tool/command 捕获异常；子进程超时/坏 JSON 降级为空 |
| 密钥与日志保护 | 完成 | 最小子进程环境；凭据模式跳过；日志脱敏 |
| 配置 schema | 完成 | 25 个配置项，validator 与 `DEFAULTS` 键一致 |
| 结构自检 | 完成 | `scripts/validate_plugin.py`：109 passed, 0 failed |
| AstrBot 真实导入 | 完成 | venv 中导入：15 个 handler、6 个 tool，schema 类型解析正确 |
| 桩 CLI 端到端 | 部分完成 | 假 CLI 下 hook/recall/remember/show/update/merge/attach 均返回；最新补丁后未重跑 |
| 真实 rsrs 只读 | 完成（手工） | 本机 `rsrs status` / `recall` / `taxonomy` / `tree` 可读 |
| 单元测试文件 | 未完成 | `tests/` 仍为空 |
| AstrBot PluginManager 真实加载 | 未完成 | 只做过直接 import，未走 `PluginManager.load()` |
| 真机聊天/WebUI | 未完成 | 未启动 AstrBot 实例发消息验证 |
| git / CI / 发版 | 未开始 | 未 `git init`，无 release workflow |

## 3. 架构

```text
AstrBot 事件
  ├─ on_llm_request
  │    ├─ 稳定规则 → req.system_prompt（只加一次，带标记）
  │    ├─ 每轮召回 → rsrs recall --titles --json
  │    │    └─ req.extra_user_content_parts.append(TextPart(...).mark_as_temp())
  │    └─ 状态引导（未装/未初始化/空库时才加）
  ├─ after_message_sent → 轮次计数（供可选催促）
  ├─ /respire 指令组 → 直接调 rsrs 并回复
  └─ LLM tools
       ├─ respire_recall
       ├─ respire_show（depth>0 时调 rsrs chain）
       ├─ respire_remember（先 candidates 审查，再 remember）
       ├─ respire_update
       ├─ respire_merge
       └─ respire_attach
```

### 文件

| 文件 | 作用 |
| --- | --- |
| `main.py` | 唯一入口；插件类、hooks、commands、LLM tools、rsrs 子进程封装、格式化与守卫 |
| `metadata.yaml` | AstrBot 元数据；`name: astrbot_plugin_respire` |
| `_conf_schema.json` | WebUI 配置 schema；25 项 |
| `requirements.txt` | 无第三方 Python 依赖 |
| `logo.png` | 256×256，1:1 |
| `README.md` | 安装、配置、命令、工具、安全边界 |
| `CHANGELOG.md` | 0.1.0 变更 |
| `scripts/validate_plugin.py` | 无第三方依赖的结构/元数据/schema/tool 自检 |
| `tests/` | 待补 |

## 4. 关键设计决策

### 4.1 动态召回不进 system prompt

AstrBot 文档明确提醒：每轮变化的内容写进 `system_prompt` 会破坏模型服务端提示词缓存，显著增加成本与首 token 延迟。因此：

- `system_prompt` 只追加稳定的 `<respire_memory_rules>`；
- 每轮标题召回写入 `req.extra_user_content_parts`，并用 `TextPart(...).mark_as_temp()` 标记为不持久化；
- 旧版 AstrBot 没有 `TextPart` 时回退为 `{"type":"text","text":...,"_no_save":True}`。

### 4.2 不自动整轮落库

Respire 是策展记忆，不是聊天日志。插件不做 `on_llm_response` 自动把整轮对话写入；只允许：

- 模型判断后调用 `respire_remember`；
- 用户用 `/respire remember`；
- 「记：/存：/remember:/store:」前缀只生成“待存事实”提示，由模型审查后决定，不自动写。

这样避免把闲聊、助手臆测、敏感内容灌进记忆库。

### 4.3 important 必须有可读父节点

对齐 Respire 既有工作流（`rsrs prompt` 的「改 ＞ 并 ＞ 挂 ＞ 存」）：

- `respire_remember(importance=important, parent="")` 不直接写，先跑 `rsrs candidates <content> --json`；
- 有候选：返回候选列表，明确 `Nothing was written`；
- 无候选：返回“需要真实父节点”的说明，不猜、不 `--force`、不降级；
- 传入 parent：先 `rsrs show <parent>` 核验存在，再写；
- 写入成功后 `rsrs show <id>` 回读核验 id、标题、完整正文；
- `--force` 从未使用。

`respire_merge` 也会检查首个来源是否有父节点；没有则要求显式 `parent`，避免合并出新孤儿。
`respire_update` 升为 important 前检查现有父节点。

### 4.4 默认安全边界

- `access_mode: admin`：默认只有 AstrBot 管理员能用记忆（自动召回、工具、指令都走同一检查）。
- `group_memory: false`：默认群聊完全关闭记忆，避免把个人记忆注入群聊上下文。
- `scope_mode: global`：个人机器人默认共享一座库。
- `sender` / `session`：通过 `rsrs --project` 做软隔离，适合多用户；文档明确标注“不是加密级隔离”。
- 需要密码学隔离时，应使用独立 AstrBot 实例或独立 `RSRS_DATA_DIR`。

### 4.5 六工具而不是 shell 透传

AstrBot 不保证模型有 shell 工具，所以把 Respire 的关键动作做成结构化工具：

`respire_recall` / `respire_show` / `respire_remember` / `respire_update` / `respire_merge` / `respire_attach`

`respire_forget` 不暴露给 LLM，只保留管理员指令 `/respire forget <id> --yes`，降低误删风险。

### 4.6 子进程最小环境

`rsrs` 子进程只继承：

- `HOME`、`PATH`、`TMPDIR`、locale、`TZ`、`SHELL` 等运行必需；
- 代理与 TLS 变量；
- `RSRS_*` / `RESPIRE_*` 中名字不含 TOKEN/SECRET/PASSWORD/SUPER/API_KEY/CREDENTIAL/AUTH 的变量。

不把宿主进程的 API key/token 整包传给 sidecar。

## 5. 已验证事实

### 5.1 AstrBot 侧

在 `/tmp/astrbot-venv`（Python 3.14.8，editable 安装）导入 `main.py` 后：

- 15 个 handler 注册成功；
- 6 个 LLM tool 注册成功；
- tool schema 类型解析正确：
  - `query` / `entry_id` / `content` / `title` / `parent` / `importance` / `type` / `tags` 为 `string`；
  - `limit` / `chain_depth` 为 `number`；
- `on_llm_request` 注入 `TextPart`，内容包含 `<respire_memory>` 标题块；
- 假 CLI 下 `respire_recall`、`respire_remember`（无父/有父）、`respire_show`、`respire_update`、`respire_merge`、`respire_attach` 均能返回；
- `scripts/validate_plugin.py` 109 项全过。

尚未完成：真实 `PluginManager.load()`、真实 AstrBot 事件管线、真实 WebUI/聊天。

### 5.2 rsrs CLI 侧

本机 `rsrs` 1.0.18：

- `rsrs status --json`：`unlocked=true`，`local_total` 约 2000+；
- `rsrs recall ... --titles --json`：`details[]` 含 `id/title/score/superseded_by`；
- `rsrs show <id> --json`：`details={entry, ancestors, children}`；
- `rsrs chain <id> --depth N --json`：`details={entry, ancestors, descendants}`；
- `rsrs remember` 无 `--parent` 且命中候选时返回 `status=pending`，`summary.written=false`；
- `rsrs candidates <content> --json` 是只读预览，`details.merge[]` / `details.parent[]`；
- `rsrs taxonomy --list --json` 返回 23 个根名称；
- `rsrs tree --outline --json` 对 2000+ 条库可达约 579 KB，不适合每轮调用。

## 6. 待办（按优先级）

1. **清理生成物**：导入 AstrBot 时在插件目录生成了 `data/`、`__pycache__/`；已删除，后续测试应把 `ASTRBOT_ROOT` 指向临时目录，避免再生成。
2. **重跑假 CLI 测试**：最新补丁（`_update` 回读、`--trivial/--important` 解析、闭合标签常量）后尚未重跑。
3. **写 `tests/test_plugin.py`**：
   - 纯函数：`format_recall` 预算/阈值、`parse_envelope` 容错、`extract_title`、`contains_secret`、`sidecar_env` 不透传 token；
   - 桩 CLI：`on_llm_request` 注入、去重、群聊关闭、非管理员拒绝、store pending、parent 校验、回读核验、六个工具；
   - 真实 rsrs 只读：`status` / `recall`，无 rsrs 时 skip；
   - AstrBot 真实加载：`PluginManager.load()`，检查 metadata/config/handlers/tools。
4. **真实 AstrBot 加载验证**：
   - `ASTRBOT_ROOT` 指向临时目录；
   - 把插件目录放进 `data/plugins/`；
   - 构造最小 `Context` 或使用 `PluginManager` + stub context；
   - 验证 `metadata.yaml`、`_conf_schema.json` 默认配置、handler 绑定、tool active。
5. **事件管线验证**：
   - 构造 `AstrMessageEvent` / `ProviderRequest`；
   - 验证私聊管理员注入、群聊默认不注入、`scope_mode=sender` 传 `--project`、`/nomem` 跳过。
6. **命令行为验证**：
   - `/respire status|recall|show|remember|forget|help`；
   - `--parent` / `--importance` / `--trivial` 解析；
   - `forget` 必须管理员 + `--yes`。
7. **README 校正**：当前写“仅在 macOS 与 AstrBot 4.29.4 上做真机加载验证”，但实际只做到直接 import；应改为“导入验证，PluginManager/聊天验证待补”，或完成真机加载后再写“真机加载”。
8. **可选**：
   - `skills/respire/SKILL.md`（AstrBot Skill Manager 自动加载）；
   - `scripts/package.sh` + `.github/workflows/release.yml`（zip 根=插件根，≤16 MB）；
   - `docs/` 发布到 respire-docs；
   - `git init` + 远端 `risense-ai/astrbot_plugin_respire`。
9. **发版前**：
   - 版本号 `metadata.yaml` / `PLUGIN_VERSION` / `CHANGELOG.md` 三处一致；
   - zip 不含 `__pycache__`、`.git`、`data/`、测试临时文件；
   - 用 AstrBot 市场安装链路实测一次。

## 7. 已知风险 / 待决

| 风险/问题 | 说明 | 建议 |
| --- | --- | --- |
| `respire_remember` 无父时只返回候选，不列根节点 | 模型可能不知道 23 个 taxonomy 根 id | 可加 `respire_roots` 工具，或在提示中让模型 `respire_recall("<主题>")` 找根 |
| `/respire remember` 默认 important | 普通用户可能只想记日记，却因缺父被拒 | 可改默认 `trivial`，或文档强调 `--importance trivial` |
| `scope_mode` 软隔离 | 依赖 `--project` 过滤，不是权限边界 | 多用户部署必须独立实例/独立数据目录 |
| `tree --outline` 体积大 | 2000+ 条库约 579 KB | 已有实现不调用 tree；若加 roots 工具，只解析 `taxonomy --list` + tree 的 `depth=0` 行 |
| `keygen --pass` 非 TTY 行为 | 本机尝试 120s 超时，runtime 起来了但未生成 session | 测试不要依赖临时数据目录写入；写测试用桩 CLI |
| AstrBot 版本兼容 | 仅对 4.29.4 源码核实；`TextPart` 有回退，但 `extra_user_content_parts` 旧版可能不存在 | `astrbot_version: >=4.16,<5`；在 README 标注最低版本 |
| 工具 schema 无 `required` | AstrBot 当前 `add_func` 只写 properties，不写 required | 模型可能省略必填参数；插件内部已做空值校验 |
| 群聊命令输出 | 管理员在群里执行 `/respire recall`，结果对群可见 | 文档提示；默认 `group_memory=false` 已禁止自动召回/工具 |

## 8. 复现命令

```bash
cd /Users/shiningray/projects/risense/astrbot_plugin_respire

# 结构自检
python3 scripts/validate_plugin.py

# 用已装 AstrBot 的 venv 导入并检查注册
/tmp/astrbot-venv/bin/python - <<'PY'
import main
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.provider.register import llm_tools
print(len(star_handlers_registry.get_handlers_by_module_name('main')))
print([t.name for t in llm_tools.func_list if t.name.startswith('respire_')])
PY

# 真引擎只读
rsrs status --json
rsrs recall "respire astrbot" --titles --json --limit 3
rsrs taxonomy --list --json
```

## 9. 方案记录

- 2026-11-03：确定“标题档自动召回 + 六个结构化工具 + 判后即存 + 默认管理员/群聊关闭”的方案。
- 2026-11-03：完成 `main.py` 0.1.0 骨架、配置、元数据、README、validator。
- 2026-11-03：AstrBot 4.29.4 导入验证通过；待补测试与真实加载。

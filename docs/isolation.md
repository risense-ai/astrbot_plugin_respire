# AstrBot 多机器人 / 多人格记忆隔离方案

> 记录时间：2026-11-03
> 适用：`astrbot_plugin_respire` 0.1.x
> 结论：Respire 有硬隔离方案（`space` / `account` 独立 data_dir）。插件 0.2.0 已实现方式 1：机器人级 `space` 硬隔离 + 人格级 `--project` 软隔离；默认 `isolation_mode=off`，需显式开启。

## 1. 先分清两类隔离

| 方案 | 机制 | 隔离强度 | 跨 agent 共享 | 成本 |
| --- | --- | --- | --- | --- |
| 软隔离 | `rsrs --project <scope>` | 仅检索过滤 | 同一座库，其他 agent 可见 | 低 |
| 硬隔离 | 每个 scope 独立 `RSRS_DATA_DIR`（space/account profile） | 独立 store / session / keys | 默认不共享，需显式指向同一 profile | 中高：每 profile 一个 runtime，可能各自加载模型 |

当前插件 `scope_mode=global|sender|session` 走的是软隔离：

- `recall` 带 `--project`，只召回该 scope 的条目；
- `remember` 带 `--project`，写入该 scope；
- `show <id>` 不带 project，知道 id 就能读任何条目；
- 其他不带 `--project` 的 agent 仍能召回全库。

因此：**`--project` 不是权限边界，不能用于互不信任的机器人/人格。**

## 2. Respire 已有的硬隔离原语

### 2.1 `space`（虚拟账号，推荐）

`rsrs space` 的模型是：**一个 space 就是一个账号**，有独立的：

- data_dir（store 文件、session、keys 都在里面）
- super key
- 云端同步分区

命令：

```bash
rsrs space list --json
rsrs space create <name>      # 创建并切换进去；之后需为该 space register/login
rsrs space use <name>         # 切换当前 profile（全局，单进程）
rsrs space invite             # 生成成员邀请码（含 super password，谨慎传递）
rsrs space join <code>
rsrs space members
rsrs space kick --session <id> | --all
rsrs space remove <name> --yes
```

`space list --json` 的 `details.spaces[]` 每项含：

```json
{"name": "botA", "dir": "/Users/.../.rsrs/accounts/botA", "user": "...", "current": false, "owner": true}
```

硬隔离的用法不是让插件反复执行 `space use`（全局切换，多个并发请求会串库），而是：

```bash
RSRS_DATA_DIR=/Users/.../.rsrs/accounts/botA rsrs recall ...
RSRS_DATA_DIR=/Users/.../.rsrs/accounts/botA rsrs remember ...
```

每个子进程各自指向自己的 profile 目录，互不影响。

### 2.2 `account`（本地多账号 profile）

`rsrs account list --json` 同样列出 `main` 与 `accounts/<name>`，每个 profile 独立 data_dir/session/store。`space` 和 `account` 在本地层面共用同一套 profile 目录；`space` 多了 owner/invite/kick 等虚拟账号语义。

如果只是本机隔离、不需要邀请成员，`account` 也够用；需要“一个 owner 下多个虚拟账号、可邀请只读成员”时用 `space`。

### 2.3 模型目录

独立 data_dir 下默认模型路径是：

```text
<data_dir>/models/bge-m3
```

如果新 profile 没有安装模型，语义召回会失败。两种做法：

1. **共享模型（推荐）**：给子进程注入

   ```bash
   RSRS_M3_DIR=~/.rsrs/models/bge-m3
   ```

2. 每个 profile 单独 `rsrs model install`（磁盘与下载成本高）。

注意：`RSRS_M3_DIR` 只在该 profile 的 **runtime 首次启动**时有效；runtime 已经用错误路径启动后，改环境变量不会热修。实测：`RSRS_DATA_DIR=<profile>` 已有 runtime 时，补 `RSRS_M3_DIR` 仍报模型缺失。插件必须在第一次调用前就注入正确环境，或者先 `rsrs doctor` 确认。

## 3. AstrBot 侧的身份来源

### 3.1 机器人

AstrBot 的“机器人”本质是平台适配器实例，Dashboard 里叫 `bot`：

- `event.get_platform_id()` → 平台实例 ID（`platform[].id`，Dashboard 的 `bot_id`）
- `event.get_self_id()` → 机器人账号 ID（QQ 号、Telegram username 等）
- `event.get_platform_name()` → 适配器类型（`aiocqhttp`、`telegram` 等）

推荐 key：

```text
bot:<platform_id>:<self_id>
```

`platform_id` 已能唯一标识一个适配器实例；加 `self_id` 可覆盖同一适配器下多账号的情况。

### 3.2 人格

AstrBot 的人格在会话的 `Conversation.persona_id` 里；核心在 `on_llm_request` 之前调用 `apply_persona()`，并把最终人格写进：

```python
event.get_extra("_trace_persona_id")
```

因此插件在 `on_llm_request` 里可以：

```python
persona_id = (
    event.get_extra("_trace_persona_id")
    or (req.conversation.persona_id if req.conversation else None)
    or "default"
)
```

注意：

- 显式人格在 `conversation.persona_id`；
- 默认人格、会话规则强制人格由 `persona_manager.resolve_selected_persona()` 解析后写入 `_trace_persona_id`；
- 第三方 Agent Runner 路径可能没有走 `apply_persona()`，此时回退到 `conversation.persona_id` 或 `default`；
- 人格 ID 是字符串（通常是人格名），可能含中文/空格，映射到 profile 名时要转成安全的 `[A-Za-z0-9_-]` 或使用哈希。

推荐 key：

```text
persona:<persona_id>
```

### 3.3 组合 key

```text
bot:<platform_id>:<self_id>:persona:<persona_id>
```

优先级建议：

1. 精确 `bot+persona` 映射
2. `persona` 映射
3. `bot` 映射
4. `default` 映射
5. fallback：`main` 或拒绝

这样可以表达：

- 每个机器人一座库，人格共享；
- 每个机器人下每个人格一座库；
- 某个人格在所有机器人间共享一座库；
- 未匹配的走默认库。

## 4. 已实现：机器人级硬隔离 + 人格级软隔离

### 4.1 配置

`_conf_schema.json` 已新增：

```json
{
  "isolation_mode": {
    "description": "记忆隔离方式",
    "type": "string",
    "default": "off",
    "options": ["off", "bot"],
    "labels": ["关闭（共享主库）", "按机器人硬隔离"]
  },
  "bot_profiles": {
    "description": "机器人到 rsrs space 的映射",
    "type": "template_list",
    "default": [],
    "templates": {
      "bot_profile": {
        "name": "机器人 profile",
        "display_item": "match",
        "items": {
          "match": {"type": "string", "description": "key，如 bot:qq_main:123"},
          "space": {"type": "string", "description": "rsrs space/account 名"}
        }
      }
    }
  },
  "profile_fallback": {
    "description": "未匹配时",
    "type": "string",
    "default": "deny",
    "options": ["deny", "main"],
    "labels": ["拒绝记忆操作", "落主库"]
  },
  "m3_dir": {
    "description": "共享 BGE-M3 模型目录",
    "type": "string",
    "default": "",
    "hint": "留空自动尝试 ~/.rsrs/models/bge-m3"
  },
  "space_list_ttl": {
    "description": "space 列表缓存（秒）",
    "type": "int",
    "default": 60
  }
}
```

`bot_profiles` 的 `match` 支持：

- `bot:<platform_id>:<self_id>`：精确机器人；
- `bot:<platform_id>:*`：该平台适配器下所有机器人；
- `default`：兜底。

### 4.2 解析流程

```text
on_llm_request / tool / command
  → isolation_mode=bot?
  → key = bot:<platform_id>:<self_id>
  → bot_profiles 精确 → bot:<platform>:* → default
  → 得到 space/account 名
  → rsrs space list --json（缓存 space_list_ttl）→ name → dir
  → 子进程 env: RSRS_DATA_DIR=<dir>, RSRS_M3_DIR=<m3_dir>
  → rsrs recall/remember/show...
  → 人格用 --project astrbot:persona:<persona_id> 软隔离
```

关键约束：

- **不要调用 `rsrs space use`**：它会写全局 `client.json`，并发请求会串库。
- **不要自动创建 space**：`space create` 会切换 profile，且新 space 需要 register/login；应由用户在终端完成。
- **space 不存在或 dir 无 session**：fail closed（返回“该机器人/人格尚未初始化记忆库”），不要静默落主库，避免跨 scope 泄漏。
- **`RSRS_M3_DIR` 在第一次子进程调用前注入**；否则 runtime 可能已缓存错误路径。
- **缓存 `space list`**：避免每次请求都跑 CLI；`/respire status` 可强制刷新。
- **key 中的特殊字符**：persona 名可能含中文/空格；profile 名只允许 `[A-Za-z0-9_-]`，映射表由用户显式填写，插件不自动拼 profile 名。

### 4.3 `sidecar_env`（已实现）

改造前：

```python
async def _run(self, args, timeout):
    process = await asyncio.create_subprocess_exec(
        engine, *args, env=sidecar_env()
    )
```

实现为：

```python
async def _run(self, args, timeout, profile_dir: str = "", m3_dir: str = ""):
    env = sidecar_env(profile_dir=profile_dir, m3_dir=m3_dir)
    process = await asyncio.create_subprocess_exec(engine, *args, env=env)
```

`sidecar_env` 在现有白名单基础上覆盖：

```python
if profile_dir:
    env["RSRS_DATA_DIR"] = profile_dir
if m3_dir:
    env["RSRS_M3_DIR"] = m3_dir
```

### 4.4 工具与命令

所有路径统一走同一个 profile 解析：

- `respire_recall` / `respire_remember` / `respire_update` / `respire_merge` / `respire_attach`
- `/respire status|recall|show|remember|forget`
- `on_llm_request` 自动召回

`respire_show` 不需要额外过滤：硬隔离下它只能看到当前 profile 的 store。

### 4.5 配置示例（方式 1）

```text
机器人 A（QQ 主号 123）：
  key: bot:qq_main:123
  space: astrbot_main
  人格“猫娘”：project=astrbot:persona:猫娘
  人格“工作”：project=astrbot:persona:工作

机器人 B（Telegram）：
  key: bot:telegram_main:mybot
  space: astrbot_tg

未匹配：
  profile_fallback=deny
```

`bot_profiles` 的匹配优先级：

```text
精确 bot:<platform>:<self_id> → bot:<platform>:* → default
```

人格不参与 space 选择；它只改变 `--project`。

## 5. 创建与初始化流程（人工）

```bash
# 1. 为每个需要隔离的机器人创建 space（会切换进去）
rsrs space create astrbot_qq_main
rsrs register --user <account>   # 或 rsrs keygen（纯本地）
rsrs space list --json           # 记下 dir，确认 session

# 2. 再创建另一个机器人 space
rsrs space create astrbot_tg
rsrs register --user <account>

# 3. 回到主 space（如果要用 CLI 继续操作主库）
rsrs space use main

# 4. 在 AstrBot 插件配置 bot_profiles 填 match → space，
#    并设置 isolation_mode=bot、scope_mode=persona
```

如果不想让每个 space 都下载模型：

```text
m3_dir = ~/.rsrs/models/bge-m3
```

或者在每个 profile 下单独安装。

## 6. 方案取舍

### 推荐：按机器人硬隔离 + 人格软隔离

- 每个机器人一个 space：避免不同机器人互看记忆；
- 同一机器人下的人格用 `--project` 软隔离：实现简单，人格之间通常互信；
- 如果某个人格需要独立密钥/独立云端账号，0.2.0 尚未实现 per-persona space；需要另立 AstrBot 实例或后续扩展映射。

### 全硬隔离

- 每个 `bot+persona` 一个 space；
- 隔离最强，但 runtime/模型内存随组合数增长；
- 适合少量高敏感人格，不适合几十个组合。

### 纯软隔离

- 只扩展 `scope_mode` 为 `bot` / `persona` / `bot_persona`；
- 零运维、共享一座库；
- 只适合“自己的一堆机器人/人格”，不适合多租户或互不信任的用户。

## 7. 已实现清单

1. `_conf_schema.json`：已新增 `isolation_mode`、`bot_profiles`、`profile_fallback`、`m3_dir`、`space_list_ttl`。
2. `main.py`：
   - `_bot_key(event)`：`bot:<platform_id>:<self_id>`；
   - `_persona_id(event, req)`：`_trace_persona_id` → `conversation.persona_id` → `default`；
   - `_resolve_profile(event, req)`：映射 + `space list` 缓存 + 返回 `{dir, space, key, error}`；
   - `sidecar_env(profile_dir, m3_dir)`；
   - 所有记忆读取/写入子进程都带 `profile_dir`；
   - `/respire status` 显示当前 profile/space；
   - 未匹配/未初始化时 fail closed。
3. 测试：
   - 桩 CLI 记录每次调用的 `RSRS_DATA_DIR` / `RSRS_M3_DIR`；
   - 精确 bot 匹配、`bot:<platform>:*`、`default`、fallback deny/main；
   - 缺 space、无 session、space 列表缓存；
   - persona `--project` 保持中文；
   - AstrBot 事件设置 `_trace_persona_id` 与 `message_obj.self_id`。
4. 文档：README 增加“机器人级硬隔离 + 人格级软隔离”章节，明确软/硬边界。

当前测试：110 项通过（含真实 `PluginManager.load()` 与真实 rsrs 只读探针）；真实 `space list`（5 个 profile）与真实 `RSRS_DATA_DIR` recall 已在 mac 上手工验证。

## 8. 结论

Respire 本身**有硬隔离方案**：`space` / `account` 每个都是独立 data_dir、独立 session、独立 store。AstrBot 插件 0.2.0 已实现方式 1：

1. 用 `rsrs space` 为需要隔离的机器人建 profile；
2. 插件按 `platform_id + self_id` 查 `bot_profiles`；
3. 每次 `rsrs` 子进程设置 `RSRS_DATA_DIR=<space dir>` 和 `RSRS_M3_DIR=<共享模型>`；
4. 同一机器人内的人格用 `--project astrbot:persona:<persona_id>` 软隔离；
5. 不调用 `space use`，不自动建 space，未匹配 fail closed。

这样机器人之间是真正的 store 级隔离；人格之间是 `--project` 软过滤（知道 id 仍可 `show`），符合方式 1 的取舍。

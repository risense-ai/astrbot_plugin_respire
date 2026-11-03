# AstrBot 插件开发知识笔记

> 调研时间：2026-11-03
> AstrBot 版本：4.29.4
> 源码 commit：`6c942855cb06b6d43854e8b841859d60a5c6043e`（2026-11-02）
> 临时源码路径：`/tmp/astrbot-src`（可随时重新 clone）
> 官方文档：<https://docs.astrbot.app/dev/star/plugin-new.html>

## 1. 插件最小结构

```text
data/plugins/astrbot_plugin_respire/
  main.py              # 必须叫 main.py；插件类继承 Star
  metadata.yaml        # 必须；AstrBot 识别插件元数据依赖它
  _conf_schema.json    # 可选；有它则 WebUI 生成配置并传入 __init__
  requirements.txt     # 可选；pip 依赖
  logo.png             # 可选；1:1，推荐 256x256
  skills/              # 可选；插件自带的 AstrBot Skill
```

插件名建议 `astrbot_plugin_` 开头、全小写、无空格。`metadata.yaml` 必填字段：

```yaml
name: astrbot_plugin_respire      # 唯一标识
desc: 详细描述
version: 0.1.0                    # 语义化版本
author: risense
repo: https://github.com/...
display_name: Respire 记忆        # 可选
short_desc: 一句话                 # 可选
astrbot_version: ">=4.16,<5"      # 可选，PEP 440
support_platforms: [...]          # 可选
tags: [...]                       # 可选
```

校验实现：`astrbot/core/star/updater.py:29` 定义必填字段 `("name","desc","version","author")`；`validate_plugin_metadata()` 在 `updater.py:360`。

## 2. 插件类与注册

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

class MyPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
```

- `Star.__init_subclass__` 会把子类登记进 `star_map` / `star_registry`。
- 插件加载时，AstrBot 用 `data.plugins.<目录名>.main` 导入模块，再实例化类。
- `__init__(context, config)` 有配置时会传 `AstrBotConfig`；没有 schema 时只传 `context`。AstrBot 用 `TypeError` 回退，所以签名可写 `config=None`。
- `initialize()` / `terminate()` 是可选生命周期，加载/卸载时调用。
- 实例属性 `self.name`、`self.author`、`self.plugin_id` 由加载器注入（`star_manager.py:1230` 附近）。
- 插件数据目录：`data/plugin_data/<self.name>/`；配置：`data/config/<插件目录名>_config.json`。

## 3. 指令

```python
@filter.command("hello", alias={"hi"})
async def hello(self, event: AstrMessageEvent):
    yield event.plain_result("hi")
```

- 指令不能带空格；要用指令组。
- 指令组：

```python
@filter.command_group("respire", alias={"rsrs"})
async def respire_group(self, event: AstrMessageEvent):
    return

@respire_group.command("recall")
async def recall(self, event: AstrMessageEvent, query: GreedyStr = ""):
    yield event.plain_result(...)
```

- 指令组根 handler 的签名要带 `event` 参数（AstrBot 插件审查规则也要求所有 `@filter` handler 有 `event`）。
- 用户只发 `/respire` 时，`CommandGroupFilter.filter()` 会抛 `ValueError("参数不足...")` 并渲染命令树（`core/star/filter/command_group.py`）。
- 捕获剩余文本用 `from astrbot.core.star.filter.command import GreedyStr`。
- 权限装饰器：

```python
@filter.permission_type(filter.PermissionType.ADMIN)
```

本插件没有用装饰器，而是在 handler 内做动态权限检查，因为权限受插件配置影响。

## 4. LLM 工具

### 4.1 装饰器方式

```python
@filter.llm_tool("respire_recall")
async def tool_recall(self, event: AstrMessageEvent, query: str, limit: int = 5) -> str:
    """Recall local encrypted memory titles.

    Args:
        query(string): Project name plus keywords.
        limit(number): Maximum number of titles.
    """
    return "..."
```

### 4.2 关键坑

- AstrBot 用 `docstring_parser.parse()` 解析 docstring，**只看 `Args:` 段**，不读函数签名的类型注解。
- 缺 `Args:` 或格式不是 `参数名(type): 描述`，参数 schema 会为空，LLM 传入的参数被静默丢弃。
- 支持类型：`string`、`number`、`object`、`boolean`、`array`、`array[string]`。
- 不支持在装饰器里显式传 `parameters=...`；要完全控制 schema 就用 `@dataclass` + `context.add_llm_tools()`。
- `self` 和 `event` 不需要写进 `Args:`；框架会提供。
- 工具 handler 返回 `str` 会进入下一次 LLM prompt；返回 `None` 不进入。
- 可以 `yield` 消息并 `event.stop_event()`。
- 当前 4.29.4 的 `add_func` 生成的 schema 只有 `properties`，**没有 `required`**；必填参数仍需插件内部校验。
- 工具注册时机是模块导入时（装饰器执行），实际实例绑定在 `PluginManager.load()` 中完成。

源码：`astrbot/core/star/register/star_handler.py:610`（`register_llm_tool`），`astrbot/core/provider/func_tool_manager.py:256`（调用 handler）。

## 5. on_llm_request 与动态上下文

```python
@filter.on_llm_request()
async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
    ...
```

- 签名必须是 `(self, event, req)`。
- `req` 是 `astrbot.core.provider.entities.ProviderRequest`，字段包括：
  - `prompt: str | None`
  - `system_prompt: str`
  - `extra_user_content_parts: list[ContentPart]`
  - `contexts: list[dict]`
  - `func_tool: ToolSet | None`
  - `conversation`、`model` 等。
- 动态内容不要写 `req.system_prompt += ...`。官方警告：每轮变化会破坏提示词缓存，成本和首 token 延迟可增加 7–20 倍。
- 推荐：

```python
from astrbot.core.agent.message import TextPart

req.extra_user_content_parts.append(
    TextPart(text="<dynamic_context>...</dynamic_context>").mark_as_temp()
)
```

- `.mark_as_temp()` 需要 AstrBot >= 4.24.0；旧版没有该方法时可以直接 append `TextPart`，或退化为 dict：
  `{"type": "text", "text": "...", "_no_save": True}`。
- `ProviderRequest.extra_user_content_parts` 注释明确“支持 dict 或 ContentPart 对象”。
- 本插件：稳定规则进 `system_prompt`，召回进 `extra_user_content_parts`。

源码：`astrbot/core/provider/entities.py:194`，`astrbot/core/agent/message.py`（`TextPart.mark_as_temp`）。

## 6. 事件与权限

`AstrMessageEvent` 常用方法：

- `event.message_str`：纯文本
- `event.message_obj`：`AstrBotMessage`（sender、group_id、type、message chain）
- `event.unified_msg_origin`：`platform:message_type:session_id`
- `event.get_sender_id()` / `get_sender_name()`
- `event.get_platform_id()` / `get_platform_name()`
- `event.get_self_id()`：机器人账号 ID（QQ 号、Telegram username 等）
- `event.get_extra("_trace_persona_id")`：核心在 `apply_persona()` 后写入的最终人格 ID；命令路径可能尚未设置，需要回退 `conversation.persona_id`
- `event.is_private_chat()`
- `event.is_admin()`
- `event.get_extra(key, default)` / `set_extra(key, value)`
- `event.plain_result(text)` / `event.chain_result([...])`
- `event.stop_event()`

权限：`filter.PermissionType.ADMIN`、`MEMBER`、`GROUP_ADMIN`、`SHARED_GROUP_ADMIN`（`core/star/filter/permission.py`）。

## 7. 配置 schema

`_conf_schema.json` 支持类型：

`string`、`text`、`int`、`float`、`bool`、`object`、`list`、`dict`、`template_list`、`file`。

常用字段：

```json
{
  "enabled": {
    "description": "总开关",
    "type": "bool",
    "default": true,
    "hint": "关闭后不再召回"
  },
  "access_mode": {
    "description": "访问范围",
    "type": "string",
    "default": "admin",
    "options": ["admin", "allowlist", "all"],
    "labels": ["仅管理员", "白名单", "所有用户"]
  },
  "recall_limit": {
    "description": "召回条数",
    "type": "int",
    "default": 5,
    "slider": {"min": 1, "max": 20, "step": 1}
  }
}
```

- AstrBot 会递归检查 schema，自动补缺失默认值、移除不存在的键。
- 配置对象是 `AstrBotConfig`（继承 dict），可直接 `.get()`，也有 `.save_config()`。
- `secret: true` 只遮罩 UI，不加密配置文件；不要把密钥写日志。

## 8. 插件加载流程（4.29.4）

`PluginManager.load()`：

1. `_get_plugin_modules()` 扫描 `data/plugins/*/main.py` 或 `data/plugins/<dir>/<dir>.py`。
2. 用 `data.plugins.<dir>.main` 导入模块。
3. 检查 `_conf_schema.json`，创建 `AstrBotConfig(data/config/<dir>_config.json, schema)`。
4. 读取 `metadata.yaml`，校验必填字段与 `astrbot_version`。
5. 实例化插件类（有配置传 `context` + `config`，否则 `context`）。
6. 把模块内所有 handler 绑定到插件实例；把 `llm_tools` 中属于该模块的工具设为 active。
7. 调用 `initialize()`。

源码：`astrbot/core/star/star_manager.py:1076` 起。

## 9. 开发/测试坑

### 9.1 导入插件会在 cwd 生成 `data/`

AstrBot 的路径工具 `get_astrbot_root()` 优先读 `ASTRBOT_ROOT`，否则用 `os.getcwd()`。直接在插件目录 import `main.py` 时，AstrBot 可能创建：

```text
<插件目录>/data/cmd_config.json
<插件目录>/data/t2i_templates/...
```

测试时应设置：

```bash
export ASTRBOT_ROOT=/tmp/astrbot-test-root
```

或把 cwd 切到临时目录，避免污染插件仓库。

### 9.2 安装 AstrBot 依赖

AstrBot 4.29.4 要求 Python >= 3.12，依赖很多（faiss、pandas、telegram、lark 等）。本机验证用：

```bash
uv venv /tmp/astrbot-venv --python 3.14
uv pip install --python /tmp/astrbot-venv/bin/python -e /tmp/astrbot-src
```

安装后可直接 import `astrbot.api`，但不会安装 pytest/pytest-asyncio（dev 依赖组未装）。写测试可用 stdlib `unittest.IsolatedAsyncioTestCase`，或额外 `uv pip install pytest pytest-asyncio`。

### 9.3 rsrs 子进程不要用 `2>&1`

`rsrs --json` 的进度写 stderr，JSON 写 stdout。合并会破坏解析。必须是：

```python
stdout, stderr = await proc.communicate()
doc = json.loads(stdout.decode("utf-8", errors="replace"))
```

并且 `--json` 失败也返回 JSON（`status: "fail"`），不要只看退出码。

### 9.4 子进程环境不要整包透传

宿主进程可能持有各种 API key。`rsrs` 只需要 `HOME`、`PATH`、locale、代理、`RSRS_*` 等。建议白名单构造环境，并剔除名字含 `TOKEN/SECRET/PASSWORD/SUPER/API_KEY/CREDENTIAL/AUTH` 的变量。

### 9.5 `rsrs keygen` 非 TTY 会卡

2026-11-03 在 `/tmp/rsrs-*` 临时数据目录尝试：

```bash
RSRS_DATA_DIR=/tmp/xxx rsrs keygen --pass test --json
```

120 秒未返回，runtime 已启动，但没有生成 session。判断是交互/提示词原因（未最终核实）。因此：

- 测试写入用桩 CLI；
- 不要依赖临时 `RSRS_DATA_DIR` 做自动化写入；
- 真实写入探针应使用现有可写库并自清理，或由人工执行。

### 9.6 `rsrs tree --outline --json` 可能很大

本机 2000+ 条库输出约 579 KB。不要每轮调用。需要根节点时优先 `rsrs taxonomy --list --json`（23 条固定根名称）。

### 9.7 工具 schema 没有 `required`

AstrBot 把 docstring 参数转成 `properties`，但不写 `required`。模型可能省略必填参数。插件内部对空值返回明确错误，不要假设模型一定传参。

## 10. 本项目的对应实现

| AstrBot 能力 | 本项目用法 |
| --- | --- |
| `@filter.on_llm_request()` | 每轮召回 + 稳定规则 + 状态引导 |
| `req.extra_user_content_parts` | 注入 `<respire_memory>` 标题块，`mark_as_temp()` |
| `@filter.after_message_sent()` | 轮次计数，供可选存储催促 |
| `@filter.command_group("respire")` | `/respire` / `/rsrs` 指令组 |
| `@filter.llm_tool(...)` | 6 个结构化记忆工具 |
| `AstrBotConfig` | 25 项配置 |
| `event.is_admin()` / `is_private_chat()` | 默认管理员、群聊关闭 |
| `event.get_extra/set_extra` | 单轮召回去重 |
| `Star.initialize/terminate` | 后台状态探测与取消 |

## 11. 参考链接

- 插件开发指南：<https://docs.astrbot.app/dev/star/plugin-new.html>
- 最小实例：<https://docs.astrbot.app/dev/star/guides/simple.html>
- 消息事件：<https://docs.astrbot.app/dev/star/guides/listen-message-event.html>
- 插件配置：<https://docs.astrbot.app/dev/star/guides/plugin-config.html>
- 调用 AI / LLM Tool：<https://docs.astrbot.app/dev/star/guides/ai.html>
- 发送消息：<https://docs.astrbot.app/dev/star/guides/send-message.html>
- 插件存储：<https://docs.astrbot.app/dev/star/guides/storage.html>
- 发布插件：<https://docs.astrbot.app/dev/star/plugin-publish.html>
- AstrBot 源码：<https://github.com/AstrBotDevs/AstrBot>

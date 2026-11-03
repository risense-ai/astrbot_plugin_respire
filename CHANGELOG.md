# Changelog

## 0.2.0 — 2026-11-03

- 新增 `isolation_mode=bot`：按 `platform_id + self_id` 将每个机器人映射到独立 rsrs space/account。
- 每次 rsrs 子进程注入 `RSRS_DATA_DIR` 与可选 `RSRS_M3_DIR`；不调用 `rsrs space use`，不自动创建 space。
- `bot_profiles` 支持精确 `bot:<platform>:<self_id>`、`bot:<platform>:*` 与 `default`。
- 未匹配/space 不存在/无 session 时默认 fail closed；`profile_fallback=main` 可落主库。
- `scope_mode` 新增 `persona` / `bot_persona`；`persona` 为机器人 profile 内的人格软隔离。
- `/respire status` 显示当前 profile/space；新增 space 列表缓存与共享 BGE-M3 模型目录配置。
- 新增 16 项隔离测试（桩 CLI 记录 `RSRS_DATA_DIR` / `RSRS_M3_DIR`，覆盖匹配、fallback、缺 space、无 session、缓存、中文人格）。

## 0.1.0 — 2026-11-03

- 首个版本：AstrBot `on_llm_request` 每轮标题档召回，动态内容走临时 user content part。
- 六个 LLM 工具：`respire_recall` / `respire_show` / `respire_remember` / `respire_update` / `respire_merge` / `respire_attach`。
- `/respire` 指令组（别名 `/rsrs`）：status / recall / show / remember / forget / help。
- 判后即存：important 必须有可读父节点；pending 不写入；成功写入后 `show` 回读核验。
- fail-open、最小子进程环境、凭据模式跳过、日志脱敏。
- 默认仅管理员、群聊关闭；提供 `global` / `sender` / `session` 三种软作用域。
- 桩测试、真实 rsrs 只读测试、AstrBot 真实加载测试。

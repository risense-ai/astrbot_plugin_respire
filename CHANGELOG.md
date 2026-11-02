# Changelog

## 0.1.0 — 2026-11-03

- 首个版本：AstrBot `on_llm_request` 每轮标题档召回，动态内容走临时 user content part。
- 六个 LLM 工具：`respire_recall` / `respire_show` / `respire_remember` / `respire_update` / `respire_merge` / `respire_attach`。
- `/respire` 指令组（别名 `/rsrs`）：status / recall / show / remember / forget / help。
- 判后即存：important 必须有可读父节点；pending 不写入；成功写入后 `show` 回读核验。
- fail-open、最小子进程环境、凭据模式跳过、日志脱敏。
- 默认仅管理员、群聊关闭；提供 `global` / `sender` / `session` 三种软作用域。
- 桩测试、真实 rsrs 只读测试、AstrBot 真实加载测试。

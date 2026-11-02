# Respire CLI（rsrs）集成笔记

> 本机版本：`rsrs` 1.0.18（2026-11-03；当时 npm 最新 1.0.19）
> 用途：给 AstrBot 插件提供记忆读写；插件只通过子进程调用 CLI，不依赖 Python SDK。

## 1. 插件实际使用的命令

```bash
rsrs status --json
rsrs recall "<query>" --titles --json --limit 5 [--project <scope>]
rsrs show <id> --json
rsrs chain <id> --depth 1..3 --json
rsrs candidates "<content>" --json
rsrs remember "<content>" --title "<title>" --importance important|trivial \
  --type context --json [--parent <id>] [--tags "<tags>"] [--project <scope>]
rsrs update <id> --content "<content>" --title "<title>" --importance important|trivial --json
rsrs attach <child> --parent <parent> --json
rsrs forget <id> --json
rsrs taxonomy --list --json
rsrs tree --outline --json        # 只读排障用；输出可能很大
```

未使用 `--force`。未使用 `--direct`。未启动/停止 runtime。

## 2. JSON 信封

所有 `--json` 命令统一返回：

```json
{
  "command": "recall",
  "status": "ok | warn | pending | skip | fail",
  "summary": {},
  "items": [{"name": "...", "status": "ok", "value": "..."}],
  "actions": ["..."],
  "errors": ["..."],
  "details": {},
  "related": []
}
```

要点：

- 失败也可能以 JSON 返回，`status: "fail"`，不能只看退出码。
- 进度写 stderr，JSON 写 stdout；不要 `2>&1`。
- `parse_envelope` 应先用 `json.loads`，失败再用 `JSONDecoder().raw_decode()` 容错尾部人类文本。
- `status=pending` 表示**没有写入**，不是成功。

## 3. recall --titles

`details` 是行数组：

```json
{
  "id": "6763b354-49a1-4963-b032-9c418854edad",
  "title": "respire-hermes 0.1.0 建成：...",
  "score": 0.505,
  "superseded_by": null
}
```

`items` 是展示行：

```json
{"name": "6763b354", "status": "ok", "value": "0.505 respire-hermes ..."}
```

插件只用 `details`；`items` 仅作回退。`score` 是相关性，不是置信度。`superseded_by` 非空表示已被替代。

## 4. show / chain

`show`：

```json
{
  "details": {
    "entry": {
      "id": "...",
      "title": "...",
      "content": "...",
      "importance": "important",
      "updated_at": "...",
      "parent_id": "..."
    },
    "ancestors": [{"id": "...", "title": "..."}],
    "children": [{"id": "...", "title": "...", "summary": "..."}]
  }
}
```

`chain`：

```json
{
  "details": {
    "entry": {...},
    "ancestors": [...],
    "descendants": [...]
  }
}
```

插件 `respire_show(entry_id, chain_depth)`：`depth=0` 调 `show`，`1..3` 调 `chain`。`chain` 的正文可能很长，插件只格式化本条正文 + 上下游标题，详细内容让模型再 `show`。

## 5. remember 的判后即存

### 5.1 无 `--parent` 且 importance=important

CLI 会先做内部候选检索：

- 高度相似 → `status=pending`，`summary.state=candidates`，`items` 列出 merge 候选；
- 中等相似 → `status=pending`，`summary.state=parent_candidates`；
- 没有候选 → 正常写入（可能成为根节点）。

插件在调用 `remember` 前先调 `candidates`：

```bash
rsrs candidates "<content>" --json
```

候选形状：

```json
{
  "details": {
    "merge": [{"entry": {"id": "...", "title": "..."}, "score": 0.9}],
    "parent": [{"entry": {...}, "score": 0.7}]
  }
}
```

有候选：返回候选，不写入。
无候选：仍不写 important，要求模型提供真实父节点；这样避免产生孤儿根。

### 5.2 有 `--parent`

插件先 `show <parent>` 核验可读，再 `remember ... --parent <parent>`。
写入成功后，用 `summary.id` 再 `show` 回读，核对：

- `summary.id == entry.id`
- `entry.title == 期望标题`
- `entry.content.strip() == 期望正文.strip()`

### 5.3 `--importance trivial`

trivial 走日记区，不要求父节点。CLI 在 `diary_mode=concise` 且无标题时会自动并入当日「活动轨迹」；插件仍然生成标题以便回执。日记条不参与主区 recall。

## 6. update / merge / attach

### update

```bash
rsrs update <id> --content "..." --title "..." --importance important --json
```

`summary.id` 是目标 id。插件在读之前先读当前条目：

- 如果升级为 important，要求现有 `parent_id` 非空；
- 更新后再次 `show` 回读，用更新后的标题/正文核对。

### merge

```bash
rsrs remember "<combined>" --title "<title>" --importance important \
  --merge-ids "id1,id2" --json [--parent <id>]
```

CLI 会 tombstone 旧条，继承第一个来源的因果链。插件检查：

- 至少一个来源；
- 第一个来源可读；
- 第一个来源是根时，必须显式传 `parent`，否则拒绝。

### attach

```bash
rsrs attach <child> --parent <parent> --json
```

插件先 `show` 两端，确认可读再 attach。

## 7. 作用域

`rsrs recall --project <project>` 是检索过滤；`rsrs remember --project <project>` 写入 project 字段。

AstrBot 插件提供：

- `global`：不传 `--project`；
- `sender`：`astrbot:<platform>:user:<sender>`；
- `session`：`astrbot:session:<umo>`。

注意：`--project` 是软隔离，不是权限边界。其他不带 `--project` 的 agent 仍可能召回全部。需要加密级隔离时用独立 `RSRS_DATA_DIR` / 独立账号。

## 8. 超时与冷启动

本机实测：

- `rsrs status` 冷启动可能数秒；
- `rsrs recall` 热态亚秒，冷态可达十余秒；
- `rsrs candidates` 首次调用可能需要加载 BGE 模型，约 30 秒量级；
- `rsrs tree --outline --json` 2000+ 条库约 579 KB。

插件默认：

- recall 14s；
- write / candidates 20s；
- status 10s；
- 所有超时都 fail-open，返回空或“结果未知”。

如果部署机器更慢，可在 WebUI 调大 `recall_timeout` / `write_timeout`。

## 9. 数据目录与测试

- 默认数据目录：`~/.rsrs`（本机实际 `~/.rsrs/accounts/ShiningRay`）。
- `RSRS_DATA_DIR` 可覆盖数据目录；适合隔离测试。
- 但全新空目录没有本地 session，`remember`/`recall` 会报：

```text
no local session: rsrs keygen --pass <password> to start fully local ...
```

- 本机尝试 `RSRS_DATA_DIR=/tmp/xxx rsrs keygen --pass test --json` 在非 TTY 下 120 秒未返回（runtime 启动但无 session）。因此自动化写入测试应使用桩 CLI，或复制现有可写库并人工清理。
- 真实只读测试可用现有库：`status`、`recall`、`show`、`taxonomy`。

## 10. 敏感信息

- `rsrs` 本地库是加密的，但插件不应把密码、token、私钥写进正文或查询词。
- 插件在 `contains_secret()` 命中时跳过 recall；不记录查询原文。
- 日志只记录事件码、长度、脱敏后的错误。
- 子进程只拿最小环境，不透传宿主 API key。
- 不要把 `session.json`、Account Secret、恢复码写入仓库或日志。

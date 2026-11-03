"""Tests for astrbot_plugin_respire.

Layers:
* pure formatting / parsing helpers (no I/O);
* the plugin against a stateful stub `rsrs` CLI (hooks, tools, commands);
* the real `rsrs` CLI in read-only mode (skipped when rsrs is absent).

Real `PluginManager.load()` verification lives in test_real_load.py.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as plugin_module
from conftest import (
    STUB_RSRS,
    collect_results,
    make_event,
    make_plugin,
    part_text,
    recall_parts,
    result_text,
)
from main import (
    DEFAULTS,
    NUDGE_TAG,
    RECALL_TAG,
    RULES_TAG,
    STATUS_TAG,
    STORE_REQUEST_TAG,
    _parse_command_flags,
    as_float,
    as_int,
    contains_secret,
    extract_title,
    find_rsrs,
    format_candidates,
    format_recall,
    format_show,
    parse_envelope,
    redact,
    sidecar_env,
    truncate,
)

from astrbot.core.provider.entities import ProviderRequest


# ---------------------------------------------------------------------------
# Stub CLI plumbing
# ---------------------------------------------------------------------------


@pytest.fixture
def stub(tmp_path, monkeypatch):
    state_dir = tmp_path / "rsrs-state"
    state_dir.mkdir()
    monkeypatch.setenv("RSRS_STUB_STATE", str(state_dir))
    STUB_RSRS.chmod(0o755)
    return SimpleNamespace(path=str(STUB_RSRS), state_dir=state_dir)


def load_state(stub) -> dict:
    path = stub.state_dir / "state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(stub, state: dict) -> None:
    stub.state_dir.mkdir(parents=True, exist_ok=True)
    (stub.state_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def add_entry(stub, entry_id: str, *, title: str = "父条目", content: str = "父内容", **extra):
    state = load_state(stub)
    state.setdefault("entries", {})[entry_id] = {
        "id": entry_id,
        "title": title,
        "content": content,
        "importance": "important",
        "parent_id": "",
        "updated_at": "2026-11-03T00:00:00Z",
        **extra,
    }
    save_state(stub, state)
    return state["entries"][entry_id]


PARENT_ID = "11111111-2222-3333-4444-555555555555"
CHILD_ID = "99999999-8888-7777-6666-555555555555"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestFormatRecall:
    def make_doc(self):
        return {
            "status": "ok",
            "details": [
                {"id": "aaaaaaaa-0000-0000-0000-000000000001", "title": "第一条记忆", "score": 0.9},
                {"id": "bbbbbbbb-0000-0000-0000-000000000002", "title": "第二条记忆", "score": 0.1},
            ],
        }

    def test_threshold_filters_low_scores(self):
        block = format_recall(self.make_doc(), limit=5, threshold=0.5)
        assert RECALL_TAG in block
        assert "第一条记忆" in block
        assert "第二条记忆" not in block

    def test_limit_caps_rows(self):
        block = format_recall(self.make_doc(), limit=1, threshold=0.0)
        assert "第一条记忆" in block
        assert "第二条记忆" not in block

    def test_budget_stops_before_overflow(self):
        # The fixed English header alone is ~210 chars; a row no longer fits.
        block = format_recall(self.make_doc(), limit=5, budget_chars=230)
        assert block == ""
        block = format_recall(self.make_doc(), limit=5, budget_chars=1200)
        assert "第一条记忆" in block

    def test_bad_status_yields_empty(self):
        assert format_recall({"status": "fail"}, limit=5) == ""
        assert format_recall(None, limit=5) == ""

    def test_items_fallback_without_details(self):
        doc = {
            "status": "ok",
            "items": [
                {"name": "cccc3333", "status": "ok", "value": "0.500 回退标题"},
            ],
        }
        block = format_recall(doc, limit=5)
        assert "回退标题" in block
        assert "(cccc3333)" in block

    def test_superseded_marker(self):
        doc = {
            "status": "ok",
            "details": [
                {"id": "dddd4444", "title": "旧条", "score": 0.8, "superseded_by": "eeee5555"}
            ],
        }
        assert "→superseded" in format_recall(doc, limit=5)


class TestParseEnvelope:
    def test_pure_json(self):
        assert parse_envelope('{"status":"ok"}') == {"status": "ok"}

    def test_trailing_human_text_tolerated(self):
        text = '{"status":"ok","summary":{}}\nSynced in 1.2s\n'
        assert parse_envelope(text)["status"] == "ok"

    def test_empty_and_non_dict(self):
        assert parse_envelope("") is None
        assert parse_envelope("   \n") is None
        assert parse_envelope("[1,2,3]") is None
        assert parse_envelope("not json at all") is None


class TestExtractTitle:
    def test_sentence_end_wins(self):
        assert extract_title("模型决定用 Rust 重写解析器。后续再讨论性能。") == "模型决定用 Rust 重写解析器。"

    def test_comma_cut(self):
        title = extract_title("respire 完成插件骨架，测试全绿，等待发版")
        assert title.endswith("，") or "，" in title

    def test_fallback_truncates(self):
        long_text = "x" * 200
        assert len(extract_title(long_text, limit=70)) == 70

    def test_empty(self):
        assert extract_title("   ") == ""


class TestSecretGuards:
    def test_contains_secret_hits(self):
        assert contains_secret("password=hunter2")
        assert contains_secret("密码：123456")
        assert contains_secret("token sk-abcdefgh12345678 in text")
        assert contains_secret("-----BEGIN RSA PRIVATE KEY-----")
        assert contains_secret("api_key = abcdef123456")

    def test_contains_secret_passes(self):
        assert not contains_secret("hello world")
        assert not contains_secret("recall the api design decision")
        assert not contains_secret("记：今天讨论了 tokenization 的方案")

    def test_sidecar_env_filters_secrets(self, monkeypatch):
        monkeypatch.setenv("RSRS_DATA_DIR", "/tmp/rsrs-data")
        monkeypatch.setenv("RSRS_AUTH_TOKEN", "should-not-leak")
        monkeypatch.setenv("RESPIRE_BIN", "/usr/bin/respire")
        monkeypatch.setenv("MY_API_KEY", "should-not-leak")
        monkeypatch.setenv("SOME_PASSWORD", "should-not-leak")
        env = sidecar_env()
        assert env["RSRS_DATA_DIR"] == "/tmp/rsrs-data"
        assert env["RESPIRE_BIN"] == "/usr/bin/respire"
        assert env["PATH"] == os.environ["PATH"]
        for forbidden in ("RSRS_AUTH_TOKEN", "MY_API_KEY", "SOME_PASSWORD"):
            assert forbidden not in env

    def test_redact_masks(self):
        assert "hunter2" not in redact("password=hunter2")
        assert "sk-abcdefgh12345678" not in redact("token sk-abcdefgh12345678 end")


class TestSmallHelpers:
    def test_parse_envelope_roundtrip(self):
        pass  # covered above; kept for section symmetry

    def test_as_int_and_float(self):
        assert as_int("7", 3) == 7
        assert as_int("bad", 3) == 3
        assert as_int("0", 3, minimum=1) == 3
        assert as_int("99", 3, maximum=20) == 20
        assert as_float("0.4", 0.0) == 0.4
        assert as_float("x", 0.5) == 0.5

    def test_truncate(self):
        assert truncate("abcdef", 10) == "abcdef"
        assert truncate("abcdef", 4).endswith("…")
        assert len(truncate("abcdef", 4)) == 4

    def test_short_id(self):
        assert plugin_module.short_id("aaaaaaaa-bbbb") == "aaaaaaaa"

    def test_parse_command_flags(self):
        values, content = _parse_command_flags('--parent abc --title "我的标题" 正文 一')
        assert values["parent"] == "abc"
        assert values["title"] == "我的标题"
        assert content == "正文 一"

        values, content = _parse_command_flags("--trivial --diary 日记内容")
        assert values["trivial"] == "1"
        assert values["diary"] == "1"
        assert content == "日记内容"

        values, _ = _parse_command_flags("--parent=xyz body")
        assert values["parent"] == "xyz"

    def test_find_rsrs_precedence(self, monkeypatch, stub):
        assert find_rsrs(stub.path) == stub.path
        monkeypatch.setenv("RESPITE_RSRS", stub.path)
        assert find_rsrs("") == stub.path


# ---------------------------------------------------------------------------
# Access control / scoping (sync)
# ---------------------------------------------------------------------------


class TestAccessControl:
    def test_disabled(self):
        plugin = make_plugin({"enabled": False})
        event = make_event()
        allowed, reason = plugin._memory_allowed(event)
        assert not allowed and "disabled" in reason

    def test_group_off_by_default(self):
        plugin = make_plugin({})
        event = make_event(group=True, admin=True)
        allowed, reason = plugin._memory_allowed(event)
        assert not allowed and "group" in reason

    def test_group_on_with_flag(self):
        plugin = make_plugin({"group_memory": True})
        assert plugin._memory_allowed(make_event(group=True, admin=True))[0]

    def test_admin_mode_rejects_member(self):
        plugin = make_plugin({})
        allowed, reason = plugin._memory_allowed(make_event(admin=False))
        assert not allowed and "administrator" in reason

    def test_allowlist(self):
        plugin = make_plugin({"access_mode": "allowlist", "allowed_users": ["user1"]})
        assert plugin._memory_allowed(make_event(admin=False, sender_id="user1"))[0]
        allowed, _ = plugin._memory_allowed(make_event(admin=False, sender_id="stranger"))
        assert not allowed

    def test_all_mode(self):
        plugin = make_plugin({"access_mode": "all"})
        assert plugin._memory_allowed(make_event(admin=False))[0]

    def test_scope_sender_project(self):
        plugin = make_plugin({"scope_mode": "sender"})
        assert plugin._scope_project(make_event(platform="test", sender_id="user1")) == (
            "astrbot:test:user:user1"
        )

    def test_scope_session_project(self):
        plugin = make_plugin({"scope_mode": "session"})
        project = plugin._scope_project(make_event(platform="test", session="s1"))
        assert project.startswith("astrbot:session:")

    def test_scope_sanitizes(self):
        plugin = make_plugin({"scope_mode": "sender"})
        project = plugin._scope_project(make_event(platform="test", sender_id="a/b:c d"))
        assert "/" not in project and " " not in project

    def test_scope_global_empty(self):
        plugin = make_plugin({})
        assert plugin._scope_project(make_event()) == ""


# ---------------------------------------------------------------------------
# Hooks (on_llm_request / after_message_sent)
# ---------------------------------------------------------------------------


class TestHooks:
    async def test_injects_rules_and_recall(self, stub):
        save_state(stub, {"recall_rows": [
            {"id": "aaaaaaaa-0000", "title": "插件骨架建成", "score": 0.7},
        ]})
        plugin = make_plugin({"rsrs_path": stub.path})
        event = make_event()
        req = ProviderRequest(prompt="插件进展如何")
        await plugin.on_llm_request(event, req)
        assert RULES_TAG in req.system_prompt
        parts = recall_parts(req)
        assert len(parts) == 1
        assert "插件骨架建成" in parts[0]

    async def test_recall_part_marked_temp(self, stub):
        save_state(stub, {"recall_rows": [
            {"id": "aaaaaaaa-0000", "title": "临时标记", "score": 0.7},
        ]})
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="再看一眼")
        await plugin.on_llm_request(make_event(), req)
        part = req.extra_user_content_parts[-1]
        assert getattr(part, "_no_save", False) is True or (
            isinstance(part, dict) and part.get("_no_save")
        )

    async def test_dedupes_same_query(self, stub):
        save_state(stub, {"recall_rows": [
            {"id": "aaaaaaaa-0000", "title": "去重", "score": 0.7},
        ]})
        plugin = make_plugin({"rsrs_path": stub.path})
        event = make_event()
        req = ProviderRequest(prompt="同样的问题")
        await plugin.on_llm_request(event, req)
        await plugin.on_llm_request(event, req)
        assert len(recall_parts(req)) == 1

    async def test_group_chat_untouched(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="群聊问题")
        await plugin.on_llm_request(make_event(group=True), req)
        assert RULES_TAG not in req.system_prompt
        assert req.extra_user_content_parts == []

    async def test_non_admin_untouched(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="普通用户")
        await plugin.on_llm_request(make_event(admin=False), req)
        assert RULES_TAG not in req.system_prompt
        assert req.extra_user_content_parts == []

    async def test_disabled_untouched(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path, "enabled": False})
        req = ProviderRequest(prompt="关闭时")
        await plugin.on_llm_request(make_event(), req)
        assert RULES_TAG not in req.system_prompt

    async def test_nomem_skips_recall_but_keeps_rules(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="/nomem 不要召回")
        await plugin.on_llm_request(make_event(), req)
        assert RULES_TAG in req.system_prompt
        assert recall_parts(req) == []

    async def test_store_prefix_becomes_store_request(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="记：今天定了插件 0.1.0 版本号")
        await plugin.on_llm_request(make_event(), req)
        texts = [part_text(part) for part in req.extra_user_content_parts]
        assert any(STORE_REQUEST_TAG in text for text in texts)
        assert all(RECALL_TAG not in text for text in texts)

    async def test_secret_query_skips_recall(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="帮我看看 password=abc123456 是什么")
        await plugin.on_llm_request(make_event(), req)
        assert recall_parts(req) == []

    async def test_short_prompt_skips_recall(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        req = ProviderRequest(prompt="好")
        await plugin.on_llm_request(make_event(), req)
        assert recall_parts(req) == []

    async def test_engine_missing_adds_status_note(self, stub, monkeypatch):
        monkeypatch.setattr(plugin_module, "find_rsrs", lambda configured="": None)
        plugin = make_plugin({"rsrs_path": ""})
        await plugin._refresh_probe(force=True)  # normally done by initialize()
        req = ProviderRequest(prompt="引擎在吗")
        await plugin.on_llm_request(make_event(), req)
        assert STATUS_TAG in req.system_prompt

    async def test_store_nudge_fires(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path, "store_nudge_every": 1, "nudge_min_interval": 60})
        plugin._turns = 1
        plugin._last_store_at = 0.0
        req = ProviderRequest(prompt="催一下存储")
        await plugin.on_llm_request(make_event(), req)
        texts = [part_text(part) for part in req.extra_user_content_parts]
        assert any(NUDGE_TAG in text for text in texts)

    async def test_after_message_sent_counts(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        await plugin.after_message_sent(make_event())
        await plugin.after_message_sent(make_event())
        assert plugin._turns == 2


# ---------------------------------------------------------------------------
# LLM tools
# ---------------------------------------------------------------------------


class TestTools:
    async def test_recall_returns_block(self, stub):
        save_state(stub, {"recall_rows": [
            {"id": "aaaaaaaa-0000", "title": "工具召回", "score": 0.8},
        ]})
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_recall(make_event(), "工具召回", 5)
        assert RECALL_TAG in out and "工具召回" in out

    async def test_recall_empty_message(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_recall(make_event(), "查无此条", 5)
        assert "empty result does not prove absence" in out

    async def test_recall_secret_refused(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_recall(make_event(), "password=abc123456", 5)
        assert "not sent to memory" in out

    async def test_recall_rejects_non_admin(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_recall(make_event(admin=False), "query", 5)
        assert "administrator" in out

    async def test_show_entry(self, stub):
        add_entry(stub, PARENT_ID, title="展示条目", content="完整正文内容")
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_show(make_event(), PARENT_ID, 0)
        assert "展示条目" in out and "完整正文内容" in out

    async def test_show_chain(self, stub):
        add_entry(stub, PARENT_ID)
        add_entry(stub, CHILD_ID, parent_id=PARENT_ID)
        state = load_state(stub)
        state["chain_ancestors"] = [{"id": "root0000", "title": "根因条目"}]
        state["chain_descendants"] = [{"id": CHILD_ID[:8], "title": "下游条目"}]
        save_state(stub, state)
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_show(make_event(), PARENT_ID, 2)
        assert "根因条目" in out and "下游条目" in out

    async def test_show_missing(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_show(make_event(), "deadbeef", 0)
        assert "read failed" in out

    async def test_remember_without_parent_previews(self, stub):
        state = {"candidates": {
            "merge": [{"entry": {"id": "merge0001", "title": "相似旧条"}, "score": 0.91}],
            "parent": [],
        }}
        save_state(stub, state)
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_remember(make_event(), "新的重要事实", title="新事实标题")
        assert "Nothing was written" in out
        assert "相似旧条" in out
        assert load_state(stub)["entries"] == {}

    async def test_remember_important_without_candidates_still_blocked(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_remember(make_event(), "孤儿内容", title="孤儿标题")
        assert "Nothing was written" in out
        assert "parent" in out
        assert load_state(stub)["entries"] == {}

    async def test_remember_with_parent_writes_and_verifies(self, stub):
        add_entry(stub, PARENT_ID, title="挂靠父条", content="父正文")
        plugin = make_plugin({"rsrs_path": stub.path})
        body = "【前因】测试\n【行为】写入\n【后果】验证"
        out = await plugin.tool_remember(
            make_event(), body, title="带父写入标题", parent=PARENT_ID
        )
        assert "Saved" in out and "Read-back verified" in out
        entries = load_state(stub)["entries"]
        assert len(entries) == 2
        child = next(entry for entry in entries.values() if entry["id"] != PARENT_ID)
        assert child["parent_id"] == PARENT_ID
        assert child["content"] == body

    async def test_remember_bad_parent_refused(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_remember(
            make_event(), "内容", title="标题", parent="does-not-exist"
        )
        assert "Nothing was written" in out
        assert load_state(stub).get("entries", {}) == {}

    async def test_remember_trivial_needs_no_parent(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_remember(
            make_event(), "日记内容", title="流水标题", importance="trivial"
        )
        assert "Saved" in out
        entries = load_state(stub)["entries"]
        assert len(entries) == 1
        assert next(iter(entries.values()))["importance"] == "trivial"

    async def test_remember_rejects_bad_importance(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_remember(make_event(), "内容", importance="vital")
        assert "importance must be" in out

    async def test_update_promote_root_refused(self, stub):
        add_entry(stub, PARENT_ID, parent_id="")
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_update(make_event(), PARENT_ID, importance="important")
        assert "Cannot promote" in out
        assert load_state(stub)["entries"][PARENT_ID]["importance"] == "important"

    async def test_update_content_with_readback(self, stub):
        add_entry(stub, CHILD_ID, parent_id=PARENT_ID, content="旧正文")
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_update(
            make_event(), CHILD_ID, content="新正文", title="新标题"
        )
        assert "Updated" in out and "Read-back verified" in out
        entry = load_state(stub)["entries"][CHILD_ID]
        assert entry["content"] == "新正文" and entry["title"] == "新标题"

    async def test_update_nothing_to_do(self, stub):
        add_entry(stub, CHILD_ID, parent_id=PARENT_ID)
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_update(make_event(), CHILD_ID)
        assert "Nothing to update" in out

    async def test_update_missing(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_update(make_event(), "deadbeef", content="x")
        assert "Cannot update" in out

    async def test_merge_root_source_requires_parent(self, stub):
        add_entry(stub, PARENT_ID, parent_id="")
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_merge(
            make_event(), "合并正文", "合并标题", PARENT_ID, parent=""
        )
        assert "root" in out
        assert len(load_state(stub)["entries"]) == 1

    async def test_merge_with_parent(self, stub):
        add_entry(stub, PARENT_ID)
        add_entry(stub, CHILD_ID, parent_id=PARENT_ID)
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_merge(
            make_event(), "合并后的正文", "合并后的标题", f"{PARENT_ID},{CHILD_ID}",
            parent=PARENT_ID,
        )
        assert "Saved" in out and "Read-back verified" in out
        entries = load_state(stub)["entries"]
        merged = next(
            entry for entry in entries.values() if entry.get("merged_from")
        )
        assert merged["parent_id"] == PARENT_ID
        assert entries[PARENT_ID].get("superseded_by") == merged["id"]

    async def test_attach(self, stub):
        add_entry(stub, PARENT_ID)
        add_entry(stub, CHILD_ID)
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_attach(make_event(), CHILD_ID, PARENT_ID)
        assert "Attached" in out
        assert load_state(stub)["entries"][CHILD_ID]["parent_id"] == PARENT_ID

    async def test_attach_missing_endpoint(self, stub):
        add_entry(stub, PARENT_ID)
        plugin = make_plugin({"rsrs_path": stub.path})
        out = await plugin.tool_attach(make_event(), "deadbeef", PARENT_ID)
        assert "could not be read" in out

    async def test_scope_sender_passes_project(self, stub):
        save_state(stub, {"recall_rows": [
            {"id": "aaaaaaaa-0000", "title": "项目隔离", "score": 0.8},
        ]})
        plugin = make_plugin({"rsrs_path": stub.path, "scope_mode": "sender"})
        event = make_event(platform="test", sender_id="user1")
        await plugin.tool_recall(event, "项目隔离", 5)
        logged = load_state(stub)["log"]
        assert any("--project" in argv and "astrbot:test:user:user1" in argv for argv in logged)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class TestCommands:
    async def test_help(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(plugin.command_help(make_event()))
        text = result_text(results[0])
        assert "/respire status" in text and "/respire forget" in text

    async def test_status(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(plugin.command_status(make_event()))
        text = result_text(results[0])
        assert "unlocked: True" in text

    async def test_recall(self, stub):
        save_state(stub, {"recall_rows": [
            {"id": "aaaaaaaa-0000", "title": "指令召回", "score": 0.8},
        ]})
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(plugin.command_recall(make_event(), "指令召回"))
        assert "指令召回" in result_text(results[0])

    async def test_recall_empty_message(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(plugin.command_recall(make_event(), "无结果查询"))
        assert "empty result does not prove absence" in result_text(results[0])

    async def test_recall_secret_refused(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(
            plugin.command_recall(make_event(), "password=abc123456")
        )
        assert "credential" in result_text(results[0])

    async def test_remember_flags(self, stub):
        add_entry(stub, PARENT_ID)
        plugin = make_plugin({"rsrs_path": stub.path})
        spec = f'--parent {PARENT_ID} --title "指令写入标题" 指令写入正文'
        results = await collect_results(plugin.command_remember(make_event(), spec))
        assert "Saved" in result_text(results[0])

    async def test_remember_trivial_flag(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(
            plugin.command_remember(make_event(), "--trivial 随手记一条")
        )
        assert "Saved" in result_text(results[0])
        entry = next(iter(load_state(stub)["entries"].values()))
        assert entry["importance"] == "trivial"

    async def test_remember_default_important_blocked(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(
            plugin.command_remember(make_event(), "默认重要无父")
        )
        assert "Nothing was written" in result_text(results[0])
        assert load_state(stub).get("entries", {}) == {}

    async def test_remember_usage(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(plugin.command_remember(make_event(), ""))
        assert "Usage:" in result_text(results[0])

    async def test_show(self, stub):
        add_entry(stub, PARENT_ID, title="指令展示", content="指令正文")
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(plugin.command_show(make_event(), PARENT_ID))
        assert "指令正文" in result_text(results[0])

    async def test_forget_requires_admin(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(
            plugin.command_forget(make_event(admin=False), f"{PARENT_ID} --yes")
        )
        assert "administrator" in result_text(results[0])

    async def test_forget_requires_yes(self, stub):
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(
            plugin.command_forget(make_event(admin=True), PARENT_ID)
        )
        assert "Usage:" in result_text(results[0])

    async def test_forget_ok(self, stub):
        add_entry(stub, PARENT_ID)
        plugin = make_plugin({"rsrs_path": stub.path})
        results = await collect_results(
            plugin.command_forget(make_event(admin=True), f"{PARENT_ID} --yes")
        )
        assert "Deleted" in result_text(results[0])
        assert load_state(stub)["entries"] == {}

    async def test_status_engine_missing(self, stub, monkeypatch):
        monkeypatch.setattr(plugin_module, "find_rsrs", lambda configured="": None)
        plugin = make_plugin({"rsrs_path": ""})
        results = await collect_results(plugin.command_status(make_event()))
        assert "not found" in result_text(results[0])


# ---------------------------------------------------------------------------
# Formatting for receipts and status
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_format_show_renders_entry_and_relatives(self):
        doc = {
            "status": "ok",
            "details": {
                "entry": {
                    "id": "aaaaaaaa-0000",
                    "title": "标题",
                    "content": "正文",
                    "importance": "important",
                    "updated_at": "2026-11-03",
                    "parent_id": "bbbbbbbb-0000",
                },
                "ancestors": [{"id": "bbbbbbbb-0000", "title": "祖先"}],
                "children": [{"id": "cccccccc-0000", "title": "孩子", "summary": "s"}],
            },
        }
        text = format_show(doc)
        assert "【标题】" in text and "正文" in text
        assert "Upstream" in text and "Downstream" in text

    def test_format_show_bad(self):
        assert format_show({"status": "fail"}) == ""

    def test_format_candidates(self):
        doc = {
            "details": {
                "merge": [{"entry": {"id": "m1", "title": "候选一"}, "score": 0.9}],
                "parent": [{"entry": {"id": "p1", "title": "候选父"}, "score": 0.6}],
            }
        }
        text = format_candidates(doc)
        assert "Nothing was written" in text
        assert "候选一" in text and "候选父" in text
        assert format_candidates({}) == ""

    def test_store_result_pending(self):
        out = plugin_module._format_store_result(
            {"status": "pending", "details": {}}, "t", "c", None
        )
        assert "Nothing was written" in out

    def test_store_result_unknown(self):
        out = plugin_module._format_store_result(None, "t", "c", None)
        assert "unknown" in out

    def test_store_result_bad_id(self):
        out = plugin_module._format_store_result(
            {"status": "ok", "summary": {"id": "not-an-id"}}, "t", "c", None
        )
        assert "no valid entry id" in out


# ---------------------------------------------------------------------------
# Real rsrs CLI (read-only; skipped when the engine is absent)
# ---------------------------------------------------------------------------


def _real_rsrs() -> str | None:
    configured = shutil.which("rsrs")
    if configured:
        return configured
    candidate = Path.home() / ".local" / "bin" / "rsrs"
    return str(candidate) if candidate.exists() else None


@pytest.mark.skipif(
    _real_rsrs() is None or bool(os.environ.get("RSRS_TEST_SKIP_REAL")),
    reason="real rsrs CLI not installed (or RSRS_TEST_SKIP_REAL set)",
)
class TestRealRsrsReadOnly:
    async def test_status(self):
        plugin = make_plugin({})
        doc, error = await plugin._run(["status", "--json"], 30)
        assert error is None
        assert doc["status"] == "ok"
        assert "unlocked" in plugin_module.format_status(doc)

    async def test_recall_readonly(self):
        plugin = make_plugin({})
        event = make_event()
        block = await plugin._recall(event, "respire 记忆", 3)
        assert isinstance(block, str)

    async def test_candidates_readonly(self):
        plugin = make_plugin({})
        doc, error = await plugin._run(["candidates", "respire 测试探针", "--json"], 60)
        assert error is None
        assert isinstance(doc, dict)
        assert "details" in doc


# ---------------------------------------------------------------------------
# Bot-level hard isolation + persona-level soft scoping
# ---------------------------------------------------------------------------


def bot_event(*, platform: str = "test", self_id: str = "bot", persona: str | None = None, **kwargs):
    event = make_event(platform=platform, **kwargs)
    event.message_obj.self_id = self_id
    if persona is not None:
        event.set_extra("_trace_persona_id", persona)
    return event


def make_space_dir(tmp_path: Path, name: str = "botA", *, session: bool = True) -> Path:
    space_dir = tmp_path / name
    space_dir.mkdir(parents=True, exist_ok=True)
    if session:
        (space_dir / "session.json").write_text("{}", encoding="utf-8")
    return space_dir


class TestBotProfileIsolation:
    async def test_bot_profile_sets_rsrs_data_dir(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        model_dir = tmp_path / "models" / "bge-m3"
        save_state(stub, {
            "spaces": [{"name": "botA", "dir": str(space_dir)}],
            "recall_rows": [{"id": "aaaaaaaa-0000", "title": "机器人隔离", "score": 0.8}],
        })
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
            "m3_dir": str(model_dir),
        })
        out = await plugin.tool_recall(bot_event(), "机器人隔离", 5)
        assert RECALL_TAG in out and "机器人隔离" in out
        calls = [
            row for row in load_state(stub)["env_log"]
            if row.get("argv", [""])[0] == "recall"
        ]
        assert calls
        assert calls[-1]["data_dir"] == str(space_dir)
        assert calls[-1]["m3_dir"] == str(model_dir)

    async def test_space_list_is_cached(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {
            "spaces": [{"name": "botA", "dir": str(space_dir)}],
            "recall_rows": [{"id": "aaaaaaaa-0000", "title": "缓存", "score": 0.8}],
        })
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        await plugin.tool_recall(bot_event(), "缓存", 5)
        await plugin.tool_recall(bot_event(), "缓存", 5)
        space_calls = [
            row for row in load_state(stub)["env_log"]
            if row.get("argv", [""])[0] == "space"
        ]
        assert len(space_calls) == 1

    async def test_persona_project_inside_bot_profile(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {
            "spaces": [{"name": "botA", "dir": str(space_dir)}],
            "recall_rows": [{"id": "aaaaaaaa-0000", "title": "人格隔离", "score": 0.8}],
        })
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "scope_mode": "persona",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        await plugin.tool_recall(bot_event(persona="猫娘"), "人格隔离", 5)
        recalls = [
            row["argv"] for row in load_state(stub)["env_log"]
            if row.get("argv", [""])[0] == "recall"
        ]
        assert recalls
        argv = recalls[-1]
        assert "--project" in argv
        assert argv[argv.index("--project") + 1] == "astrbot:persona:猫娘"

    async def test_bot_profile_wildcard_and_default(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        for match in ("bot:test:*", "default"):
            save_state(stub, {
                "spaces": [{"name": "botA", "dir": str(space_dir)}],
                "recall_rows": [{"id": "aaaaaaaa-0000", "title": match, "score": 0.8}],
            })
            plugin = make_plugin({
                "rsrs_path": stub.path,
                "isolation_mode": "bot",
                "bot_profiles": [{"match": match, "space": "botA"}],
            })
            out = await plugin.tool_recall(bot_event(), match, 5)
            assert RECALL_TAG in out, match

    async def test_persona_from_conversation_manager(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {
            "spaces": [{"name": "botA", "dir": str(space_dir)}],
            "recall_rows": [{"id": "aaaaaaaa-0000", "title": "会话人格", "score": 0.8}],
        })
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "scope_mode": "persona",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })

        class Manager:
            async def get_curr_conversation_id(self, umo):
                return "c1"

            async def get_conversation(self, umo, cid):
                return SimpleNamespace(persona_id="猫娘")

        plugin.context = SimpleNamespace(conversation_manager=Manager())
        await plugin.tool_recall(bot_event(), "会话人格", 5)
        recalls = [
            row["argv"] for row in load_state(stub)["env_log"]
            if row.get("argv", [""])[0] == "recall"
        ]
        assert recalls
        assert recalls[-1][recalls[-1].index("--project") + 1] == "astrbot:persona:猫娘"

    async def test_scoped_candidate_preview_uses_project(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {
            "spaces": [{"name": "botA", "dir": str(space_dir)}],
            "recall_rows": [{"id": "aaaaaaaa-0000", "title": "同人格候选", "score": 0.8}],
            "candidates": {"merge": [{"entry": {"id": "bbbbbbbb", "title": "跨人格候选"}, "score": 0.9}]},
        })
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "scope_mode": "persona",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        out = await plugin.tool_remember(
            bot_event(persona="猫娘"), "待存事实", "待存标题", importance="important"
        )
        assert "Scoped candidates" in out
        state = load_state(stub)
        assert not any(row[0] == "candidates" for row in state["log"])
        recall_args = next(row for row in state["log"] if row[0] == "recall")
        assert recall_args[recall_args.index("--project") + 1] == "astrbot:persona:猫娘"

    async def test_merge_keeps_persona_project(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {"spaces": [{"name": "botA", "dir": str(space_dir)}]})
        add_entry(stub, PARENT_ID, title="根父")
        add_entry(stub, CHILD_ID, title="子", parent_id=PARENT_ID)
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "scope_mode": "persona",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        await plugin.tool_merge(
            bot_event(persona="猫娘"),
            "合并正文",
            "合并标题",
            CHILD_ID,
            parent=PARENT_ID,
        )
        remembers = [
            row for row in load_state(stub)["log"]
            if row[0] == "remember" and "--merge-ids" in row
        ]
        assert remembers
        argv = remembers[-1]
        assert argv[argv.index("--project") + 1] == "astrbot:persona:猫娘"

    async def test_status_reports_bot_profile(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {"spaces": [{"name": "botA", "dir": str(space_dir)}]})
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        results = await collect_results(plugin.command_status(bot_event()))
        text = result_text(results[0])
        assert "profile: botA" in text
        assert "unlocked: True" in text

    async def test_unmapped_bot_fails_closed(self, stub, tmp_path):
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [],
            "profile_fallback": "deny",
        })
        out = await plugin.tool_recall(bot_event(), "未映射", 5)
        assert "unavailable" in out
        assert not any(
            row.get("argv", [""])[0] == "recall"
            for row in load_state(stub).get("env_log", [])
        )

    async def test_profile_fallback_main(self, stub, tmp_path, monkeypatch):
        monkeypatch.delenv("RSRS_DATA_DIR", raising=False)
        save_state(stub, {
            "recall_rows": [{"id": "aaaaaaaa-0000", "title": "主库", "score": 0.8}],
        })
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [],
            "profile_fallback": "main",
        })
        out = await plugin.tool_recall(bot_event(), "主库", 5)
        assert RECALL_TAG in out
        calls = [
            row for row in load_state(stub)["env_log"]
            if row.get("argv", [""])[0] == "recall"
        ]
        assert calls
        assert calls[-1]["data_dir"] == ""

    async def test_missing_space_fails_closed(self, stub, tmp_path):
        save_state(stub, {"spaces": [{"name": "main", "dir": str(stub.state_dir)}]})
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [{"match": "bot:test:bot", "space": "missing"}],
        })
        out = await plugin.tool_recall(bot_event(), "缺失", 5)
        assert "does not exist" in out
        assert not any(
            row.get("argv", [""])[0] == "recall"
            for row in load_state(stub).get("env_log", [])
        )

    async def test_uninitialized_space_fails_closed(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path, session=False)
        save_state(stub, {"spaces": [{"name": "botA", "dir": str(space_dir)}]})
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        out = await plugin.tool_recall(bot_event(), "未初始化", 5)
        assert "not initialized" in out

    def test_sidecar_env_profile_overrides_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RSRS_DATA_DIR", "/host/data")
        monkeypatch.setenv("RSRS_M3_DIR", "/host/model")
        profile = tmp_path / "profile"
        model = tmp_path / "model"
        env = sidecar_env(profile_dir=str(profile), m3_dir=str(model))
        assert env["RSRS_DATA_DIR"] == str(profile)
        assert env["RSRS_M3_DIR"] == str(model)

    def test_bot_key_includes_self_id(self):
        plugin = make_plugin({})
        event = bot_event(platform="qq_main", self_id="123456")
        assert plugin._bot_key(event) == "bot:qq_main:123456"

    def test_persona_scope_keeps_unicode(self):
        plugin = make_plugin({"scope_mode": "persona"})
        event = bot_event(persona="猫娘")
        assert plugin._scope_project(event) == "astrbot:persona:猫娘"

    async def test_space_discovery_uses_main_profile(self, stub, tmp_path):
        space_dir = make_space_dir(tmp_path)
        save_state(stub, {"spaces": [{"name": "botA", "dir": str(space_dir)}]})
        plugin = make_plugin({
            "rsrs_path": stub.path,
            "isolation_mode": "bot",
            "bot_profiles": [{"match": "bot:test:bot", "space": "botA"}],
        })
        await plugin._load_space_dirs(force=True)
        space_calls = [
            row for row in load_state(stub)["env_log"]
            if row.get("argv", [""])[0] == "space"
        ]
        assert space_calls
        assert space_calls[-1]["data_dir"] == ""


# ---------------------------------------------------------------------------
# Config schema consistency
# ---------------------------------------------------------------------------


class TestConfigSchema:
    def test_defaults_cover_schema(self):
        schema = json.loads(
            (Path(plugin_module.__file__).parent / "_conf_schema.json").read_text()
        )
        assert set(schema.keys()) == set(DEFAULTS.keys())

    def test_defaults_sane(self):
        assert DEFAULTS["access_mode"] == "admin"
        assert DEFAULTS["group_memory"] is False
        assert DEFAULTS["scope_mode"] in ("global", "sender", "session", "persona", "bot_persona")
        assert DEFAULTS["isolation_mode"] == "off"
        assert DEFAULTS["profile_fallback"] == "deny"
        assert DEFAULTS["space_list_ttl"] >= 10
        assert DEFAULTS["recall_limit"] >= 1
        assert DEFAULTS["store_nudge_every"] >= 0

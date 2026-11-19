import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chatspy_plus as C


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def now(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestActivityRegistry(unittest.TestCase):
    def test_add_and_current(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        e1 = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=clk.now())
        reg.add(e1)
        self.assertEqual(reg.current(clk.now()).chat_id, 10)

    def test_newest_priority(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        e1 = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=1.0)
        e2 = C.make_activity_event(2, 20, C.ActivityType.SENDING_PHOTO, chat_name="B", timestamp=2.0)
        reg.add(e1)
        reg.add(e2)
        cur = reg.current(2.0)
        self.assertEqual(cur.chat_id, 20)

    def test_dedupe_same_key(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        e1 = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=1.0)
        e2 = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=2.0)
        reg.add(e1)
        reg.add(e2)
        self.assertEqual(len(reg.active(2.0)), 1)
        self.assertEqual(reg.current(2.0).timestamp, 2.0)

    def test_expiry_restores_empty(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=0.0)
        reg.add(e)
        clk.advance(10)
        self.assertIsNone(reg.current(clk.now()))
        self.assertEqual(len(reg.active(clk.now())), 0)

    def test_transition_to_previous(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        e1 = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=1.0, ttl=100)
        e2 = C.make_activity_event(2, 20, C.ActivityType.SENDING_PHOTO, chat_name="B", timestamp=2.0, ttl=1.0)
        reg.add(e1)
        reg.add(e2)
        # expire e2
        cur = reg.current(3.5)
        self.assertEqual(cur.chat_id, 10)

    def test_remove(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="A", timestamp=0.0)
        reg.add(e)
        n = reg.remove(participant_id=1)
        self.assertEqual(n, 1)
        self.assertIsNone(reg.current(0.0))


class TestStatusFormatter(unittest.TestCase):
    def test_personal_ru(self):
        fmt = C.StatusFormatter(C.Language.RU)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="Minecraft")
        self.assertEqual(fmt.format(e, False), "печатает в чате «Minecraft»")

    def test_group_ru(self):
        fmt = C.StatusFormatter(C.Language.RU)
        e = C.make_activity_event(1, 10, C.ActivityType.SENDING_PHOTO, chat_name="Друзья",
                                  participant_name="Иван")
        self.assertEqual(fmt.format(e, True), "Иван отправляет фото в чате «Друзья»")

    def test_username_fallback(self):
        fmt = C.StatusFormatter(C.Language.RU)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="X",
                                  participant_username="ivan")
        self.assertEqual(fmt.format(e, True), "@ivan печатает в чате «X»")

    def test_no_chat_name_returns_none(self):
        fmt = C.StatusFormatter(C.Language.RU)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="")
        self.assertIsNone(fmt.format(e, False))

    def test_no_name_group_returns_none(self):
        fmt = C.StatusFormatter(C.Language.RU)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="X")
        self.assertIsNone(fmt.format(e, True))

    def test_english(self):
        fmt = C.StatusFormatter(C.Language.EN)
        e = C.make_activity_event(1, 10, C.ActivityType.TYPING, chat_name="Minecraft")
        self.assertEqual(fmt.personal(e), 'is typing in chat "Minecraft"')


class TestCommonChatsManager(unittest.TestCase):
    def test_excludes_current_dialog(self):
        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(1, "A"), C.ChatInfo(2, "B")]

        mgr = C.CommonChatsManager(Prov())
        chats = mgr.fetch_for_participant(1, exclude_chat_id=1)
        self.assertEqual([c.chat_id for c in chats], [2])

    def test_cache(self):
        calls = {"n": 0}

        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                calls["n"] += 1
                return [C.ChatInfo(5, "X")]

        mgr = C.CommonChatsManager(Prov())
        mgr.fetch_for_participant(1, 99)
        mgr.fetch_for_participant(1, 99)
        self.assertEqual(calls["n"], 1)

    def test_provider_error_logged(self):
        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                raise ValueError("boom")

        logger = C.ErrorLogger()
        mgr = C.CommonChatsManager(Prov(), logger=logger)
        chats = mgr.fetch_for_participant(1, 99)
        self.assertEqual(chats, [])
        self.assertTrue(any(e["category"] == "common_chats" for e in logger.all()))

    def test_parallel_fetch(self):
        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(participant_id * 10, "c")]

        mgr = C.CommonChatsManager(Prov(), max_concurrency=2)
        res = mgr.fetch_for_participants([1, 2, 3], 0)
        self.assertEqual(sorted(res.keys()), [1, 2, 3])


class TestParticipantManager(unittest.TestCase):
    def test_filter_trackable(self):
        ps = [C.ParticipantInfo(1, "Ivan"), C.ParticipantInfo(None, "Bot")]
        out = C.ParticipantManager.filter_trackable(ps)
        self.assertEqual(len(out), 1)

    def test_best_name(self):
        p = C.ParticipantInfo(1, "", "ivan")
        self.assertEqual(p.best_name(), "@ivan")
        p2 = C.ParticipantInfo(2, "Ivan", "ivan")
        self.assertEqual(p2.best_name(), "Ivan")


class TestCompatibility(unittest.TestCase):
    def test_missing_required(self):
        cm = C.CompatibilityManager({"dialog_detection": False, "participants": True,
                                    "common_chats": True, "update_hooks": True, "ui_restore": True})
        self.assertFalse(cm.is_usable())
        self.assertIn("dialog_detection", cm.missing_required())

    def test_all_required_ok(self):
        cm = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        self.assertTrue(cm.is_usable())
        self.assertEqual(cm.missing_required(), [])


class TestSettings(unittest.TestCase):
    def test_default_enabled(self):
        s = C.SettingsManager()
        self.assertTrue(s.is_enabled())

    def test_toggle(self):
        s = C.SettingsManager()
        s.set_enabled(False)
        self.assertFalse(s.is_enabled())


class TestLifecycle(unittest.TestCase):
    def test_close_restores_and_clears(self):
        clk = FakeClock(0)
        reg = C.ActivityRegistry(ttl=6.0, clock=clk.now)
        statuses = {"n": 0, "last": None}

        def set_status(t):
            statuses["last"] = t

        def get_std():
            return "online"

        sc = C.StatusController(set_status, get_std)
        lc = C.LifecycleManager()
        lc.set_status_controller(sc)
        ctx = C.TrackingContext(dialog_id=1, registry=reg)
        lc.open_context(ctx)
        e = C.make_activity_event(1, 2, C.ActivityType.TYPING, chat_name="X", timestamp=0)
        ctx.registry.add(e)
        sc.apply_text("печатает в чате «X»")
        lc.close_context()
        self.assertIsNone(lc.current)
        self.assertEqual(statuses["last"], "online")

    def test_switch_closes_previous(self):
        lc = C.LifecycleManager()
        c1 = C.TrackingContext(dialog_id=1)
        c2 = C.TrackingContext(dialog_id=2)
        lc.open_context(c1)
        lc.open_context(c2)
        self.assertEqual(lc.current.dialog_id, 2)
        # only one active
        self.assertIsNotNone(lc.current)


class TestStatusController(unittest.TestCase):
    def test_no_rewrite_same(self):
        calls = []

        def set_status(t):
            calls.append(t)

        sc = C.StatusController(set_status, lambda: "std")
        sc.apply_text("a")
        sc.apply_text("a")
        self.assertEqual(calls, ["a"])

    def test_restore(self):
        calls = []

        def set_status(t):
            calls.append(t)

        sc = C.StatusController(set_status, lambda: "std")
        sc.apply_text("a")
        sc.restore()
        self.assertEqual(calls[-1], "std")


class TestChatSpyPlugin(unittest.TestCase):
    def test_dialog_opened_filters_current_chat(self):
        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        # minimal init
        plugin.settings = C.SettingsManager()
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.status_controller = C.StatusController(lambda t: None, lambda: "")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.formatter = C.StatusFormatter()

        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(1, "cur"), C.ChatInfo(2, "other")]

        ctx = plugin.handle_dialog_opened(
            dialog_id=1, participants=[C.ParticipantInfo(5, "Ivan")],
            common_chats_provider=Prov(), language=C.Language.RU)
        self.assertEqual(ctx.common_chats[5], [C.ChatInfo(2, "other")])

    def test_activity_event_updates_status(self):
        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        plugin.settings = C.SettingsManager()
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        statuses = []

        def set_status(t):
            statuses.append(t)

        plugin.status_controller = C.StatusController(set_status, lambda: "std")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.formatter = C.StatusFormatter(C.Language.RU)

        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(7, "Other")]

        plugin.handle_dialog_opened(1, participants=[C.ParticipantInfo(5, "Ivan")],
                                   common_chats_provider=Prov())
        ev = C.make_activity_event(5, 7, C.ActivityType.TYPING, chat_name="Other",
                                   participant_name="Ivan", timestamp=1.0)
        plugin.handle_event(ev, now=1.0)
        self.assertTrue(any("печатает в чате «Other»" in s for s in statuses))

    def test_disabled_plugin_no_context(self):
        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        plugin.settings = C.SettingsManager()
        plugin.settings.set_enabled(False)
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.status_controller = C.StatusController(lambda t: None, lambda: "")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.formatter = C.StatusFormatter()
        ctx = plugin.handle_dialog_opened(1, participants=[C.ParticipantInfo(5, "Ivan")])
        self.assertIsNone(ctx)

    def test_handle_activity_update_normalization(self):
        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        plugin.settings = C.SettingsManager()
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        plugin.status_controller = C.StatusController(lambda t: None, lambda: "")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.formatter = C.StatusFormatter(C.Language.RU)

        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(7, "Other")]

        plugin.handle_dialog_opened(1, participants=[C.ParticipantInfo(5, "Ivan")],
                                   common_chats_provider=Prov())

        class Upd:
            user_id = 5
            chat_id = 7
            action = type("TL_typing", (), {})()

        ev = plugin.handle_activity_update(Upd(), now=1.0)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.chat_id, 7)

    def test_missing_chat_name_not_shown(self):
        statuses = []

        def set_status(t):
            statuses.append(t)

        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        plugin.settings = C.SettingsManager()
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        plugin.status_controller = C.StatusController(set_status, lambda: "std")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.formatter = C.StatusFormatter(C.Language.RU)

        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(7, "")]  # no title

        plugin.handle_dialog_opened(1, participants=[C.ParticipantInfo(5, "Ivan")],
                                   common_chats_provider=Prov())
        # manually add event with empty chat name
        ctx = plugin.lifecycle.current
        ev = C.make_activity_event(5, 7, C.ActivityType.TYPING, chat_name="", timestamp=1.0)
        ctx.registry.add(ev)
        plugin._refresh_status(ctx, now=1.0)
        self.assertEqual(statuses[-1], "std")

    def test_cleanup_idempotent(self):
        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        plugin.settings = C.SettingsManager()
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        plugin.status_controller = C.StatusController(lambda t: None, lambda: "")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.formatter = C.StatusFormatter()
        plugin.handle_dialog_closed()
        plugin.handle_dialog_closed()
        self.assertIsNone(plugin.lifecycle.current)

    def test_no_leak_between_sessions(self):
        plugin = C.ChatSpyPlugin.__new__(C.ChatSpyPlugin)
        plugin.settings = C.SettingsManager()
        plugin.error_logger = C.ErrorLogger()
        plugin.compatibility = C.CompatibilityManager({k: True for k in C.CompatibilityManager.REQUIRED})
        plugin.lifecycle = C.LifecycleManager(plugin.error_logger)
        plugin.status_controller = C.StatusController(lambda t: None, lambda: "")
        plugin.lifecycle.set_status_controller(plugin.status_controller)
        plugin.adapter = C.PlatformAdapter(plugin)
        plugin.common_chats_manager = None
        plugin.participant_manager = None
        plugin.formatter = C.StatusFormatter()

        class Prov(C.CommonChatsProvider):
            def get_common_chats(self, participant_id, exclude_chat_id):
                return [C.ChatInfo(7, "Other")]

        plugin.handle_dialog_opened(1, participants=[C.ParticipantInfo(5, "Ivan")],
                                   common_chats_provider=Prov())
        ctx1 = plugin.lifecycle.current
        ev = C.make_activity_event(5, 7, C.ActivityType.TYPING, chat_name="Other", timestamp=1.0)
        ctx1.registry.add(ev)
        plugin.handle_dialog_closed()
        plugin.handle_dialog_opened(2, participants=[C.ParticipantInfo(6, "Petr")],
                                   common_chats_provider=Prov())
        ctx2 = plugin.lifecycle.current
        self.assertEqual(len(ctx2.registry.active()), 0)


class TestNormalizeTypingUpdate(unittest.TestCase):
    def test_returns_none_if_not_tracked(self):
        class Upd:
            user_id = 1
            chat_id = 999
        self.assertIsNone(C.normalize_typing_update(Upd(), {1, 2}))

    def test_returns_event_if_tracked(self):
        class Upd:
            user_id = 1
            chat_id = 2
            first_name = "Ivan"

        class Action:
            pass

        Upd.action = Action()
        ev = C.normalize_typing_update(Upd(), {2})
        self.assertIsNotNone(ev)
        self.assertEqual(ev.participant_id, 1)

    def test_none_update(self):
        self.assertIsNone(C.normalize_typing_update(None, set()))


class TestOnPluginLoad(unittest.TestCase):
    def test_loads_without_sdk(self):
        plugin = C.ChatSpyPlugin()
        plugin.on_plugin_load()
        # compatibility likely false due to find_class None -> missing required
        # common_chats requires TLRPC class -> None in plain env
        self.assertFalse(plugin.compatibility.is_usable())
        self.assertTrue(len(plugin.error_logger.all()) > 0)

    def test_create_settings(self):
        plugin = C.ChatSpyPlugin()
        s = plugin.create_settings()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0].key, "enabled")


if __name__ == "__main__":
    unittest.main()
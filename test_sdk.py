import io
import json
import unittest

import winshun


def frame(method, message_id=1, params=None):
    payload = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}}
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self._saved_out = winshun._out
        self.out = io.BytesIO()
        winshun._out = self.out
        self.addCleanup(self._restore)

    def _restore(self):
        winshun._out = self._saved_out

    def run_plugin(self, messages, on_query=None, **handlers):
        plugin = winshun.Plugin(
            on_query or (lambda query, plugin: []),
            name="me.todo",
            on_tray_menu=handlers.get("on_tray_menu"),
            on_menu=handlers.get("on_menu"),
            on_hotkey=handlers.get("on_hotkey"),
            on_initialized=handlers.get("on_initialized"),
            on_appearance=handlers.get("on_appearance"),
            on_show_settings=handlers.get("on_show_settings"),
        )
        plugin._in = io.BytesIO(b"".join(messages))
        plugin.serve()
        return [json.loads(line) for line in self.out.getvalue().decode("utf-8").splitlines() if line.strip()]

    def test_tray_menu_returns_items(self):
        messages = self.run_plugin(
            [frame("initialize"), frame("trayMenu", 2)],
            on_tray_menu=lambda plugin: [{"id": "toggle", "title": "显示便签", "checked": False}],
        )

        by_id = {message.get("id"): message for message in messages}

        self.assertEqual(by_id[2]["result"]["items"][0]["id"], "toggle")

    def test_menu_and_hotkey_notifications_reach_handlers(self):
        seen = []

        self.run_plugin(
            [frame("initialize"), frame("menu", 2, {"id": "new"}), frame("hotkey", 3, {"key": "Ctrl+Alt+T"})],
            on_menu=lambda menu_id, plugin: seen.append(("menu", menu_id)),
            on_hotkey=lambda key, plugin: seen.append(("hotkey", key)),
        )

        self.assertEqual(seen, [("menu", "new"), ("hotkey", "Ctrl+Alt+T")])

    def test_initialized_sees_settings_and_capabilities(self):
        seen = {}

        def on_initialized(plugin):
            seen["settings"] = dict(plugin.settings)
            seen["capabilities"] = dict(plugin.capabilities)

        self.run_plugin(
            [
                frame(
                    "initialize",
                    1,
                    {"settings": {"show_sticky": True}, "capabilities": {"hostHotkeys": True}},
                )
            ],
            on_initialized=on_initialized,
        )

        self.assertEqual(seen["settings"], {"show_sticky": True})
        self.assertTrue(seen["capabilities"]["hostHotkeys"])

    def test_initialize_exposes_native_appearance(self):
        seen = {}
        appearance = {"uiStyle": "fluent", "nativeLook": {"background": "#FF202124"}}

        self.run_plugin(
            [frame("initialize", 1, {"theme": "dark", "appearance": appearance})],
            on_initialized=lambda plugin: seen.update(
                theme=plugin.theme, appearance=plugin.appearance,
            ),
        )

        self.assertEqual(seen["theme"], "dark")
        self.assertEqual(seen["appearance"], appearance)

    def test_appearance_notifications_reach_handler_and_update_sdk_state(self):
        seen = []
        appearance = {
            "locale": "zh", "theme": "dark", "uiStyle": "tactile",
            "nativeLook": {"background": "#FF202124"},
        }

        self.run_plugin(
            [frame("initialize"), frame("appearance", 2, appearance)],
            on_appearance=lambda current, plugin: seen.append(
                (current, plugin.theme, plugin.locale)
            ),
        )

        self.assertEqual(seen, [(appearance, "dark", "zh")])

    def test_show_settings_is_still_answered(self):
        messages = self.run_plugin([frame("initialize"), frame("showSettings", 2)])

        by_id = {message.get("id"): message for message in messages}

        self.assertEqual(by_id[2]["result"], {})


if __name__ == "__main__":
    unittest.main()

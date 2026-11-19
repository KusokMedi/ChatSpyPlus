# ChatSpy+

ExteraGram plugin that shows participants' activity in other common chats.

## Build / install

The installable artifact is `chatspy_plus.plugin` (a single-file ExteraGram plugin).

## Development

Run tests:

```bash
python3 -m unittest tests.test_chatspy -v
```

## Compatibility

- ExteraGram 12.5.1+
- Plugin SDK 1.4.5.0+

## Known limitations

- Real ExteraGram UI hooking for status text and dialog lifecycle requires
  on-device Xposed/SDK integration; the shipped code degrades gracefully when
  those APIs are unavailable.
- Common-chats and participant enumeration depend on ExteraGram/Telegram
  internals available on the device.
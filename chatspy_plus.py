# -*- coding: utf-8 -*-
"""ChatSpy+ plugin for exteraGram."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from base_plugin import BasePlugin, HookResult, HookStrategy  # type: ignore
except Exception:  # noqa: BLE001
    class BasePlugin:  # type: ignore
        def __init__(self) -> None:
            self.logger = _FallbackLogger()

    class HookResult:  # type: ignore
        def __init__(self, strategy=None, request=None, response=None, update=None,
                     updates=None, params=None) -> None:
            self.strategy = strategy
            self.request = request
            self.response = response
            self.update = update
            self.updates = updates
            self.params = params

    class HookStrategy:  # type: ignore
        DEFAULT = "default"
        CANCEL = "cancel"
        MODIFY = "modify"
        MODIFY_FINAL = "modify_final"

try:
    from ui.settings import Switch  # type: ignore
except Exception:  # noqa: BLE001
    @dataclass
    class Switch:  # type: ignore
        key: str = ""
        text: str = ""
        default: bool = True
        subtext: str = ""
        icon: str = ""

try:
    from base_plugin import MenuItemData, MenuItemType  # type: ignore
except Exception:  # noqa: BLE001
    @dataclass
    class MenuItemData:  # type: ignore
        menu_type: Any = None
        text: str = ""
        on_click: Any = None
        item_id: Optional[str] = None
        icon: Optional[str] = None
        subtext: Optional[str] = None
        condition: Optional[str] = None
        priority: int = 0

    class MenuItemType:  # type: ignore
        DRAWER_MENU = "drawer"

try:
    from hook_utils import find_class  # type: ignore
except Exception:  # noqa: BLE001
    def find_class(name):  # type: ignore
        return None

try:
    from client_utils import send_request  # type: ignore
except Exception:  # noqa: BLE001
    def send_request(request, callback=None, account=None):  # type: ignore
        return None


class _FallbackLogger:
    def info(self, *a, **k) -> None:
        pass

    def error(self, *a, **k) -> None:
        pass

    def warning(self, *a, **k) -> None:
        pass


class Language(str, Enum):
    RU = "ru"
    EN = "en"


class ActivityType(str, Enum):
    TYPING = "typing"
    SENDING_PHOTO = "sending_photo"
    SENDING_VIDEO = "sending_video"
    SENDING_FILE = "sending_file"
    SENDING_AUDIO = "sending_audio"
    SENDING_GAME = "sending_game"
    SENDING_VIDEO_STATUS = "sending_video_status"
    RECORDING_AUDIO = "recording_audio"
    RECORDING_ROUND = "recording_round"
    SELECTING_CONTACT = "selecting_contact"
    SELECTING_LOCATION = "selecting_location"


VERBS_RU = {
    ActivityType.TYPING: "печатает",
    ActivityType.SENDING_PHOTO: "отправляет фото",
    ActivityType.SENDING_VIDEO: "отправляет видео",
    ActivityType.SENDING_FILE: "отправляет файл",
    ActivityType.SENDING_AUDIO: "отправляет аудио",
    ActivityType.SENDING_GAME: "отправляет игру",
    ActivityType.SENDING_VIDEO_STATUS: "отправляет видеосообщение",
    ActivityType.RECORDING_AUDIO: "записывает голосовое",
    ActivityType.RECORDING_ROUND: "записывает видеосообщение",
    ActivityType.SELECTING_CONTACT: "выбирает контакт",
    ActivityType.SELECTING_LOCATION: "выбирает геолокацию",
}

VERBS_EN = {
    ActivityType.TYPING: "is typing",
    ActivityType.SENDING_PHOTO: "is sending a photo",
    ActivityType.SENDING_VIDEO: "is sending a video",
    ActivityType.SENDING_FILE: "is sending a file",
    ActivityType.SENDING_AUDIO: "is sending audio",
    ActivityType.SENDING_GAME: "is sending a game",
    ActivityType.SENDING_VIDEO_STATUS: "is sending a video message",
    ActivityType.RECORDING_AUDIO: "is recording audio",
    ActivityType.RECORDING_ROUND: "is recording a video message",
    ActivityType.SELECTING_CONTACT: "is selecting a contact",
    ActivityType.SELECTING_LOCATION: "is selecting a location",
}


@dataclass
class ChatInfo:
    chat_id: Any
    title: str = ""


@dataclass
class ParticipantInfo:
    user_id: Any
    display_name: str = ""
    username: str = ""

    def best_name(self) -> Optional[str]:
        if self.display_name and self.display_name.strip():
            return self.display_name.strip()
        if self.username:
            return "@" + self.username.lstrip("@")
        return None


@dataclass
class ActivityEvent:
    participant_id: Any
    chat_id: Any
    activity_type: ActivityType
    timestamp: float
    source: str = ""
    chat_name: str = ""
    participant_name: Optional[str] = None
    participant_username: Optional[str] = None
    expires_at: Optional[float] = None

    def participant_label(self) -> Optional[str]:
        if self.participant_name and self.participant_name.strip():
            return self.participant_name.strip()
        if self.participant_username:
            return "@" + self.participant_username.lstrip("@")
        return None

    def key(self):
        return (self.participant_id, self.chat_id, self.activity_type)


@dataclass
class TrackingContext:
    dialog_id: Any
    dialog_type: str = "private"
    participants: List[ParticipantInfo] = field(default_factory=list)
    common_chats: Dict[Any, List[ChatInfo]] = field(default_factory=dict)
    registry: "ActivityRegistry" = field(default_factory=lambda: None)  # type: ignore
    language: Language = Language.RU

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = ActivityRegistry()


class ErrorLogger:
    def __init__(self, sink: Optional[Callable[[str, str], None]] = None) -> None:
        self._sink = sink
        self.errors: List[Dict[str, str]] = []

    def log(self, category: str, message: str, exc: Optional[BaseException] = None) -> None:
        entry = {"category": category, "message": message}
        if exc is not None:
            entry["exception"] = repr(exc)
        self.errors.append(entry)
        if self._sink is not None:
            try:
                self._sink(category, message)
            except Exception:  # noqa: BLE001
                pass

    def all(self) -> List[Dict[str, str]]:
        return list(self.errors)

    def clear(self) -> None:
        self.errors.clear()


class ActivityRegistry:
    def __init__(self, ttl: float = 6.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._events: List[ActivityEvent] = []
        self._lock = threading.RLock()

    def _now(self) -> float:
        return self._clock()

    def _prune(self, now: float) -> None:
        self._events = [
            e for e in self._events
            if e.expires_at is None or e.expires_at > now
        ]

    def add(self, event: ActivityEvent) -> ActivityEvent:
        with self._lock:
            now = self._now()
            self._prune(now)
            if event.expires_at is None:
                event.expires_at = event.timestamp + self.ttl
            for existing in self._events:
                if existing.key() == event.key():
                    existing.timestamp = event.timestamp
                    existing.expires_at = event.expires_at
                    existing.chat_name = event.chat_name or existing.chat_name
                    existing.participant_name = event.participant_name or existing.participant_name
                    existing.participant_username = event.participant_username or existing.participant_username
                    existing.source = event.source or existing.source
                    return existing
            self._events.append(event)
            return event

    def remove(self, participant_id: Any = None, chat_id: Any = None,
               activity_type: Optional[ActivityType] = None) -> int:
        with self._lock:
            before = len(self._events)
            self._events = [
                e for e in self._events
                if not (
                    (participant_id is None or e.participant_id == participant_id)
                    and (chat_id is None or e.chat_id == chat_id)
                    and (activity_type is None or e.activity_type == activity_type)
                )
            ]
            return before - len(self._events)

    def active(self, now: Optional[float] = None) -> List[ActivityEvent]:
        if now is None:
            now = self._now()
        with self._lock:
            self._prune(now)
            return list(self._events)

    def current(self, now: Optional[float] = None) -> Optional[ActivityEvent]:
        active = self.active(now)
        if not active:
            return None
        return max(active, key=lambda e: e.timestamp)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        return len(self.active())


class StatusFormatter:
    def __init__(self, language: Language = Language.RU) -> None:
        self.language = language

    def verb(self, activity_type: ActivityType) -> str:
        table = VERBS_RU if self.language == Language.RU else VERBS_EN
        return table.get(activity_type, str(activity_type.value))

    def personal(self, event: ActivityEvent) -> Optional[str]:
        if not event.chat_name:
            return None
        if self.language == Language.RU:
            return f"{self.verb(event.activity_type)} в чате «{event.chat_name}»"
        return f"{self.verb(event.activity_type)} in chat \"{event.chat_name}\""

    def group(self, event: ActivityEvent) -> Optional[str]:
        name = event.participant_label()
        if not name or not event.chat_name:
            return None
        if self.language == Language.RU:
            return f"{name} {self.verb(event.activity_type)} в чате «{event.chat_name}»"
        return f"{name} {self.verb(event.activity_type)} in chat \"{event.chat_name}\""

    def format(self, event: ActivityEvent, is_group: bool) -> Optional[str]:
        if is_group:
            return self.group(event)
        return self.personal(event)


class StatusController:
    def __init__(self, set_status: Callable[[str], None],
                 get_standard_status: Callable[[], str]) -> None:
        self._set_status = set_status
        self._get_standard = get_standard_status
        self._last: Optional[str] = None

    def apply_text(self, text: Optional[str]) -> None:
        target = text if text is not None else self._get_standard()
        if target is None:
            target = ""
        if target != self._last:
            self._set_status(target)
            self._last = target

    def restore(self) -> None:
        self.apply_text(None)

    @property
    def last(self) -> Optional[str]:
        return self._last


class CommonChatsProvider:
    def get_common_chats(self, participant_id: Any, exclude_chat_id: Any) -> List[ChatInfo]:
        raise NotImplementedError


class CommonChatsManager:
    def __init__(self, provider: CommonChatsProvider, max_concurrency: int = 4,
                 logger: Optional[ErrorLogger] = None) -> None:
        self.provider = provider
        self.max_concurrency = max(1, int(max_concurrency))
        self.logger = logger or ErrorLogger()
        self._cache: Dict[Any, List[ChatInfo]] = {}

    def fetch_for_participant(self, participant_id: Any, exclude_chat_id: Any) -> List[ChatInfo]:
        if participant_id in self._cache:
            return [c for c in self._cache[participant_id] if c.chat_id != exclude_chat_id]
        try:
            chats = self.provider.get_common_chats(participant_id, exclude_chat_id)
        except Exception as exc:  # noqa: BLE001
            self.logger.log("common_chats", f"failed for {participant_id}", exc)
            chats = []
        filtered = [c for c in (chats or []) if c.chat_id != exclude_chat_id]
        self._cache[participant_id] = list(chats or [])
        return filtered

    def fetch_for_participants(self, participant_ids: List[Any], exclude_chat_id: Any) -> Dict[Any, List[ChatInfo]]:
        result: Dict[Any, List[ChatInfo]] = {}
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {
                pool.submit(self.fetch_for_participant, pid, exclude_chat_id): pid
                for pid in participant_ids
            }
            for fut in as_completed(futures):
                pid = futures[fut]
                try:
                    result[pid] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    self.logger.log("common_chats", f"failed for {pid}", exc)
                    result[pid] = []
        return result

    def clear_cache(self) -> None:
        self._cache.clear()


class ParticipantProvider:
    def get_participants(self, dialog: Any) -> List[ParticipantInfo]:
        raise NotImplementedError


class ParticipantManager:
    def __init__(self, provider: ParticipantProvider, logger: Optional[ErrorLogger] = None) -> None:
        self.provider = provider
        self.logger = logger or ErrorLogger()

    def get_participants(self, dialog: Any) -> List[ParticipantInfo]:
        try:
            return list(self.provider.get_participants(dialog) or [])
        except Exception as exc:  # noqa: BLE001
            self.logger.log("participants", "failed to get participants", exc)
            return []

    @staticmethod
    def filter_trackable(participants: List[ParticipantInfo]) -> List[ParticipantInfo]:
        return [p for p in participants if p.user_id is not None]


class CompatibilityManager:
    REQUIRED = ("dialog_detection", "participants", "common_chats", "update_hooks", "ui_restore")
    OPTIONAL_ACTIVITY = ("typing", "photo", "video", "file", "audio", "location", "contact")

    def __init__(self, capabilities: Optional[Dict[str, bool]] = None) -> None:
        self.capabilities = dict(capabilities or {})

    def set(self, name: str, value: bool) -> None:
        self.capabilities[name] = bool(value)

    def has(self, name: str) -> bool:
        return bool(self.capabilities.get(name, False))

    def missing_required(self) -> List[str]:
        return [c for c in self.REQUIRED if not self.has(c)]

    def is_usable(self) -> bool:
        return not self.missing_required()

    def disabled_activities(self) -> List[str]:
        return [a for a in self.OPTIONAL_ACTIVITY if not self.has(a)]


class SettingsManager:
    def __init__(self, storage: Optional[Dict[str, Any]] = None, default_enabled: bool = True) -> None:
        self._storage = storage if storage is not None else {}
        if "enabled" not in self._storage:
            self._storage["enabled"] = default_enabled

    def is_enabled(self) -> bool:
        return bool(self._storage.get("enabled", True))

    def set_enabled(self, value: bool) -> None:
        self._storage["enabled"] = bool(value)

    def get(self, key: str, default: Any = None) -> Any:
        return self._storage.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._storage[key] = value

    @property
    def storage(self) -> Dict[str, Any]:
        return self._storage


class LifecycleManager:
    def __init__(self, logger: Optional[ErrorLogger] = None) -> None:
        self.logger = logger or ErrorLogger()
        self.current: Optional[TrackingContext] = None
        self._status_controller: Optional[StatusController] = None
        self._on_close_callbacks: List[Callable[[], None]] = []

    def set_status_controller(self, controller: StatusController) -> None:
        self._status_controller = controller

    def open_context(self, context: TrackingContext) -> None:
        self.close_context()
        self.current = context

    def close_context(self) -> None:
        ctx = self.current
        if ctx is None:
            return
        try:
            if ctx.registry is not None:
                ctx.registry.clear()
        except Exception as exc:  # noqa: BLE001
            self.logger.log("lifecycle", "registry clear failed", exc)
        if self._status_controller is not None:
            try:
                self._status_controller.restore()
            except Exception as exc:  # noqa: BLE001
                self.logger.log("lifecycle", "status restore failed", exc)
        self.current = None

    def disable(self) -> None:
        self.close_context()

    def is_active(self) -> bool:
        return self.current is not None


def make_activity_event(
    participant_id: Any,
    chat_id: Any,
    activity_type: ActivityType,
    chat_name: str = "",
    participant_name: Optional[str] = None,
    participant_username: Optional[str] = None,
    timestamp: Optional[float] = None,
    ttl: float = 6.0,
    source: str = "",
) -> ActivityEvent:
    ts = timestamp if timestamp is not None else time.monotonic()
    return ActivityEvent(
        participant_id=participant_id,
        chat_id=chat_id,
        activity_type=activity_type,
        timestamp=ts,
        source=source,
        chat_name=chat_name,
        participant_name=participant_name,
        participant_username=participant_username,
        expires_at=ts + ttl,
    )


def normalize_typing_update(update: Any, common_chat_ids: Set[Any]) -> Optional[ActivityEvent]:
    if update is None:
        return None

    def g(obj, *names):
        for n in names:
            if hasattr(obj, n):
                return getattr(obj, n)
        return None

    user_id = g(update, "user_id", "userId", "from_id", "fromId")
    chat_id = g(update, "chat_id", "chatId", "peer_chat_id", "peerChatId", "dialog_id", "dialogId")
    action = g(update, "action", "typing_action", "typingAction")

    activity_type = ActivityType.TYPING
    action_name = type(action).__name__ if action is not None else ""
    lowered = action_name.lower()
    mapping = [
        ("photo", ActivityType.SENDING_PHOTO),
        ("video", ActivityType.SENDING_VIDEO),
        ("document", ActivityType.SENDING_FILE),
        ("file", ActivityType.SENDING_FILE),
        ("audio", ActivityType.SENDING_AUDIO),
        ("game", ActivityType.SENDING_GAME),
        ("contact", ActivityType.SELECTING_CONTACT),
        ("location", ActivityType.SELECTING_LOCATION),
        ("round", ActivityType.RECORDING_ROUND),
        ("voice", ActivityType.RECORDING_AUDIO),
    ]
    for needle, atype in mapping:
        if needle in lowered:
            activity_type = atype
            break

    if user_id is None or chat_id is None:
        return None
    if chat_id not in common_chat_ids:
        return None

    chat_name = g(update, "chat_name", "chatName", "title") or ""
    participant_name = g(update, "first_name", "firstName", "display_name", "displayName")
    username = g(update, "username")
    return make_activity_event(
        participant_id=user_id,
        chat_id=chat_id,
        activity_type=activity_type,
        chat_name=str(chat_name) if chat_name else "",
        participant_name=str(participant_name) if participant_name else None,
        participant_username=str(username) if username else None,
        source="update",
    )


class PlatformAdapter:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.logger = getattr(plugin, "logger", _FallbackLogger())
        self._standard_status: Optional[str] = None

    def current_dialog_id(self) -> Optional[Any]:
        try:
            from client_utils import get_last_fragment  # type: ignore
            fragment = get_last_fragment()
            if fragment is None:
                return None
            for attr in ("dialog_id", "dialogId", "currentDialogId"):
                if hasattr(fragment, attr):
                    return getattr(fragment, attr)
            return None
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"current_dialog_id failed: {exc}")
            return None

    def is_group(self, dialog_id: Any) -> bool:
        try:
            return isinstance(dialog_id, int) and dialog_id < 0
        except Exception:  # noqa: BLE001
            return False

    def get_participants(self, dialog_id: Any) -> List[ParticipantInfo]:
        result: List[ParticipantInfo] = []
        try:
            from client_utils import get_messages_controller  # type: ignore
            from hook_utils import get_private_field  # type: ignore
            mc = get_messages_controller()
            if dialog_id is None:
                return result
            if isinstance(dialog_id, int) and dialog_id > 0:
                u = mc.getUser(dialog_id) if hasattr(mc, "getUser") else None
                if u is not None:
                    first = getattr(u, "first_name", "") or ""
                    last = getattr(u, "last_name", "") or ""
                    name = (first + " " + last).strip()
                    username = getattr(u, "username", "") or ""
                    result.append(ParticipantInfo(user_id=dialog_id, display_name=name, username=username))
                else:
                    result.append(ParticipantInfo(user_id=dialog_id))
                return result
            # group/channel
            cid = -dialog_id if isinstance(dialog_id, int) else dialog_id
            users = None
            # try chatParticipants map
            try:
                cp = get_private_field(mc, "chatParticipants")
                if cp is not None and hasattr(cp, "get"):
                    obj = cp.get(cid) if isinstance(cid, int) else None
                    if obj is not None:
                        for attr in ("participants", "users"):
                            if hasattr(obj, attr):
                                users = getattr(obj, attr)
                                break
            except Exception:  # noqa: BLE001
                users = None
            if users is None:
                # try public method
                try:
                    if hasattr(mc, "getChatParticipants"):
                        users = mc.getChatParticipants(cid)
                except Exception:  # noqa: BLE001
                    users = None
            if users is not None:
                for u in list(users):
                    uid = None
                    for attr in ("user_id", "userId", "id", "uid"):
                        if hasattr(u, attr):
                            uid = getattr(u, attr)
                            break
                    if uid is None and hasattr(u, "user"):
                        uid = getattr(getattr(u, "user"), "id", None)
                    if uid is None:
                        continue
                    user_obj = getattr(u, "user", None)
                    if user_obj is None:
                        try:
                            user_obj = mc.getUser(uid)
                        except Exception:  # noqa: BLE001
                            user_obj = None
                    first = getattr(user_obj, "first_name", "") if user_obj is not None else ""
                    last = getattr(user_obj, "last_name", "") if user_obj is not None else ""
                    name = ((first or "") + " " + (last or "")).strip()
                    username = getattr(user_obj, "username", "") if user_obj is not None else ""
                    result.append(ParticipantInfo(user_id=uid, display_name=name, username=username or ""))
            if not result:
                # fallback: at least current user? no
                pass
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"get_participants failed: {exc}")
        return result

    def get_common_chats(self, user_id: Any, exclude_chat_id: Any) -> List[ChatInfo]:
        result: List[ChatInfo] = []
        try:
            from org.telegram.tgnet import TLRPC  # type: ignore
            from client_utils import send_request  # type: ignore

            req = TLRPC.TL_messages_getCommonChats()
            input_user = None
            try:
                from client_utils import get_messages_controller  # type: ignore
                mc = get_messages_controller()
                if hasattr(mc, "getUser"):
                    u = mc.getUser(user_id)
                    if u is not None and hasattr(mc, "getInputUser"):
                        input_user = mc.getInputUser(u)
            except Exception:  # noqa: BLE001
                input_user = None
            if input_user is None:
                return result
            req.user_id = input_user
            req.max_id = 0
            req.limit = 100

            done = threading.Event()

            def cb(response, error):
                try:
                    if error is None and response is not None and hasattr(response, "chats"):
                        for c in response.chats:
                            result.append(ChatInfo(chat_id=getattr(c, "id", None),
                                                  title=getattr(c, "title", "") or ""))
                except Exception as exc:  # noqa: BLE001
                    self.logger.error(f"common chats cb failed: {exc}")
                finally:
                    done.set()

            send_request(req, cb)
            done.wait(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"get_common_chats failed: {exc}")
        return [c for c in result if c.chat_id != exclude_chat_id]

    def set_status_text(self, text: str) -> None:
        try:
            from client_utils import get_last_fragment  # type: ignore
            from hook_utils import get_private_field  # type: ignore
            from android_utils import run_on_ui_thread  # type: ignore
            fragment = get_last_fragment()
            if fragment is None:
                return

            def apply():
                try:
                    if self._standard_status is None:
                        try:
                            ab0 = get_private_field(fragment, "actionBar")
                            if ab0 is not None and hasattr(ab0, "getSubtitle"):
                                self._standard_status = ab0.getSubtitle()
                            else:
                                tv0 = get_private_field(fragment, "subtitleTextView")
                                if tv0 is not None and hasattr(tv0, "getText"):
                                    self._standard_status = str(tv0.getText())
                        except Exception:  # noqa: BLE001
                            self._standard_status = ""
                    ab = get_private_field(fragment, "actionBar")
                    if ab is not None and hasattr(ab, "setSubtitle"):
                        try:
                            ab.setSubtitle(text)
                            return
                        except Exception:  # noqa: BLE001
                            pass
                    tv = get_private_field(fragment, "subtitleTextView")
                    if tv is not None and hasattr(tv, "setText"):
                        try:
                            tv.setText(text)
                            return
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass

            run_on_ui_thread(apply)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"set_status_text failed: {exc}")

    def get_standard_status(self) -> str:
        if self._standard_status is None:
            return ""
        return self._standard_status

    def show_error(self, message: str) -> None:
        try:
            from ui.bulletin import BulletinHelper  # type: ignore
            BulletinHelper.show_error(message, None)
        except Exception:  # noqa: BLE001
            pass

    def capabilities(self) -> Dict[str, bool]:
        caps: Dict[str, bool] = {}
        try:
            caps["dialog_detection"] = find_class("org.telegram.ui.ChatActivity") is not None
        except Exception:  # noqa: BLE001
            caps["dialog_detection"] = False
        try:
            caps["participants"] = find_class("org.telegram.messenger.MessagesController") is not None
        except Exception:  # noqa: BLE001
            caps["participants"] = False
        try:
            caps["common_chats"] = find_class("org.telegram.tgnet.TLRPC") is not None
        except Exception:  # noqa: BLE001
            caps["common_chats"] = False
        caps["update_hooks"] = True
        caps["ui_restore"] = True
        for a in CompatibilityManager.OPTIONAL_ACTIVITY:
            caps[a] = True
        return caps


class ChatSpyPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__()
        self.logger = getattr(self, "logger", _FallbackLogger())
        self.settings = SettingsManager()
        self.error_logger = ErrorLogger()
        self.compatibility = CompatibilityManager()
        self.lifecycle = LifecycleManager(self.error_logger)
        self.adapter = PlatformAdapter(self)
        self.common_chats_manager: Optional[CommonChatsManager] = None
        self.participant_manager: Optional[ParticipantManager] = None
        self.status_controller: Optional[StatusController] = None
        self.formatter = StatusFormatter()
        self._enabled = True

    def on_plugin_load(self) -> None:
        try:
            self.settings = SettingsManager(storage=getattr(self, "_settings_storage", None))
            self._enabled = self.settings.is_enabled()
            caps = self.adapter.capabilities()
            self.compatibility = CompatibilityManager(caps)
            if not self.compatibility.is_usable():
                self.error_logger.log("compatibility", f"missing required: {self.compatibility.missing_required()}")
                self.adapter.show_error("ChatSpy+ is not compatible with this ExteraGram build.")
                return
            for act in self.compatibility.disabled_activities():
                pass
            self.common_chats_manager = CommonChatsManager(
                _AdapterCommonChatsProvider(self.adapter), logger=self.error_logger)
            self.participant_manager = ParticipantManager(
                _AdapterParticipantProvider(self.adapter), logger=self.error_logger)
            self.status_controller = StatusController(
                self.adapter.set_status_text, self.adapter.get_standard_status)
            self.lifecycle.set_status_controller(self.status_controller)
            if self._enabled:
                self._register_hooks()
            try:
                self.add_menu_item(MenuItemData(
                    menu_type=MenuItemType.DRAWER_MENU,
                    text="ChatSpy+ errors",
                    on_click=self._show_errors,
                    icon="msg_info",
                ))
            except Exception as exc:  # noqa: BLE001
                self.error_logger.log("menu", "add_menu_item failed", exc)
        except Exception as exc:  # noqa: BLE001
            self.error_logger.log("lifecycle", "on_plugin_load failed", exc)

    def _show_errors(self, context=None) -> None:
        errs = self.error_logger.all()
        if not errs:
            self.adapter.show_error("ChatSpy+: no errors")
            return
        msg = "\n".join(f"{e['category']}: {e['message']}" for e in errs[-15:])
        self.adapter.show_error(msg)

    def on_plugin_unload(self) -> None:
        try:
            self.lifecycle.disable()
            self._unregister_hooks()
        except Exception as exc:  # noqa: BLE001
            self.error_logger.log("lifecycle", "on_plugin_unload failed", exc)

    def create_settings(self) -> List[Any]:
        return [
            Switch(key="enabled", text="ChatSpy+ — Включить отслеживание активности", default=True),
        ]

    def _register_hooks(self) -> None:
        try:
            self.add_hook("TL_updateUserTyping")
            self.add_hook("TL_updateChatUserTyping")
            self.add_hook("TL_updateEncryptedChatTyping")
        except Exception as exc:  # noqa: BLE001
            self.error_logger.log("hooks", "register failed", exc)

    def _unregister_hooks(self) -> None:
        pass

    def handle_dialog_opened(
        self,
        dialog_id: Any,
        dialog_type: str = "private",
        participants: Optional[List[ParticipantInfo]] = None,
        common_chats_provider: Optional[CommonChatsProvider] = None,
        language: Language = Language.RU,
    ) -> TrackingContext:
        if not self.settings.is_enabled():
            self.handle_dialog_closed()
            return None  # type: ignore
        if dialog_type != "private":
            self.handle_dialog_closed()
            return None  # type: ignore
        if participants is None:
            participants = []
        if common_chats_provider is not None:
            ccm = CommonChatsManager(common_chats_provider, logger=self.error_logger)
        else:
            ccm = self.common_chats_manager or CommonChatsManager(
                _AdapterCommonChatsProvider(self.adapter), logger=self.error_logger)
        self.common_chats_manager = ccm
        if self.participant_manager is None:
            self.participant_manager = ParticipantManager(
                _AdapterParticipantProvider(self.adapter), logger=self.error_logger)
        trackable = ParticipantManager.filter_trackable(participants)
        ids = [p.user_id for p in trackable]
        common = ccm.fetch_for_participants(ids, dialog_id)
        mapped: Dict[Any, List[ChatInfo]] = {}
        for p in trackable:
            mapped[p.user_id] = [c for c in common.get(p.user_id, [])
                                 if c.chat_id != dialog_id]
        ctx = TrackingContext(
            dialog_id=dialog_id,
            dialog_type=dialog_type,
            participants=trackable,
            common_chats=mapped,
            language=language,
        )
        self.formatter = StatusFormatter(language)
        self.lifecycle.open_context(ctx)
        return ctx

    def handle_dialog_closed(self) -> None:
        self.lifecycle.close_context()

    def ensure_context(self, language: Language = Language.RU) -> Optional[TrackingContext]:
        if not self.settings.is_enabled():
            return None
        dialog_id = self.adapter.current_dialog_id()
        if dialog_id is None:
            return self.lifecycle.current
        ctx = self.lifecycle.current
        if ctx is not None and ctx.dialog_id == dialog_id:
            return ctx
        if ctx is not None:
            self.handle_dialog_closed()
        participants = self.adapter.get_participants(dialog_id) if hasattr(self.adapter, "get_participants") else []
        if self.adapter.is_group(dialog_id):
            # Only private chats are supported.
            self.handle_dialog_closed()
            return None
        dialog_type = "private"
        try:
            return self.handle_dialog_opened(
                dialog_id=dialog_id,
                dialog_type=dialog_type,
                participants=participants,
                language=language,
            )
        except Exception as exc:  # noqa: BLE001
            self.error_logger.log("lifecycle", "ensure_context failed", exc)
            return None

    def handle_activity_update(self, update: Any, now: Optional[float] = None) -> Optional[ActivityEvent]:
        if self.lifecycle.current is None:
            self.ensure_context()
        ctx = self.lifecycle.current
        if ctx is None or not self.settings.is_enabled():
            return None
        tracked_chats: Set[Any] = set()
        for chats in ctx.common_chats.values():
            for c in chats:
                tracked_chats.add(c.chat_id)
        event = normalize_typing_update(update, tracked_chats)
        if event is None:
            return None
        for p in ctx.participants:
            if p.user_id == event.participant_id:
                event.participant_name = p.display_name or event.participant_name
                event.participant_username = p.username or event.participant_username
                break
        if not event.chat_name:
            for chats in ctx.common_chats.values():
                for c in chats:
                    if c.chat_id == event.chat_id:
                        event.chat_name = c.title
                        break
        ctx.registry.add(event)
        self._refresh_status(ctx, now)
        return event

    def handle_event(self, event: ActivityEvent, now: Optional[float] = None) -> None:
        ctx = self.lifecycle.current
        if ctx is None or not self.settings.is_enabled():
            return
        ctx.registry.add(event)
        self._refresh_status(ctx, now)

    def _refresh_status(self, ctx: TrackingContext, now: Optional[float] = None) -> None:
        if self.status_controller is None:
            return
        event = ctx.registry.current(now)
        is_group = ctx.dialog_type != "private"
        text: Optional[str] = None
        if event is not None:
            text = self.formatter.format(event, is_group)
        if text is None:
            self.status_controller.restore()
        else:
            self.status_controller.apply_text(text)

    def on_update_hook(self, update_name: str, account: int, update: Any) -> Any:
        if update_name in ("TL_updateUserTyping", "TL_updateChatUserTyping",
                           "TL_updateEncryptedChatTyping"):
            self.handle_activity_update(update)
        return None


class _AdapterCommonChatsProvider(CommonChatsProvider):
    def __init__(self, adapter: PlatformAdapter) -> None:
        self.adapter = adapter

    def get_common_chats(self, participant_id: Any, exclude_chat_id: Any) -> List[ChatInfo]:
        return self.adapter.get_common_chats(participant_id, exclude_chat_id)


class _AdapterParticipantProvider(ParticipantProvider):
    def __init__(self, adapter: PlatformAdapter) -> None:
        self.adapter = adapter

    def get_participants(self, dialog: Any) -> List[ParticipantInfo]:
        return self.adapter.get_participants(dialog)


# Plugin metadata (kept at runtime bottom so it does not clobber __name__ used by
# dataclasses/import machinery; the exteraGram loader reads these via AST).
__id__ = "chatspy_plus"
__name__ = "ChatSpy+"
__description__ = "Shows participants' activity in other common chats."
__author__ = "@kusokmedi52"
__version__ = "1.0.0"
__icon__ = "exteraPlugins/1"
__app_version__ = ">=12.5.1"
__sdk_version__ = ">=1.4.5.0"
__category__ = ["chats", "tools"]

__all__ = [
    "ChatSpyPlugin",
    "ActivityRegistry",
    "ActivityEvent",
    "ActivityType",
    "StatusFormatter",
    "StatusController",
    "TrackingContext",
    "CommonChatsManager",
    "ParticipantManager",
    "ParticipantInfo",
    "ChatInfo",
    "LifecycleManager",
    "CompatibilityManager",
    "SettingsManager",
    "ErrorLogger",
    "Language",
    "make_activity_event",
    "normalize_typing_update",
]
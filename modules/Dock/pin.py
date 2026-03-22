class Pin:
    def __init__(self, session_manager, app_resolver):
        self._session_manager = session_manager
        self._app_resolver = app_resolver
        self.pinned_apps_info: dict = {}

    def is_pinned(self, unique_id: str) -> bool:
        return unique_id in self.pinned_apps_info

    def pin(self, unique_id: str, app, key: str, original: str):
        self.pinned_apps_info[unique_id] = {
            "app": app,
            "key": key,
            "original": original,
        }
        self._save()

    def unpin(self, unique_id: str) -> bool:
        if unique_id in self.pinned_apps_info:
            del self.pinned_apps_info[unique_id]
            self._save()
            return True
        return False

    def toggle(self, unique_id: str, app, key: str, original: str) -> bool:
        if self.is_pinned(unique_id):
            self.unpin(unique_id)
            return False
        self.pin(unique_id, app, key, original)
        return True

    def restore(self):
        if not self._session_manager:
            return
        self._app_resolver.refresh()
        pinned_list = self._session_manager.get_pinned()
        for p in pinned_list:
            key = p.get("key", "")
            original = p.get("original", key)
            uid = p.get("unique_id", key)
            app = (
                self._app_resolver.app_map.get(key)
                or self._app_resolver.app_map.get(original.lower())
                or self._app_resolver.find_app(original)
            )
            self.pinned_apps_info[uid] = {
                "app": app,
                "key": key,
                "original": original,
            }

    def get_ghost_candidates(self, existing_ids: set) -> list:
        result = []
        for uid, info in self.pinned_apps_info.items():
            if uid not in existing_ids:
                result.append(
                    {
                        "unique_id": uid,
                        "app": info["app"],
                        "insts": [],
                        "key": info["key"],
                        "original": info["original"],
                    }
                )
        return result

    def _save(self):
        if not self._session_manager:
            return
        pinned_keys = set()
        pinned_info = []
        for uid, info in self.pinned_apps_info.items():
            pinned_keys.add(info["key"])
            pinned_info.append(
                {
                    "unique_id": uid,
                    "key": info["key"],
                    "original": info["original"],
                }
            )
        self._session_manager.save(pinned_keys, pinned_info)
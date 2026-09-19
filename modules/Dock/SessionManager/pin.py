class Pin:
    def __init__(self, session_manager, app_resolver):
        self._session_manager = session_manager
        self._app_resolver = app_resolver
        self.pinned_apps_info: dict = {}

    def is_pinned(self, unique_id: str) -> bool:
        return str(unique_id).lower() in self.pinned_apps_info

    def pin(self, unique_id: str, app, key: str, original: str):
        uid = str(unique_id).lower()
        self.pinned_apps_info[uid] = {
            "app": app,
            "key": key,
            "original": original,
        }
        self._save()

    def unpin(self, unique_id: str) -> bool:
        uid = str(unique_id).lower()
        if uid in self.pinned_apps_info:
            del self.pinned_apps_info[uid]
            self._save()
            return True
        return False

    def toggle(self, unique_id: str, app, key: str, original: str) -> bool:
        uid = str(unique_id).lower()
        if self.is_pinned(uid):
            self.unpin(uid)
            return False
        self.pin(uid, app, key, original)
        return True

    def restore(self):
        if not self._session_manager:
            return
        self._app_resolver.refresh()
        pinned_list = self._session_manager.get_pinned()
        for p in pinned_list:
            key = p.get("key", "")
            original = p.get("original", key)
            uid = str(p.get("unique_id", key)).lower()
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
        normalized_existing = {str(eid).lower() for eid in existing_ids}
        
        for uid, info in self.pinned_apps_info.items():
            if uid not in normalized_existing:
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
                    "unique_id": str(uid).lower(),
                    "key": info["key"],
                    "original": info["original"],
                }
            )
        self._session_manager.save(pinned_keys, pinned_info)

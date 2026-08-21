from typing import Optional

_KB_ARTICLES = [
    {
        "id": "KB-001",
        "title": "App crashes on opening dashboard (iOS)",
        "keywords": ["crash", "ios", "dashboard", "app", "close", "freeze"],
        "summary": (
            "Known issue on iOS app versions below 4.2.1: opening the dashboard "
            "screen can cause a crash if cached data is corrupted. Fix: force-quit "
            "the app, then reopen — this clears the cache. If it recurs, update to "
            "4.2.1+ from the App Store."
        ),
    },
    {
        "id": "KB-002",
        "title": "Login fails with 'invalid credentials' despite correct password",
        "keywords": ["login", "password", "credentials", "invalid", "signin", "sign-in"],
        "summary": (
            "Usually caused by a stale session token. Fix: fully log out (not just "
            "close the app), clear the app's local storage/cache, then log back in. "
            "If using SSO, confirm the linked email hasn't changed on the identity "
            "provider's side."
        ),
    },
    {
        "id": "KB-003",
        "title": "Data not syncing between mobile and web",
        "keywords": ["sync", "syncing", "mobile", "web", "out of date", "stale", "not updating"],
        "summary": (
            "Sync runs on a 5-minute background interval on mobile; it does not "
            "sync instantly on every change. Force an immediate sync via "
            "Settings > Sync Now. If that doesn't resolve it, check "
            "check_service_status for the sync service specifically before "
            "assuming it's account-specific."
        ),
    },
    {
        "id": "KB-004",
        "title": "Dashboard loads slowly or times out",
        "keywords": ["slow", "loading", "timeout", "dashboard", "performance", "lag"],
        "summary": (
            "Most common cause is a large number of active widgets on one dashboard "
            "(50+). Recommend removing unused widgets. Distinct from a service "
            "outage — always check_service_status first to rule that out before "
            "treating this as account/config-specific."
        ),
    },
    {
        "id": "KB-005",
        "title": "Push notifications not arriving",
        "keywords": ["notification", "push", "alert", "not receiving"],
        "summary": (
            "Check device-level notification permissions first (most common cause, "
            "not an app bug). If permissions are correct, confirm the account's "
            "notification preferences weren't disabled in Settings > Notifications."
        ),
    },
]

def search_kb(query)
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .config import Settings
from .models import BackendStatus


class BackendProfile(BaseModel):
    url: str
    api_key: str | None = None
    org: str | None = None
    kind: str = "custom"


class BackendProfiles(BaseModel):
    profiles: dict[str, BackendProfile] = {}
    organizations: dict[str, BackendProfile] = {}


def load_backend_profiles(path: Path) -> BackendProfiles:
    if not path.exists():
        return BackendProfiles()
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return BackendProfiles()
    return BackendProfiles.model_validate_json(raw)


def resolve_backend(settings: Settings) -> BackendStatus:
    profiles = load_backend_profiles(settings.backend_profile_path)

    if settings.backend_url:
        return BackendStatus(
            name=settings.backend_profile or "explicit-backend",
            kind="custom" if not settings.backend_org else "corporate",
            selection_source="explicit_url",
            url=settings.backend_url,
            namespace_kind="org" if settings.backend_org else "user",
            namespace_id=settings.backend_org or "local",
            api_key_present=bool(settings.backend_api_key),
            org=settings.backend_org,
        )

    if settings.backend_profile and settings.backend_profile in profiles.profiles:
        profile = profiles.profiles[settings.backend_profile]
        return BackendStatus(
            name=settings.backend_profile,
            kind=profile.kind,
            selection_source="named_profile",
            url=profile.url,
            namespace_kind="org" if profile.org else "user",
            namespace_id=profile.org or "local",
            api_key_present=bool(settings.backend_api_key or profile.api_key),
            org=profile.org,
        )

    if settings.backend_org and settings.backend_org in profiles.organizations:
        profile = profiles.organizations[settings.backend_org]
        return BackendStatus(
            name=settings.backend_org,
            kind=profile.kind if profile.kind != "custom" else "corporate",
            selection_source="organization_profile",
            url=profile.url,
            namespace_kind="org",
            namespace_id=settings.backend_org,
            api_key_present=bool(settings.backend_api_key or profile.api_key),
            org=settings.backend_org,
        )

    if settings.default_backend_url:
        return BackendStatus(
            name="hosted-default",
            kind="hosted_default",
            selection_source="default_hosted",
            url=settings.default_backend_url,
            namespace_kind="org" if settings.backend_org else "user",
            namespace_id=settings.backend_org or "local",
            api_key_present=bool(settings.backend_api_key),
            org=settings.backend_org,
        )

    return BackendStatus(
        name="embedded-local",
        kind="local",
        selection_source="embedded_local",
        url=None,
        namespace_kind="org" if settings.backend_org else "user",
        namespace_id=settings.backend_org or "local",
        api_key_present=False,
        org=settings.backend_org,
    )

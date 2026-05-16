from __future__ import annotations

from typing import Any

from fastapi import Request

from coremcp.auth.oauth import OAuthService
from coremcp.auth.rate_limit import FixedWindowRateLimiter
from coremcp.db.repository import Repository
from coremcp.db.repository_facade import RepositoryFacades
from coremcp.settings import Settings


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


def get_repos(request: Request) -> RepositoryFacades:
    return request.app.state.repos


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_vault(request: Request) -> Any:
    return request.app.state.vault


def get_oauth_service(request: Request) -> OAuthService:
    return request.app.state.oauth


def get_oauth_dcr_rate_limiter(request: Request) -> FixedWindowRateLimiter:
    return request.app.state.oauth_dcr_rate_limiter

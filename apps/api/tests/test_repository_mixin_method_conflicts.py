"""Regression: Repository mixin 구성이 메서드 이름 충돌 없이 합쳐지는지 검증.

`Repository` 는 도메인별 mixin (Credentials, Connections, Audit, Jobs) 을 다중
상속으로 합친다. 두 mixin 이 같은 메서드 이름을 노출하면 MRO 순서에 따라
한쪽이 silent 으로 override 되어 운영 시 진단하기 어려운 결함을 만들 수 있다.

본 회귀는:
  - 모든 mixin pair 간 같은 이름의 callable 이 0 개
  - 어떤 mixin 의 메서드가 `Repository` base 의 동일 이름 메서드를 가리지 않음
"""

from __future__ import annotations

import inspect
import itertools
from pathlib import Path

from coremcp.db.repository import Repository
from coremcp.db.repository_audit import AuditRepositoryMixin
from coremcp.db.repository_catalog import CatalogRepositoryMixin
from coremcp.db.repository_connections import ConnectionsRepositoryMixin
from coremcp.db.repository_credentials import CredentialsRepositoryMixin
from coremcp.db.repository_jobs import JobsRepository
from coremcp.db.repository_services import ServicesRepositoryMixin
from coremcp.db.repository_toolbox import ToolboxRepositoryMixin

# Jobs graduated from mixin to composition (ADR-046 Step 1 / Phase 2,
# 2026-05-23 cycle) — Repository.jobs is now a JobsRepository instance.
MIXINS = [
    ServicesRepositoryMixin,
    CatalogRepositoryMixin,
    ToolboxRepositoryMixin,
    CredentialsRepositoryMixin,
    ConnectionsRepositoryMixin,
    AuditRepositoryMixin,
]


def _public_callables(cls: type) -> set[str]:
    names: set[str] = set()
    for name, value in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if inspect.isfunction(value) or inspect.iscoroutinefunction(value):
            names.add(name)
    return names


def test_repository_mixin_method_names_do_not_collide_pairwise() -> None:
    method_sets = {cls.__name__: _public_callables(cls) for cls in MIXINS}
    for left, right in itertools.combinations(MIXINS, 2):
        intersection = method_sets[left.__name__] & method_sets[right.__name__]
        assert not intersection, (
            f"{left.__name__} and {right.__name__} share method(s): {sorted(intersection)}"
        )


def test_repository_mixin_methods_do_not_shadow_base_repository_attributes() -> None:
    base_attrs = {
        name
        for name in vars(Repository)
        if not name.startswith("_")
    }
    for cls in MIXINS:
        names = _public_callables(cls)
        overlap = names & base_attrs
        assert not overlap, (
            f"{cls.__name__} shadows Repository base attribute(s): {sorted(overlap)}"
        )


def test_repository_combines_all_mixins() -> None:
    bases = Repository.__mro__
    for cls in MIXINS:
        assert cls in bases, f"Repository MRO is missing mixin: {cls.__name__}"


def test_repository_composes_jobs_facade(tmp_path: Path) -> None:
    """Jobs graduated to composition — verify the wiring is in place."""
    repo = Repository(database_path=tmp_path / "__composition_check__.sqlite3")
    assert isinstance(repo.jobs, JobsRepository)
    assert repo.jobs._repo is repo  # noqa: SLF001 - assertion on composition wiring

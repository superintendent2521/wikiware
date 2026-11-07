import importlib

import pytest

from src.schema import metadata


def test_postgres_schema_contains_normalized_relations():
    assert {
        "users",
        "pages",
        "page_branches",
        "revisions",
        "favorites",
        "branch_editors",
        "user_page_edit_counts",
        "sessions",
        "image_hashes",
        "analytics_events",
        "system_logs",
        "settings",
    } <= set(metadata.tables)


def test_branch_and_revision_relationships_cascade():
    branch_fk = next(iter(metadata.tables["page_branches"].c.page_id.foreign_keys))
    revision_fk = next(iter(metadata.tables["revisions"].c.page_branch_id.foreign_keys))
    assert branch_fk.ondelete == "CASCADE"
    assert revision_fk.ondelete == "CASCADE"


def test_image_metadata_needed_by_uploads_is_retained():
    assert {"filename", "sha256", "size", "last_modified", "url"} <= set(
        metadata.tables["image_hashes"].c.keys()
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "src.database",
        "src.services.page_service",
        "src.services.branch_service",
        "src.services.user_service",
        "src.services.settings_service",
        "src.services.analytics_service",
        "src.services.export_service",
        "src.routes.web.history",
        "src.routes.api.history",
        "src.server",
    ],
)
def test_migrated_modules_import(module_name):
    importlib.import_module(module_name)

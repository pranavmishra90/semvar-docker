from __future__ import annotations

from datetime import datetime, timedelta, timezone
from os import remove as delete_file
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time
from pytest_lazy_fixtures.lazy_fixture import lf as lazy_fixture

from semantic_release.changelog.context import ChangelogMode
from semantic_release.cli.commands.main import main

from tests.const import (
    MAIN_PROG_NAME,
    VERSION_SUBCMD,
)
from tests.e2e.conftest import (
    get_sanitized_md_changelog_content,
    get_sanitized_rst_changelog_content,
)
from tests.fixtures.example_project import (
    changelog_md_file,
    changelog_rst_file,
)
from tests.fixtures.repos import (
    repo_w_trunk_only_angular_commits,
)
from tests.util import (
    assert_successful_exit_code,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TypedDict

    from click.testing import CliRunner

    from tests.conftest import GetStableDateNowFn
    from tests.e2e.conftest import GetSanitizedChangelogContentFn
    from tests.fixtures.example_project import UpdatePyprojectTomlFn
    from tests.fixtures.git_repo import (
        BuildRepoFromDefinitionFn,
        BuiltRepoResult,
        CommitDef,
        GetCfgValueFromDefFn,
        GetVersionsFromRepoBuildDefFn,
        RepoActions,
        SplitRepoActionsByReleaseTagsFn,
    )

    class Commit2Section(TypedDict):
        angular: Commit2SectionCommit
        emoji: Commit2SectionCommit
        scipy: Commit2SectionCommit

    class Commit2SectionCommit(TypedDict):
        commit: CommitDef
        section: str


@pytest.mark.parametrize(
    str.join(
        ", ",
        [
            "custom_commit_message",
            "changelog_mode",
            "changelog_file",
            "get_sanitized_changelog_content",
            "repo_result",
            "cache_key",
        ],
    ),
    [
        pytest.param(
            custom_commit_message,
            changelog_mode,
            lazy_fixture(changelog_file),
            lazy_fixture(cl_sanitizer),
            lazy_fixture(repo_fixture_name),
            f"psr/repos/{repo_fixture_name}",
            marks=pytest.mark.comprehensive,
        )
        for changelog_mode in [ChangelogMode.INIT, ChangelogMode.UPDATE]
        for changelog_file, cl_sanitizer in [
            (
                changelog_md_file.__name__,
                get_sanitized_md_changelog_content.__name__,
            ),
            (
                changelog_rst_file.__name__,
                get_sanitized_rst_changelog_content.__name__,
            ),
        ]
        for repo_fixture_name, custom_commit_message in [
            *[
                (
                    # Repos: Must have at least 2 releases
                    repo_w_trunk_only_angular_commits.__name__,
                    commit_msg,
                )
                for commit_msg in [
                    dedent(
                        # Angular compliant prefix with skip-ci idicator
                        """\
                        chore(release): v{version} [skip ci]

                        Automatically generated by python-semantic-release.
                        """
                    ),
                ]
            ],
        ]
    ],
)
def test_version_changelog_content_custom_commit_message_excluded_automatically(
    repo_result: BuiltRepoResult,
    get_versions_from_repo_build_def: GetVersionsFromRepoBuildDefFn,
    get_cfg_value_from_def: GetCfgValueFromDefFn,
    split_repo_actions_by_release_tags: SplitRepoActionsByReleaseTagsFn,
    build_repo_from_definition: BuildRepoFromDefinitionFn,
    cli_runner: CliRunner,
    update_pyproject_toml: UpdatePyprojectTomlFn,
    changelog_file: Path,
    changelog_mode: ChangelogMode,
    custom_commit_message: str,
    cache: pytest.Cache,
    cache_key: str,
    stable_now_date: GetStableDateNowFn,
    example_project_dir: Path,
    get_sanitized_changelog_content: GetSanitizedChangelogContentFn,
):
    """
    Given a repo with a custom release commit message
    When the version subcommand is invoked with the changelog flag
    Then the resulting changelog content should not include the
    custom commit message

    It should work regardless of changelog mode and changelog file type
    """
    expected_changelog_content = get_sanitized_changelog_content(
        repo_dir=example_project_dir,
        remove_insertion_flag=bool(changelog_mode == ChangelogMode.INIT),
    )

    repo = repo_result["repo"]
    repo_def = repo_result["definition"]
    tag_format_str: str = get_cfg_value_from_def(repo_def, "tag_format_str")  # type: ignore[assignment]
    all_versions = get_versions_from_repo_build_def(repo_def)
    latest_tag = tag_format_str.format(version=all_versions[-1])
    previous_tag = tag_format_str.format(version=all_versions[-2])

    # split repo actions by release actions
    releasetags_2_steps: dict[str, list[RepoActions]] = (
        split_repo_actions_by_release_tags(repo_def, tag_format_str)
    )

    # Reverse release to make the previous version again with the new commit message
    repo.git.tag("-d", latest_tag)
    repo.git.reset("--hard", f"{previous_tag}~1")
    repo.git.tag("-d", previous_tag)

    # Set the project configurations
    update_pyproject_toml("tool.semantic_release.changelog.mode", changelog_mode.value)
    update_pyproject_toml(
        "tool.semantic_release.changelog.default_templates.changelog_file",
        str(changelog_file.name),
    )
    update_pyproject_toml(
        "tool.semantic_release.commit_message",
        custom_commit_message,
    )

    if not (repo_build_data := cache.get(cache_key, None)):
        pytest.fail("Repo build date not found in cache")

    repo_build_datetime = datetime.strptime(repo_build_data["build_date"], "%Y-%m-%d")
    now_datetime = stable_now_date().replace(
        year=repo_build_datetime.year,
        month=repo_build_datetime.month,
        day=repo_build_datetime.day,
    )

    if changelog_mode == ChangelogMode.UPDATE and len(all_versions) == 2:
        # When in update mode, and at the very first release, its better the
        # changelog file does not exist as we have an non-conformative example changelog
        # in the base example project
        delete_file(example_project_dir / changelog_file)

    cli_cmd = [MAIN_PROG_NAME, VERSION_SUBCMD, "--no-push", "--changelog"]

    # Act: make the first release again
    with freeze_time(now_datetime.astimezone(timezone.utc)):
        result = cli_runner.invoke(main, cli_cmd[1:])
        assert_successful_exit_code(result, cli_cmd)

    # Act: apply commits for change of version
    steps_for_next_release = releasetags_2_steps[latest_tag][
        :-1
    ]  # stop before the release step
    build_repo_from_definition(
        dest_dir=example_project_dir,
        repo_construction_steps=steps_for_next_release,
    )

    # Act: make the second release again
    with freeze_time(now_datetime.astimezone(timezone.utc) + timedelta(minutes=1)):
        result = cli_runner.invoke(main, cli_cmd[1:])

    actual_content = get_sanitized_changelog_content(
        repo_dir=example_project_dir,
        remove_insertion_flag=bool(changelog_mode == ChangelogMode.INIT),
    )

    # Evaluate
    assert_successful_exit_code(result, cli_cmd)
    assert expected_changelog_content == actual_content
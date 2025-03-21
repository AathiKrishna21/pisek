# pisek  - Tool for developing tasks for programming competitions.
#
# Copyright (c)   2023        Daniel Skýpala <daniel@honza.info>
# Copyright (c)   2024        Benjamin Swart <benjaminswart@email.cz>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import shutil
from typing import Optional

from pisek.utils.text import tab
from pisek.utils.paths import TaskPath, BUILD_DIR

from pisek.env.env import Env, TestingTarget
from pisek.config.task_config import BuildConfig, RunConfig
from pisek.config.config_types import BuildStrategyName, OutCheck

from pisek.task_jobs.tools import PrepareTokenJudge, PrepareShuffleJudge
from pisek.jobs.jobs import Job, PipelineItemFailure
from pisek.task_jobs.task_job import TaskJob
from pisek.task_jobs.task_manager import TaskJobManager

from pisek.task_jobs.builder.strategies import (
    BuildStrategy,
    AUTO_STRATEGIES,
    ALL_STRATEGIES,
)

WORKING_DIR = os.path.join(BUILD_DIR, "_pisek")


class BuildManager(TaskJobManager):
    """Builds task programs."""

    def __init__(self):
        self.skipped_validator = ""
        super().__init__("Build programs")

    def _build(self, run: Optional[RunConfig]) -> Optional["Build"]:
        if run is None:
            return None
        return Build(self._env, run.build)

    def _get_jobs(self) -> list[Job]:
        jobs: list[Job | None] = []

        jobs.append(self._build(self._env.config.in_gen))
        jobs.append(self._build(self._env.config.validator))
        if self._env.target in (TestingTarget.solution, TestingTarget.all):
            if self._env.config.out_check == OutCheck.judge:
                jobs.append(self._build(self._env.config.out_judge))
            elif self._env.config.out_check == OutCheck.tokens:
                jobs.append(PrepareTokenJudge(self._env))
            elif self._env.config.out_check == OutCheck.shuffle:
                jobs.append(PrepareShuffleJudge(self._env))

            for solution in self._env.solutions:
                jobs.append(self._build(self._env.config.solutions[solution].run))

        filtered_jobs = []
        for j in jobs:
            if j is not None:
                filtered_jobs.append(j)
        return filtered_jobs


class Build(TaskJob):
    """Job that compiles a program."""

    def __init__(
        self,
        env: Env,
        build_section: BuildConfig,
        **kwargs,
    ) -> None:
        super().__init__(env=env, name=f"Build {build_section.program_name}", **kwargs)
        self.build_section = build_section

    def _resolve_glob(self, glob: str) -> list[TaskPath]:
        result = self._globs_to_files([f"{glob}.*", glob], TaskPath("."))
        if len(result) == 0:
            raise PipelineItemFailure(f"No paths matching '{glob}'.")
        return result

    def _run(self) -> None:
        sources: list[TaskPath] = []
        for part in self.build_section.sources:
            part_sources = self._resolve_glob(part)
            for s in part_sources:
                if s not in sources:
                    sources.append(s)

        if any(map(lambda p: os.path.isdir(p.path), sources)) and any(
            map(lambda p: os.path.isfile(p.path), sources)
        ):
            raise PipelineItemFailure(
                f"Mixed files and directories for sources:\n"
                + tab(self._path_list(sources))
            )

        if self.build_section.strategy == BuildStrategyName.auto:
            strategy = self._resolve_strategy(sources)
        else:
            strategy = ALL_STRATEGIES[self.build_section.strategy]

        for extra in strategy.extra_files:
            for part in getattr(self.build_section, extra):
                new_sources = self._resolve_glob(part)
                for source in new_sources:
                    if os.path.isdir(source.path):
                        raise PipelineItemFailure(
                            f"{extra} matched directory '{source}'."
                        )
                sources += new_sources

        if self._env.verbosity >= 1:
            self._print(
                self._colored(
                    tab(
                        f"Building '{self.build_section.program_name}' using build strategy '{strategy.name}'."
                    ),
                    "magenta",
                )
            )

        if os.path.exists(WORKING_DIR):
            shutil.rmtree(WORKING_DIR)
        os.makedirs(WORKING_DIR, exist_ok=True)

        for source in sources:
            shutil.copy(source.path, os.path.join(WORKING_DIR, source.name))
            self._access_file(source)

        target = TaskPath(BUILD_DIR, self.build_section.program_name)
        self.make_filedirs(target)
        if os.path.isdir(target.path):
            shutil.rmtree(target.path)
        elif os.path.isfile(target.path):
            os.remove(target.path)

        built = os.path.join(
            WORKING_DIR,
            strategy(self.build_section, self._env, self._print).build(WORKING_DIR),
        )
        if os.path.isdir(built):
            shutil.copytree(built, target.path)
        else:
            shutil.copy(built, target.path)
        self._access_file(target)

    def _resolve_strategy(self, sources: list[TaskPath]) -> type[BuildStrategy]:
        applicable = []
        for strategy in AUTO_STRATEGIES:
            if strategy.applicable(
                self.build_section, list(map(lambda p: p.path, sources))
            ):
                applicable.append(strategy)
        if len(applicable) == 0:
            raise PipelineItemFailure(
                f"No applicable build strategy for [{self.build_section.section_name}] with sources:\n"
                + tab(self._path_list(sources))
            )
        elif len(applicable) >= 2:
            names = " ".join(s.name for s in applicable)
            raise RuntimeError(f"Multiple strategies applicable: {names}")

        return applicable[0]

from setuptools import setup
from setuptools.command.build_py import build_py


class ReleaseBuildPy(build_py):
    """Exclude the operator-owned live evaluation helper from artifacts."""

    def find_package_modules(
        self,
        package: str,
        package_dir: str,
    ) -> list[tuple[str, str, str]]:
        modules = super().find_package_modules(package, package_dir)
        return [
            module
            for module in modules
            if module[1] != "codex_research_eval"
        ]


setup(cmdclass={"build_py": ReleaseBuildPy})

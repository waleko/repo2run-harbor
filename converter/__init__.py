"""Convert Repo2Run task type into Harbor dataset format.

Repo2Run defines a task: "Given (repo, base_sha), make `pytest --collect-only`
succeed in /repo." This package packages that task type as Harbor task dirs.
"""

__all__ = ["convert_one", "build_dataset"]

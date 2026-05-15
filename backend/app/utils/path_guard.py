import os


def guard_within(path: str, root: str) -> str:
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if real_path != real_root and not real_path.startswith(real_root + os.sep):
        raise PermissionError(
            f"Path escape blocked: {real_path} is not within {real_root}"
        )
    return real_path

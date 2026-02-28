DEFAULT_ARTIFACT_CONTRACT_VERSION = "v1"
DEFAULT_ARTIFACT_COMPAT_POLICY = "strict"


def build_artifact_repository(*args: object, **kwargs: object):
    from app.lib.artifacts.factory import build_artifact_repository as _build_artifact_repository

    return _build_artifact_repository(*args, **kwargs)


__all__ = ["DEFAULT_ARTIFACT_CONTRACT_VERSION", "DEFAULT_ARTIFACT_COMPAT_POLICY", "build_artifact_repository"]

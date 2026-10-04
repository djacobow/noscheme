"""Repository search/navigation settings for https://github.com/djacobow/st."""

# st applies exclusions to complete paths using regular expressions.
EXCLUDE = (r"/(?:build|\.git)/",)

CONFIG = {
    "subpaths": {
        "rr": ("repository root", []),
        "src": ("sources", ["src"]),
        "dist": ("distribution_artifacts", ["dist"]),
        "build": ("build_stuff", ["build"]),
        "tests": ("tests", ["tests"]),
    },
    "code_file_types": {
        "h": {"default": True},
        "py": {"default": True, "grep_extra_glob": [".st_config.py"]},
        "nosch": {"default": True},
        "md": {"default": True},
        "ini": {"default": True},
    },
    "code_search_paths": {
        "nosch": {
            "src": {"default": True, "include": ["src"]},
            "build": {"default": False, "include": ["build"]},
            "dist": {"default": False, "include": ["dist"]},
        },
    },
}

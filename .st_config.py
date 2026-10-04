"""Repository search/navigation settings for https://github.com/djacobow/st."""

# st reads one regex string from each search scope's "exclude" entry.
SOURCE_EXCLUDE = r"/(?:build|dist|output|__pycache__|[^/]+\.egg-info|\.[^/]+)/"
CACHE_EXCLUDE = r"/(?:__pycache__|[^/]+\.egg-info|\.[^/]+)/"

CONFIG = {
    "subpaths": {
        "rr": ("repository root", []),
        "src": ("sources", ["src"]),
        "dist": ("distribution_artifacts", ["dist"]),
        "build": ("build_stuff", ["build"]),
        "tests": ("tests", ["tests"]),
        "examples": ("examples", ["examples"]),
        "experiments": ("experiments", ["experiments"]),
    },
    "code_file_types": {
        "h": {"default": True},
        "py": {"default": True, "grep_extra_glob": [".st_config.py"]},
        "nosch": {"default": True},
        "md": {"default": True},
        "ini": {"default": True},
        "toml": {"default": True},
        "lark": {"default": True},
    },
    # Keys are Git remote repository names, not filename suffixes.
    "code_search_paths": {
        "noscheme": {
            "rr": {"default": True, "include": ["."], "exclude": SOURCE_EXCLUDE},
            "src": {"default": False, "include": ["src"], "exclude": SOURCE_EXCLUDE},
            "tests": {"default": False, "include": ["tests"], "exclude": SOURCE_EXCLUDE},
            "examples": {"default": False, "include": ["examples"], "exclude": SOURCE_EXCLUDE},
            "experiments": {
                "default": False,
                "include": ["experiments"],
                "exclude": SOURCE_EXCLUDE,
            },
            "build": {"default": False, "include": ["build"], "exclude": CACHE_EXCLUDE},
            "dist": {"default": False, "include": ["dist"], "exclude": CACHE_EXCLUDE},
        },
    },
}

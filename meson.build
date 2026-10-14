project(
    'noscheme-native',
    'cpp',
    version: '0.1.0',
    meson_version: '>=1.3.0',
    default_options: [
        'cpp_std=c++23',
        'warning_level=3',
        'buildtype=debugoptimized',
        # Release builds drop assert and libstdc++ assertion checks.
        'b_ndebug=if-release',
    ],
)
cmake = import('cmake')
json_options = cmake.subproject_options()
json_options.add_cmake_defines({'JSON_BuildTests': false})
json_project = cmake.subproject('nlohmann_json', options: json_options)
json_dep = json_project.dependency('nlohmann_json')
toml_dep = subproject('tomlplusplus').get_variable('tomlplusplus_dep').as_system(
    'system',
)
build_python = find_program('python3')
antlr_options = cmake.subproject_options()
antlr_options.add_cmake_defines(
    {
        'ANTLR_BUILD_CPP_TESTS': false,
        'ANTLR_BUILD_SHARED': false,
        'ANTLR_BUILD_STATIC': true,
    },
)
antlr = cmake.subproject('antlr4-runtime', options: antlr_options)
antlr_dep = antlr.dependency('antlr4_static').as_system('system')
find_program('java')
generator = files('tools/native_dependencies.py')
generated = custom_target(
    'antlr-parser',
    input: 'cpp/grammar/NoScheme.g4',
    output: [
        'NoSchemeLexer.cpp',
        'NoSchemeLexer.h',
        'NoSchemeParser.cpp',
        'NoSchemeParser.h',
    ],
    command: [build_python, generator, '--generate', '@INPUT@', '@OUTDIR@'],
    depend_files: files('cpp/dependencies.json'),
)
inc = include_directories('cpp/include')
core = static_library(
    'noscheme-core',
    'cpp/src/ast.cpp',
    'cpp/src/meta.cpp',
    'cpp/src/source.cpp',
    'cpp/src/parser.cpp',
    'cpp/src/classify.cpp',
    'cpp/src/formatter.cpp',
    'cpp/src/validation.cpp',
    'cpp/src/project.cpp',
    'cpp/src/evaluate.cpp',
    'cpp/src/parameters.cpp',
    'cpp/src/inspect.cpp',
    'cpp/src/registry.cpp',
    'cpp/src/python_bridge.cpp',
    'cpp/src/erc.cpp',
    'cpp/src/erc_standard.cpp',
    'cpp/src/metadata.cpp',
    'cpp/src/resolve.cpp',
    'cpp/src/resolve_body.cpp',
    'cpp/src/circuit.cpp',
    'cpp/src/lsp_analysis.cpp',
    'cpp/src/lsp_local_index.cpp',
    'cpp/src/lsp_project_discovery.cpp',
    'cpp/src/lsp_references.cpp',
    'cpp/src/lsp_rename.cpp',
    'cpp/src/lsp_completion.cpp',
    'cpp/src/lsp_scope_completion.cpp',
    'cpp/src/lsp_code_actions.cpp',
    'cpp/src/lsp_erc_completion.cpp',
    'cpp/src/lsp_instance_completion.cpp',
    'cpp/src/lsp_section_completion.cpp',
    'cpp/src/lsp_keyword_completion.cpp',
    generated,
    include_directories: inc,
    dependencies: [
        json_dep,
        antlr_dep,
        toml_dep,
    ],
)
probe = executable(
    'noscheme-native-probe',
    'cpp/src/probe.cpp',
    link_with: core,
    include_directories: inc,
    dependencies: [json_dep, antlr_dep],
)
native_cli = executable(
    'noscheme-native',
    'cpp/src/cli.cpp',
    'cpp/src/export.cpp',
    'cpp/src/sheets.cpp',
    link_with: core,
    include_directories: inc,
    dependencies: [
        json_dep,
        antlr_dep,
        dependency('openssl'),
    ],
)
py = find_program(
    meson.project_source_root() / '.venv/bin/python',
    required: false,
)
if not py.found()
    py = build_python
endif
test(
    'native-parser-parity',
    py,
    args: [files('cpp/tests/parser_parity.py'), probe],
    timeout: 120,
)
test(
    'native-project-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_project_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)
test(
    'native-evaluate-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_evaluate_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)
test(
    'native-parameters-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_parameters_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)
test(
    'native-registry-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_registry_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)
test(
    'native-metadata-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_metadata_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)
test(
    'native-resolve-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_resolve_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)

test(
    'native-body-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_body_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)

test(
    'native-circuit-parity',
    py,
    args: [
        '-m',
        'pytest',
        '-q',
        files(
            'cpp/tests/test_circuit_parity.py',
            'cpp/tests/test_concise_parity.py',
        ),
    ],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)

test(
    'native-erc-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_erc_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    timeout: 180,
)

native_lsp = executable(
    'noscheme-native-lsp',
    'cpp/src/lsp.cpp',
    link_with: core,
    include_directories: inc,
    dependencies: [
        json_dep,
        antlr_dep,
        dependency('threads'),
    ],
)
test(
    'native-lsp',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_lsp.py')],
    env: {'NOSCHEME_NATIVE_LSP': native_lsp.full_path()},
    depends: native_lsp,
    timeout: 180,
)

test(
    'native-formatter-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_formatter_parity.py')],
    env: {'NOSCHEME_NATIVE_PROBE': probe.full_path()},
    depends: probe,
    timeout: 180,
)

test(
    'native-rename-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_rename_parity.py')],
    env: {'NOSCHEME_NATIVE_LSP': native_lsp.full_path()},
    depends: native_lsp,
    timeout: 180,
)

test(
    'native-cli-parity',
    py,
    args: ['-m', 'pytest', '-q', files('cpp/tests/test_cli.py')],
    env: {'NOSCHEME_NATIVE_TOOL': native_cli.full_path()},
    depends: native_cli,
    timeout: 180,
)

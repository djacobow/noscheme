import json

from noscheme.cli import main
from noscheme.lint import unused_parameters
from noscheme.resolve import resolve_project
from test_circuit import make

SOURCE = """from noscheme import num
component P:
  params:
    string value = "10k"
    number resistance = 10000
    number placeholder = 0
    number unused_arg = 1
  pins:
    1:
      role: role.passive
      pull_resistance: resistance
extend P<resistance: 4700> Specialized
block B:
  Specialized<unused_arg: 2> p:
    1: no_connect
"""


def test_lint_deduplicates_inheritance_and_excludes_argument_labels(tmp_path):
    make(tmp_path, SOURCE)
    warnings = unused_parameters(resolve_project(tmp_path)[1])
    assert len(warnings) == 2
    assert "placeholder" in warnings[0].message
    assert "unused_arg" in warnings[1].message
    assert all(w.code == "lint.unused-parameter" for w in warnings)


def test_lint_cli_is_opt_in_and_writes_no_artifacts(tmp_path, capsys):
    make(tmp_path, SOURCE)
    assert main(["lint", "--project", str(tmp_path), "--diagnostics", "json"]) == 0
    output = capsys.readouterr()
    assert len(json.loads(output.out)["diagnostics"]) == 2
    assert output.err == ""
    assert not (tmp_path / "build").exists()
    assert main(["check", "--project", str(tmp_path)]) == 0
    assert "lint.unused-parameter" not in capsys.readouterr().err


def test_element_expression_and_dimensions_are_uses(tmp_path):
    source = SOURCE.replace(
        "block B:",
        """block Group:
  params:
    number count = 2
    number resistance = 3300
  P p[count]:
    1: no_connect
    elements:
      [0]:
        params:
          resistance: resistance
block B:""",
    )
    make(tmp_path, source)
    assert len(unused_parameters(resolve_project(tmp_path)[1])) == 2


def test_format_member_name_is_not_a_parameter_use(tmp_path):
    source = SOURCE.replace("number placeholder = 0", "number round = 0").replace(
        'string value = "10k"', 'string value = "{0}".format(num.round(10))'
    )
    make(tmp_path, source)
    assert "round" in unused_parameters(resolve_project(tmp_path)[1])[0].message

"""Hierarchical KiCad schematics: sharing, determinism, upkeep, and KiCad round trips."""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from noscheme.circuit import elaborate_project
from noscheme.cli import main
from noscheme.kicad.export import export_project, net_classes
from noscheme.kicad.sheets import library_table
from noscheme.resolve import resolve_project

ROOT = Path(__file__).resolve().parents[1]
KICAD = shutil.which("kicad-cli")
STAND_IN = "NoSchemeStandIn"
KICAD_FOOTPRINTS = Path("/usr/share/kicad/footprints")


def targets():
    """(example, target) for every example project and each of its targets."""
    out = []
    for config in sorted((ROOT / "examples").glob("*/noscheme.toml")):
        for target in tomllib.loads(config.read_text())["targets"]:
            out.append((config.parent.name, target))
    return out


def copy_example(name, tmp_path):
    """The examples, copied together: some find libraries in their siblings."""
    examples = tmp_path / "examples"
    if not examples.exists():
        shutil.copytree(
            ROOT / "examples", examples, ignore=shutil.ignore_patterns("build", "pcb", "kicad")
        )
    return examples / name


def stand_in(part):
    """A footprint for a part that has none: one pad per pin, named for the part."""
    return f"{STAND_IN}:{part['definition'].replace('.', '_')}"


def installed(footprint):
    library, item = footprint.split(":", 1)
    return (KICAD_FOOTPRINTS / f"{library}.pretty" / f"{item}.kicad_mod").exists()


def prepare(name, target, tmp_path):
    """An example ready to export: elaborated, with footprint records, and a KiCad
    project directory. Parts without footprints get stand-ins, and footprint
    records come from the example's snapshot or, without one, from the pins."""
    project_dir = copy_example(name, tmp_path)
    project, definitions = resolve_project(project_dir, target=target)
    circuit, _ = elaborate_project(project, definitions)
    kicad = tmp_path / "kicad"
    kicad.mkdir()
    own = ROOT / "examples" / name / "pcb"
    own_libs = {p.stem for p in own.glob("*.pretty")}
    stand_ins = {}  # library -> {item: pads}
    snapshot = project_dir / "kicad_lib/footprints.json"
    records = json.loads(snapshot.read_text()) if snapshot.exists() else None
    synthetic = {"schema_version": 1, "footprints": {}}
    for part in circuit["parts"]:
        if "footprint" not in part["metadata"]:
            part["metadata"]["footprint"] = {"value": stand_in(part)}
        name_ = part["metadata"]["footprint"]["value"]
        pads = sorted(part["pins"])
        library, item = name_.split(":", 1)
        if library not in own_libs and not installed(name_):
            # Not installed here (wren's CERN libraries): stand in for KiCad's check.
            stand_ins.setdefault(library, {})[item] = pads
        if records is None or library == STAND_IN:
            synthetic["footprints"][name_] = {"exists": True, "pads": pads}
    if records is not None:
        records["footprints"].update(synthetic["footprints"])
    else:
        records = synthetic
    path = tmp_path / "footprints.json"
    path.write_text(json.dumps(records))
    # Footprint libraries: the stand-ins, plus any the example's KiCad project has.
    libs = []
    for library, items in sorted(stand_ins.items()):
        pretty = kicad / f"{library}.pretty"
        pretty.mkdir()
        for item, pads in items.items():
            (pretty / f"{item}.kicad_mod").write_text(
                f'(footprint "{item}" (layer "F.Cu")\n'
                + "".join(
                    f'  (pad "{pad}" smd rect (at {i * 2} 0) (size 1 1) (layers "F.Cu"))\n'
                    for i, pad in enumerate(pads)
                )
                + ")\n"
            )
        libs.append(
            f'  (lib (name "{library}")(type "KiCad")(uri "${{KIPRJMOD}}/{library}.pretty")'
            '(options "")(descr ""))'
        )
    if (own / "fp-lib-table").exists():
        libs += [
            line for line in (own / "fp-lib-table").read_text().splitlines() if "(lib " in line
        ]
        for library in own.glob("*.pretty"):
            shutil.copytree(library, kicad / library.name)
    (kicad / "fp-lib-table").write_text(
        "(fp_lib_table\n  (version 7)\n" + "\n".join(libs) + "\n)\n"
    )
    # The project defines the net classes the design names, as a user's would.
    classes = [{"name": "Default"}] + [
        {"name": c} for c in sorted(set(net_classes(circuit).values()))
    ]
    root = kicad / f"{name}.kicad_sch"
    root.with_suffix(".kicad_pro").write_text(
        json.dumps({"net_settings": {"classes": classes, "meta": {"version": 4}}})
    )
    return project, circuit, path, root


def export(project_dir, root):
    project, definitions = resolve_project(project_dir)
    circuit, _ = elaborate_project(project, definitions)
    records = project_dir / "kicad_lib/footprints.json"
    return export_project(project, circuit, records, schematic=root)


@pytest.fixture
def clock(tmp_path):
    project_dir = copy_example("clock", tmp_path)
    shutil.copytree(
        ROOT / "examples/clock/pcb",
        project_dir / "pcb",
        ignore=shutil.ignore_patterns("*.kicad_pcb", "*.kicad_sch", "snapshots", ".history"),
    )
    root = project_dir / "pcb/honest_clock.kicad_sch"
    return project_dir, root, export(project_dir, root)


def test_writes_one_file_per_distinct_block(clock):
    project_dir, root, report = clock
    files = {Path(f).name for f in report["schematic"]}
    assert {root.name, "display_Digit.kicad_sch", "noscheme.kicad_sym"} <= files
    # The nine display digits are one sheet file, used nine times.
    digit = (root.parent / "display_Digit.kicad_sch").read_text()
    driver = digit[digit.index('(lib_id "noscheme:display.LedDriver")') :]
    driver = driver[: driver.index("(lib_id", 10)]
    assert len(re.findall(r'\(reference "U\d+"\)', driver)) == 9
    display = (root.parent / "display_Display.kicad_sch").read_text()
    assert display.count('(property "Sheetfile" "display_Digit.kicad_sch"') == 9


def test_same_design_same_sheets(clock):
    project_dir, root, report = clock
    before = {f: Path(f).read_bytes() for f in report["schematic"]}
    again = export(project_dir, root)
    assert {f: Path(f).read_bytes() for f in again["schematic"]} == before


def test_removes_only_its_own_stale_sheets(clock):
    project_dir, root, _ = clock
    ours = root.parent / "retired_Block.kicad_sch"
    ours.write_text('(kicad_sch\n\t(version 20250114)\n\t(generator "noscheme")\n)\n')
    theirs = root.parent / "notes.kicad_sch"
    theirs.write_text('(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n)\n')
    report = export(project_dir, root)
    assert report["schematic_removed"] == [str(ours)]
    assert not ours.exists() and theirs.exists()


def test_library_table_keeps_other_libraries():
    table = '(sym_lib_table\n  (version 7)\n  (lib (name "mine")(type "KiCad")(uri "x")(options "")(descr ""))\n)\n'
    merged = library_table(table)
    assert '(name "mine")' in merged and '(name "noscheme")' in merged
    assert library_table(merged) == merged
    assert '(name "noscheme")' in library_table(None)


def nets(text):
    """Net name -> nodes, leaving out nets KiCad leaves unnamed.

    A net with a single pin is a no-connect in the sheets, so KiCad names it
    `unconnected-(...)`; there is nothing on it to compare.
    """
    found = {}
    pattern = r'\(net\s+\(code "?\d+"?\)\s+\(name "((?:[^"\\]|\\.)*)"\)(.*?)(?=\(net\s+\(code|\Z)'
    for match in re.finditer(pattern, text, re.S):
        nodes = frozenset(re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"', match[2]))
        if match[1].startswith("unconnected-") or len(nodes) < 2:
            continue
        found[match[1]] = nodes
    return found


def components(text):
    pattern = r'\(comp\s+\(ref "([^"]+)"\)\s+\(value "([^"]*)"\)\s+\(footprint "([^"]*)"\)'
    return {m[1]: (m[2], m[3]) for m in re.finditer(pattern, text)}


def kicad_netlist(root, out):
    subprocess.run(
        [KICAD, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, root],
        check=True,
        capture_output=True,
    )
    return out.read_text()


@pytest.mark.skipif(KICAD is None, reason="needs KiCad's kicad-cli")
@pytest.mark.parametrize(("name", "target"), targets())
def test_kicad_agrees(name, target, tmp_path):
    """KiCad reads back the exported netlist; with NoScheme's ERC clean, so is KiCad's."""
    project, circuit, records, root = prepare(name, target, tmp_path)
    export_project(project, circuit, records, schematic=root)
    ours = next(project.config.output_dir.rglob("board.net")).read_text()
    theirs = kicad_netlist(root, tmp_path / "kicad.net")
    assert nets(theirs) == nets(ours)
    assert components(theirs) == components(ours)
    report = tmp_path / "erc.rpt"
    subprocess.run(
        [KICAD, "sch", "erc", "--severity-all", "-o", report, root],
        check=True,
        capture_output=True,
    )
    if circuit["erc"]["errors"] == 0:
        assert "ERC messages: 0  Errors 0  Warnings 0" in report.read_text(), report.read_text()


@pytest.mark.skipif(KICAD is None, reason="needs KiCad's kicad-cli")
def test_net_classes_reach_kicad(clock, tmp_path):
    project_dir, root, _ = clock
    netlist = tmp_path / "kicad.net"
    subprocess.run(
        [KICAD, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", netlist, root],
        check=True,
        capture_output=True,
    )
    classes = dict(
        re.findall(
            r'\(net\s+\(code "?\d+"?\)\s+\(name "([^"]*)"\)\s+\(class "([^"]*)"\)',
            netlist.read_text(),
        )
    )
    assert {n: classes[n] for n in ("gnd", "v3v3", "v3v3_quiet", "v5", "vbus")} == dict.fromkeys(
        ("gnd", "v3v3", "v3v3_quiet", "v5", "vbus"), "Power"
    )


def test_native_implementation_refuses_schematic(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(
            [
                "export-kicad",
                "--project",
                str(tmp_path),
                "--footprints",
                str(tmp_path / "f.json"),
                "--schematic",
                str(tmp_path / "x.kicad_sch"),
                "--implementation",
                "native",
            ]
        )
    assert "--schematic is not supported by --implementation native" in capsys.readouterr().err

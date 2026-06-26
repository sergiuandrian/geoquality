"""JUnit XML reporter for CI-native test reporting.

Each layer becomes a ``<testsuite>`` and each check a ``<testcase>``. FAIL maps
to ``<failure>``, ERROR (a check that crashed) to ``<error>``, SKIP to
``<skipped>``, and WARN to a passing case with the message in ``<system-out>``.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from geoqa.result import Report, Status


def write_junit(report: Report, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(build_junit(report)).write(path, encoding="utf-8", xml_declaration=True)
    return path


def build_junit(report: Report) -> ET.Element:
    total = failures = errors = skipped = 0
    duration = 0.0
    root = ET.Element("testsuites", name=report.suite_name)

    for layer in report.layers:
        l_tests = l_fail = l_err = l_skip = 0
        l_time = 0.0
        suite_el = ET.SubElement(root, "testsuite", name=layer.layer)
        suite_el.set("package", layer.source)
        for r in layer.results:
            l_tests += 1
            l_time += r.duration_s
            case = ET.SubElement(
                suite_el, "testcase", name=r.check, classname=layer.layer,
                time=f"{r.duration_s:.4f}",
            )
            if r.status == Status.FAIL:
                l_fail += 1
                ET.SubElement(case, "failure", message=r.message, type=r.severity.value)
            elif r.status == Status.ERROR:
                l_err += 1
                ET.SubElement(case, "error", message=r.message, type="error")
            elif r.status == Status.SKIP:
                l_skip += 1
                ET.SubElement(case, "skipped", message=r.message)
            elif r.status == Status.WARN:
                out = ET.SubElement(case, "system-out")
                out.text = f"WARNING: {r.message}"

        suite_el.set("tests", str(l_tests))
        suite_el.set("failures", str(l_fail))
        suite_el.set("errors", str(l_err))
        suite_el.set("skipped", str(l_skip))
        suite_el.set("time", f"{l_time:.4f}")

        total += l_tests
        failures += l_fail
        errors += l_err
        skipped += l_skip
        duration += l_time

    root.set("tests", str(total))
    root.set("failures", str(failures))
    root.set("errors", str(errors))
    root.set("skipped", str(skipped))
    root.set("time", f"{duration:.4f}")
    return root

"""Small behavioral checks for numeric propagation and report promotion.

The records are synthetic. They do not validate real datasheet requirements,
operating modes, or electrical-review accuracy.
"""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pureagent.calculations import evaluate
from pureagent.report import build_report


def divider_record():
    return {
        "id": "synthetic_unloaded_divider",
        "inputs": {
            "vin": {"value": 5, "unit": "V", "source": "synthetic fixed source"},
            "rtop": {"value": 10000, "unit": "ohm", "source": "synthetic R1 value"},
            "rbot": {"value": 10000, "unit": "ohm", "source": "synthetic R2 value"},
        },
        "expression": "vin * rbot / (rtop + rbot)",
        "result_unit": "V",
        "assumptions": ["This synthetic divider has no load."],
        "validity": {"status": "valid", "reason": "The fixture defines a DC source and no load."},
    }


def report_records():
    candidate = {
        "id": "synthetic_candidate",
        "investigator": "investigator-a",
        "title": "Synthetic electrical candidate",
        "why": "The synthetic connection violates the supplied synthetic requirement.",
        "recommendation": "Correct the synthetic connection.",
        "internal_note": "missing package metadata is not an electrical error",
    }
    review = {
        "candidate_id": candidate["id"],
        "reviewer": "reviewer-b",
        "verdict": "confirmed",
        "raw_connection_evidence": {"source": "synthetic netlist", "pins": ["U1.1", "J1.1"]},
        "datasheet_evidence": {"source": "synthetic requirement", "page": 1},
    }
    coverage = {"investigated_count": 1, "total_count": 4, "full_board": False,
                "scope": "One synthetic circuit; not a full board review."}
    return candidate, review, coverage


class CalculationTests(unittest.TestCase):
    def test_valid_record_and_mode_changes_do_not_propagate_stale_values(self):
        original = divider_record()
        before = deepcopy(original)
        valid = evaluate(original)
        self.assertEqual(valid["result"], 2.5)
        self.assertTrue(valid["usable"])
        self.assertEqual(original, before)
        for key in ("inputs", "expression", "result_unit", "assumptions", "validity"):
            self.assertEqual(valid[key], original[key])
        for mode in ("conditional", "invalid", "unknown"):
            with self.subTest(mode=mode):
                changed = deepcopy(valid)
                changed["validity"]["status"] = mode
                changed["trial_result"] = 999
                output = evaluate(changed)
                self.assertFalse(output["usable"])
                self.assertIsNone(output["result"])
                if mode == "conditional":
                    self.assertEqual(output["trial_result"], 2.5)
                else:
                    self.assertNotIn("trial_result", output)

    def test_missing_unknown_unsourced_and_nonfinite_inputs_do_not_compute(self):
        for value in (None, "unknown", float("nan"), float("inf"), True):
            with self.subTest(value=value):
                record = divider_record()
                record["inputs"]["vin"]["value"] = value
                output = evaluate(record)
                self.assertFalse(output["usable"])
                self.assertIsNone(output["result"])
                self.assertNotIn("trial_result", output)
        for change in ("missing_variable", "missing_source"):
            with self.subTest(change=change):
                record = divider_record()
                if change == "missing_variable":
                    del record["inputs"]["rbot"]
                else:
                    record["inputs"]["rbot"]["source"] = {"quote": "  "}
                output = evaluate(record)
                self.assertFalse(output["usable"])
                self.assertIsNone(output["result"])

    def test_illegal_expression_cannot_execute_code_or_access_attributes(self):
        with TemporaryDirectory(prefix="pureagent_expression_test_") as directory:
            marker = Path(directory) / "executed.txt"
            expressions = [
                f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')",
                "vin.__class__", "[vin][0]", "[x for x in (1, 2)]", "vin // 2",
            ]
            for expression in expressions:
                with self.subTest(expression=expression):
                    record = divider_record()
                    record["expression"] = expression
                    output = evaluate(record)
                    self.assertFalse(output["usable"])
                    self.assertIsNone(output["result"])
            self.assertFalse(marker.exists())

    def test_unused_context_does_not_block_but_used_unknown_still_does(self):
        record = divider_record()
        record["inputs"]["unrelated_current"] = {"value": None, "unit": "A", "source": None}
        record["inputs"]["unrelated_note"] = "Unstructured context is not part of this expression."
        before = deepcopy(record)
        output = evaluate(record)
        self.assertEqual(output["result"], 2.5)
        self.assertTrue(output["usable"])
        self.assertEqual(output["inputs"], before["inputs"])
        self.assertEqual(record, before)

        record["expression"] = "vin * rbot / (rtop + rbot) + unrelated_current"
        failed = evaluate(record)
        self.assertFalse(failed["usable"])
        self.assertIsNone(failed["result"])
        self.assertIn("unrelated_current", failed["reason"])

    def test_function_names_are_not_inputs_but_bare_same_named_variable_is(self):
        record = divider_record()
        record["expression"] = "min(vin, 4)"
        self.assertEqual(evaluate(record)["result"], 4)

        record["expression"] = "min(vin, 4) + min"
        missing = evaluate(record)
        self.assertFalse(missing["usable"])
        record["inputs"]["min"] = {"value": 1, "unit": "V", "source": "synthetic independent input"}
        self.assertEqual(evaluate(record)["result"], 5)

    def test_allowed_functions_and_arithmetic_domain_errors(self):
        record = divider_record()
        record["expression"] = "min(max(sqrt(vin ** 2), abs(-vin)), 10)"
        output = evaluate(record)
        self.assertEqual(output["result"], 5)
        self.assertTrue(output["usable"])
        for expression in ("vin / 0", "sqrt(-1)", "10 ** 1000000000", "1e308 * 1e308"):
            with self.subTest(expression=expression):
                record["expression"] = expression
                output = evaluate(record)
                self.assertFalse(output["usable"])
                self.assertIsNone(output["result"])


class ReportTests(unittest.TestCase):
    def test_independent_confirmation_keeps_evidence_and_omits_internal_notes(self):
        candidate, review, coverage = report_records()
        before = deepcopy((candidate, review, coverage))
        report = build_report([candidate], [review], coverage)
        self.assertEqual(report["run_status"], "partial_review")
        self.assertEqual(report["scope"], coverage["scope"])
        self.assertEqual(len(report["confirmed_errors"]), 1)
        error = report["confirmed_errors"][0]
        self.assertEqual(error["raw_connection_evidence"], review["raw_connection_evidence"])
        self.assertEqual(error["datasheet_evidence"], review["datasheet_evidence"])
        self.assertNotIn("internal_note", error)
        self.assertEqual((candidate, review, coverage), before)

    def test_self_review_and_incomplete_evidence_cannot_promote(self):
        for change in ("same_actor", "missing_investigator", "missing_reviewer",
                       "raw_connection_evidence", "datasheet_evidence", "why", "recommendation"):
            with self.subTest(change=change):
                candidate, review, coverage = report_records()
                if change == "same_actor":
                    review["reviewer"] = " INVESTIGATOR-A "
                elif change == "missing_investigator":
                    del candidate["investigator"]
                elif change == "missing_reviewer":
                    del review["reviewer"]
                elif change in {"why", "recommendation"}:
                    candidate[change] = " "
                else:
                    review[change] = [None, "", {}]
                report = build_report([candidate], [review], coverage)
                self.assertEqual(report["confirmed_errors"], [])
                self.assertEqual(report["run_status"], "partial_review")

    def test_applicable_requirement_can_replace_datasheet_without_weakening_review(self):
        candidate, review, coverage = report_records()
        del review["datasheet_evidence"]
        review["requirement_evidence"] = {
            "source": "Synthetic user-supplied interface contract",
            "requirement": "The two specified physical contacts must remain on separate nets.",
        }
        report = build_report([candidate], [review], coverage)
        self.assertEqual(len(report["confirmed_errors"]), 1)
        error = report["confirmed_errors"][0]
        self.assertEqual(error["requirement_evidence"], review["requirement_evidence"])
        self.assertNotIn("datasheet_evidence", error)

        for change in ("empty_requirement", "self_review", "missing_connection"):
            with self.subTest(change=change):
                changed = deepcopy(review)
                if change == "empty_requirement":
                    changed["requirement_evidence"] = [None, {}, " "]
                elif change == "self_review":
                    changed["reviewer"] = candidate["investigator"]
                else:
                    changed["raw_connection_evidence"] = None
                self.assertEqual(build_report([candidate], [changed], coverage)["confirmed_errors"], [])

    def test_undetermined_refuted_and_later_reopened_candidates_stay_internal(self):
        for verdict in ("undetermined", "refuted", "unknown"):
            with self.subTest(verdict=verdict):
                candidate, confirmed, coverage = report_records()
                current = deepcopy(confirmed)
                current["verdict"] = verdict
                reviews = [confirmed, current]
                before = deepcopy(reviews)
                report = build_report([candidate], reviews, coverage)
                self.assertEqual(report["confirmed_errors"], [])
                self.assertEqual(reviews, before)
        candidate, review, coverage = report_records()
        self.assertEqual(build_report([candidate], [], coverage)["confirmed_errors"], [])
        self.assertEqual(build_report([candidate, deepcopy(candidate)], [review], coverage)["confirmed_errors"], [])

    def test_zero_investigation_never_becomes_all_passed_or_full_review(self):
        cases = [
            (0, 0, True, "not_completed"),
            (0, 4, False, "not_completed"),
            (1, 4, True, "partial_review"),
            (4, 4, False, "partial_review"),
            (4, 4, True, "completed_review"),
            (True, 1, True, "not_completed"),
        ]
        for investigated, total, full_board, expected in cases:
            with self.subTest(investigated=investigated, total=total, full_board=full_board):
                coverage = {"investigated_count": investigated, "total_count": total,
                            "full_board": full_board, "scope": "synthetic review scope"}
                report = build_report([], [], coverage)
                self.assertEqual(report["run_status"], expected)
                self.assertEqual(report["confirmed_errors"], [])
                self.assertNotIn("all_passed", report.values())


if __name__ == "__main__":
    unittest.main()

import json
import unittest

from schemas import SchemaError, provider_schema, validate_action_json
from examples import get_example
from replay import expected_finding
from review import FormatError, parse_action, validate_findings


def envelope(action):
    wire = {"action": "get_diff"} if action == {"action": "get_diff", "arguments": {}} else action
    return json.dumps({"step": wire}, ensure_ascii=False)


class SchemaTests(unittest.TestCase):
    def test_all_actions_and_final_report(self):
        actions = [{"action": "get_diff", "arguments": {}},
                   {"action": "read_file", "arguments": {"path": "checkout.py", "version": "after"}},
                   {"action": "search_code", "arguments": {"query": "discount"}},
                   {"action": "find_definition", "arguments": {"symbol": "discount_rate"}},
                   {"action": "final", "summary": "完成审查", "findings": [expected_finding("checkout")]}]
        for action in actions:
            self.assertEqual(validate_action_json(envelope(action), envelope=True), action)

    def test_rejects_wrong_argument_branch_missing_and_extra_fields(self):
        bad = [{"action": "read_file", "arguments": {"query": "wrong"}},
               {"action": "get_diff", "arguments": {"path": "extra"}},
               {"action": "search_code", "arguments": {}},
               {"action": "search_code", "arguments": {"query": "   "}},
               {"action": "read_file", "arguments": {"path": "x", "version": "latest"}},
               {"action": "final", "summary": "done", "findings": [], "extra": "explanation"}]
        for action in bad:
            with self.subTest(action=action), self.assertRaises(SchemaError):
                validate_action_json(envelope(action), envelope=True)

    def test_rejects_prose_multiple_json_and_multiple_actions(self):
        action = {"action": "get_diff", "arguments": {}}
        valid = envelope(action)
        for raw in (valid + "\nExplanation", valid + "\n" + valid, "```json\n" + valid + "\n```",
                    json.dumps({"step": [action, action]}), json.dumps({"step": action, "explanation": "extra"})):
            with self.assertRaises(SchemaError):
                validate_action_json(raw, envelope=True)

    def test_final_only_schema_does_not_allow_tools(self):
        with self.assertRaises(SchemaError):
            validate_action_json(envelope({"action": "get_diff", "arguments": {}}), envelope=True, allow_tools=False)
        final = {"action": "final", "summary": "没有确认问题", "findings": []}
        self.assertEqual(validate_action_json(envelope(final), envelope=True, allow_tools=False), final)
        self.assertNotIn("anyOf", provider_schema(False)["properties"]["step"])

    def test_line_numbers_strict_and_positive_and_enum_limited(self):
        for field, value in (("line", "6"), ("line", 6.0), ("line", True), ("line", 0), ("line", -1), ("severity", "critical")):
            finding = dict(expected_finding("checkout"), **{field: value})
            raw = envelope({"action": "final", "summary": "done", "findings": [finding]})
            with self.subTest(field=field, value=value), self.assertRaises(SchemaError):
                validate_action_json(raw, envelope=True)

    def test_schema_keeps_title_property_and_has_no_external_refs(self):
        schema = provider_schema()
        text = json.dumps(schema)
        self.assertNotIn('"$ref"', text)
        self.assertNotIn('"discriminator"', text)
        self.assertNotIn('"oneOf"', text)
        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertGreater(len(value["properties"]), 0)
                    self.assertFalse(value["additionalProperties"])
                    self.assertEqual(set(value["required"]), set(value["properties"]))
                    if "severity" in value["properties"]:
                        self.assertIn("title", value["properties"])
                        self.assertIn("title", value["required"])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(schema)

    def test_structure_success_does_not_bypass_evidence_checks(self):
        finding = dict(expected_finding("checkout"), evidence="    return invented_code()")
        parsed = parse_action(envelope({"action": "final", "summary": "done", "findings": [finding]}), envelope=True)
        with self.assertRaises(FormatError):
            validate_findings(get_example("checkout"), parsed["findings"])

    def test_errors_do_not_echo_invalid_values(self):
        secret = "private-invalid-value"
        with self.assertRaises(SchemaError) as caught:
            validate_action_json(envelope({"action": "unknown", "arguments": {"value": secret}}), envelope=True)
        self.assertNotIn(secret, str(caught.exception))

    def test_legacy_flat_action_still_supported_explicitly(self):
        action = {"action": "read_file", "arguments": {"path": "checkout.py"}}
        self.assertEqual(parse_action(json.dumps(action)), action)
        with self.assertRaises(FormatError):
            parse_action(json.dumps(action), envelope=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

import unittest

from execution.core.validation import SchemaError, validate_args

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
        "level": {"type": "string", "enum": ["info", "warn"]},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name"],
    "additionalProperties": False,
}


class Validation(unittest.TestCase):
    def test_ok(self):
        out = validate_args(SCHEMA, {"name": "a", "count": 3, "level": "info", "tags": ["x"]})
        self.assertEqual(out["count"], 3)

    def test_missing_required(self):
        with self.assertRaises(SchemaError):
            validate_args(SCHEMA, {"count": 1})

    def test_unknown_key(self):
        with self.assertRaises(SchemaError):
            validate_args(SCHEMA, {"name": "a", "extra": 1})

    def test_wrong_type(self):
        with self.assertRaises(SchemaError):
            validate_args(SCHEMA, {"name": 5})

    def test_bool_not_integer(self):
        with self.assertRaises(SchemaError):
            validate_args(SCHEMA, {"name": "a", "count": True})

    def test_enum(self):
        with self.assertRaises(SchemaError):
            validate_args(SCHEMA, {"name": "a", "level": "nope"})

    def test_array_items(self):
        with self.assertRaises(SchemaError):
            validate_args(SCHEMA, {"name": "a", "tags": ["ok", 3]})


if __name__ == "__main__":
    unittest.main()

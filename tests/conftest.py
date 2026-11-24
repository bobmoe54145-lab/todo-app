import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from sheets import TodoStore  # noqa: E402


class FakeWorksheet:
    """gspread の Worksheet のうち、アプリが使う部分だけをまねた偽物（メモリ上の表）。"""

    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]

    def row_values(self, n):
        return list(self.rows[n - 1]) if n <= len(self.rows) else []

    def col_values(self, n):
        return [r[n - 1] if len(r) >= n else "" for r in self.rows]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, values, value_input_option=None, table_range=None):
        assert value_input_option == "RAW"
        self.rows.append([str(v) for v in values])

    def insert_cols(self, values, col=1, value_input_option=None):
        assert value_input_option == "RAW"
        for c_off, column in enumerate(values):
            for r, v in enumerate(column):
                while len(self.rows) <= r:
                    self.rows.append([])
                row = self.rows[r]
                while len(row) < col - 1:
                    row.append("")
                row.insert(col - 1 + c_off, str(v))

    def update(self, values, range_name, value_input_option=None):
        assert value_input_option == "RAW"
        m = re.fullmatch(r"([A-Z])(\d+)(?::([A-Z])(\d+))?", range_name)
        col = ord(m.group(1)) - ord("A")
        row = int(m.group(2)) - 1
        for r_off, line in enumerate(values):
            while len(self.rows) <= row + r_off:
                self.rows.append([])
            target = self.rows[row + r_off]
            for c_off, v in enumerate(line):
                while len(target) <= col + c_off:
                    target.append("")
                target[col + c_off] = str(v)


@pytest.fixture
def sheet():
    return FakeWorksheet()


@pytest.fixture
def client(sheet):
    app = create_app(store_factory=lambda: TodoStore(sheet))
    app.config["TESTING"] = True
    return app.test_client()

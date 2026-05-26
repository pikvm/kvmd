#!/usr/bin/env python3
import sys
from collections import defaultdict

import vulture.core
import vulture.noqa
from vulture.noqa import NOQA_REGEXP, _parse_error_codes

NOQA_CODE_MAP = {
    "vulture-unused": ["V101", "V102", "V103", "V104", "V105", "V106", "V107"]
}

def parse_noqa_custom(code):
    noqa_lines = defaultdict(set)
    for lineno, line in enumerate(code, start=1):
        match = NOQA_REGEXP.search(line)
        if match:
            for error_code in _parse_error_codes(match):
                error_code = NOQA_CODE_MAP.get(error_code, error_code)
                if not isinstance(error_code, list):
                    error_code = [error_code]
                for code in error_code:
                    noqa_lines[code].add(lineno)
    return noqa_lines

if __name__ == "__main__":
    vulture.noqa.NOQA_CODE_MAP.update(NOQA_CODE_MAP)
    vulture.noqa.parse_noqa_custom = parse_noqa_custom
    sys.argv[0] = "vulture"
    exit(vulture.core.main())

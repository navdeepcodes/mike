#!/usr/bin/env python3
"""Setup fixture for Task 3 - creates the messy 300-line script."""
from pathlib import Path


def setup_task3(base_dir: Path):
    """Create the messy script to be refactored."""
    base_dir.mkdir(parents=True, exist_ok=True)

    # A messy 300-line script that does parsing, processing, and formatting all in one
    messy_script = '''#!/usr/bin/env python3
"""Messy script that does everything in one file - needs refactoring."""
import json
import csv
import re
import sys
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

# ============================================================
# PARSING LOGIC (mixed with everything else)
# ============================================================

def parse_json_input(data: str) -> List[Dict]:
    """Parse JSON input."""
    try:
        parsed = json.loads(data)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]
        else:
            return []
    except json.JSONDecodeError:
        return []

def parse_csv_input(data: str) -> List[Dict]:
    """Parse CSV input."""
    lines = data.strip().split('\\n')
    if not lines:
        return []
    reader = csv.DictReader(lines)
    return list(reader)

def parse_log_line(line: str) -> Optional[Dict]:
    """Parse a single log line."""
    # Format: timestamp level message
    pattern = r'(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}) (\\w+) (.*)'
    match = re.match(pattern, line)
    if match:
        return {
            'timestamp': match.group(1),
            'level': match.group(2),
            'message': match.group(3)
        }
    return None

def parse_input(data: str, format_type: str) -> List[Dict]:
    """Main parsing entry point."""
    if format_type == 'json':
        return parse_json_input(data)
    elif format_type == 'csv':
        return parse_csv_input(data)
    elif format_type == 'log':
        return [parse_log_line(line) for line in data.strip().split('\\n') if parse_log_line(line)]
    else:
        return []

# ============================================================
# PROCESSING LOGIC (mixed with everything else)
# ============================================================

def filter_by_date(records: List[Dict], start_date: str = None, end_date: str = None) -> List[Dict]:
    """Filter records by date range."""
    if not start_date and not end_date:
        return records

    filtered = []
    for record in records:
        if 'timestamp' in record:
            try:
                ts = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
                if start_date:
                    start = datetime.fromisoformat(start_date)
                    if ts < start:
                        continue
                if end_date:
                    end = datetime.fromisoformat(end_date)
                    if ts > end:
                        continue
                filtered.append(record)
            except:
                pass
        else:
            filtered.append(record)
    return filtered

def filter_by_level(records: List[Dict], levels: List[str]) -> List[Dict]:
    """Filter records by log level."""
    if not levels:
        return records
    return [r for r in records if r.get('level', '').upper() in [l.upper() for l in levels]]

def aggregate_by_field(records: List[Dict], field: str) -> Dict[str, int]:
    """Aggregate counts by field value."""
    counts = {}
    for record in records:
        value = record.get(field, 'unknown')
        counts[value] = counts.get(value, 0) + 1
    return counts

def compute_statistics(records: List[Dict]) -> Dict[str, Any]:
    """Compute statistics on records."""
    if not records:
        return {'count': 0}

    stats = {
        'count': len(records),
        'levels': aggregate_by_field(records, 'level'),
        'fields': list(records[0].keys()) if records else []
    }

    # Add numeric field stats if present
    for key in records[0].keys():
        values = [r.get(key) for r in records if isinstance(r.get(key), (int, float))]
        if values:
            stats[f'{key}_sum'] = sum(values)
            stats[f'{key}_avg'] = sum(values) / len(values)
            stats[f'{key}_min'] = min(values)
            stats[f'{key}_max'] = max(values)

    return stats

def transform_records(records: List[Dict], operations: List[str]) -> List[Dict]:
    """Apply transformations to records."""
    result = []
    for record in records:
        new_record = record.copy()
        for op in operations:
            if op == 'uppercase':
                for k, v in new_record.items():
                    if isinstance(v, str):
                        new_record[k] = v.upper()
            elif op == 'trim':
                for k, v in new_record.items():
                    if isinstance(v, str):
                        new_record[k] = v.strip()
            elif op == 'add_timestamp':
                new_record['processed_at'] = datetime.now().isoformat()
        result.append(new_record)
    return result

def process_records(records: List[Dict], options: Dict) -> Dict[str, Any]:
    """Main processing entry point."""
    # Filter
    filtered = filter_by_date(records, options.get('start_date'), options.get('end_date'))
    filtered = filter_by_level(filtered, options.get('levels', []))

    # Transform
    if options.get('transform'):
        filtered = transform_records(filtered, options['transform'])

    # Statistics
    stats = compute_statistics(filtered)

    return {
        'records': filtered,
        'statistics': stats
    }

# ============================================================
# FORMATTING LOGIC (mixed with everything else)
# ============================================================

def format_as_json(data: Dict) -> str:
    """Format as JSON."""
    return json.dumps(data, indent=2, default=str)

def format_as_csv(data: Dict) -> str:
    """Format as CSV."""
    records = data.get('records', [])
    if not records:
        return ''

    output = []
    fieldnames = records[0].keys()
    output.append(','.join(fieldnames))
    for record in records:
        output.append(','.join(str(record.get(f, '')) for f in fieldnames))
    return '\\n'.join(output)

def format_as_table(data: Dict) -> str:
    """Format as ASCII table."""
    records = data.get('records', [])
    if not records:
        return 'No records'

    fieldnames = list(records[0].keys())
    col_widths = {f: len(f) for f in fieldnames}

    # Calculate column widths
    for record in records:
        for f in fieldnames:
            col_widths[f] = max(col_widths[f], len(str(record.get(f, ''))))

    # Build table
    lines = []
    # Header
    header = ' | '.join(f.ljust(col_widths[f]) for f in fieldnames)
    lines.append(header)
    lines.append('-' * len(header))
    # Data rows
    for record in records:
        row = ' | '.join(str(record.get(f, '')).ljust(col_widths[f]) for f in fieldnames)
        lines.append(row)

    # Add statistics
    stats = data.get('statistics', {})
    lines.append('')
    lines.append('Statistics:')
    for k, v in stats.items():
        if isinstance(v, dict):
            lines.append(f'  {k}:')
            for k2, v2 in v.items():
                lines.append(f'    {k2}: {v2}')
        else:
            lines.append(f'  {k}: {v}')

    return '\\n'.join(lines)

def format_output(data: Dict, format_type: str) -> str:
    """Main formatting entry point."""
    if format_type == 'json':
        return format_as_json(data)
    elif format_type == 'csv':
        return format_as_csv(data)
    elif format_type == 'table':
        return format_as_table(data)
    else:
        return format_as_table(data)

# ============================================================
# MAIN ENTRY POINT (mixed with everything else)
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='Process data files')
    parser.add_argument('input', help='Input file or stdin')
    parser.add_argument('--format', choices=['json', 'csv', 'log'], default='json')
    parser.add_argument('--output', choices=['json', 'csv', 'table'], default='table')
    parser.add_argument('--start-date', help='Filter start date (ISO format)')
    parser.add_argument('--end-date', help='Filter end date (ISO format)')
    parser.add_argument('--levels', help='Comma-separated log levels')
    parser.add_argument('--transform', nargs='*', choices=['uppercase', 'trim', 'add_timestamp'])

    args = parser.parse_args()

    # Read input
    if args.input == '-':
        data = sys.stdin.read()
    else:
        with open(args.input, 'r') as f:
            data = f.read()

    # Parse
    records = parse_input(data, args.format)

    # Process
    options = {
        'start_date': args.start_date,
        'end_date': args.end_date,
        'levels': args.levels.split(',') if args.levels else [],
        'transform': args.transform or []
    }
    result = process_records(records, options)

    # Format and output
    output = format_output(result, args.output)
    print(output)


if __name__ == '__main__':
    main()
'''

    # Test file for the messy script
    test_script = '''#!/usr/bin/env python3
"""Tests for the messy script."""
import sys
import json
import tempfile
import os
sys.path.insert(0, os.path.dirname(__file__))

import messy_script as ms

def test_parse_json():
    data = '[{"a": 1}, {"a": 2}]'
    result = ms.parse_input(data, 'json')
    assert len(result) == 2
    assert result[0]['a'] == 1

def test_parse_csv():
    data = "a,b\\n1,2\\n3,4"
    result = ms.parse_input(data, 'csv')
    assert len(result) == 2
    assert result[0]['a'] == '1'

def test_parse_log():
    data = "2024-01-15 10:30:00 INFO Test message"
    result = ms.parse_input(data, 'log')
    assert len(result) == 1
    assert result[0]['level'] == 'INFO'

def test_filter_by_date():
    records = [
        {'timestamp': '2024-01-10T10:00:00', 'value': 1},
        {'timestamp': '2024-01-20T10:00:00', 'value': 2},
        {'timestamp': '2024-02-01T10:00:00', 'value': 3},
    ]
    filtered = ms.filter_by_date(records, '2024-01-15', '2024-01-25')
    assert len(filtered) == 1
    assert filtered[0]['value'] == 2

def test_filter_by_level():
    records = [
        {'level': 'INFO', 'msg': 'a'},
        {'level': 'ERROR', 'msg': 'b'},
        {'level': 'DEBUG', 'msg': 'c'},
    ]
    filtered = ms.filter_by_level(records, ['ERROR'])
    assert len(filtered) == 1
    assert filtered[0]['msg'] == 'b'

def test_aggregate_by_field():
    records = [
        {'level': 'INFO'}, {'level': 'INFO'}, {'level': 'ERROR'}
    ]
    counts = ms.aggregate_by_field(records, 'level')
    assert counts['INFO'] == 2
    assert counts['ERROR'] == 1

def test_compute_statistics():
    records = [{'value': 10}, {'value': 20}, {'value': 30}]
    stats = ms.compute_statistics(records)
    assert stats['count'] == 3
    assert stats['value_sum'] == 60
    assert stats['value_avg'] == 20

def test_transform_uppercase():
    records = [{'name': 'alice'}, {'name': 'bob'}]
    result = ms.transform_records(records, ['uppercase'])
    assert result[0]['name'] == 'ALICE'

def test_process_records():
    records = [
        {'timestamp': '2024-01-15T10:00:00', 'level': 'INFO', 'value': 5},
        {'timestamp': '2024-01-20T10:00:00', 'level': 'ERROR', 'value': 10},
    ]
    options = {'start_date': '2024-01-10', 'end_date': '2024-01-25', 'levels': ['INFO']}
    result = ms.process_records(records, options)
    assert len(result['records']) == 1
    assert result['records'][0]['level'] == 'INFO'

def test_format_json():
    data = {'records': [{'a': 1}], 'statistics': {'count': 1}}
    output = ms.format_output(data, 'json')
    parsed = json.loads(output)
    assert parsed['records'][0]['a'] == 1

def test_format_table():
    data = {'records': [{'a': 1, 'b': 2}], 'statistics': {'count': 1}}
    output = ms.format_output(data, 'table')
    assert 'a' in output and 'b' in output

def run_all_tests():
    test_parse_json()
    test_parse_csv()
    test_parse_log()
    test_filter_by_date()
    test_filter_by_level()
    test_aggregate_by_field()
    test_compute_statistics()
    test_transform_uppercase()
    test_process_records()
    test_format_json()
    test_format_table()
    print("All tests passed!")

if __name__ == '__main__':
    run_all_tests()
'''

    (base_dir / "messy_script.py").write_text(messy_script)
    (base_dir / "test_messy.py").write_text(test_script)

    return base_dir


if __name__ == "__main__":
    setup_task3(Path("/tmp/test_task3"))
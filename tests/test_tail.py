from devradar.collector.tail import read_new_lines


def test_partial_line_not_consumed(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":')
    lines, off = read_new_lines(p, 0)
    assert lines == ['{"a":1}']
    assert off == 8
    with open(p, "ab") as f:
        f.write(b"2}\n")
    lines, off2 = read_new_lines(p, off)
    assert lines == ['{"b":2}']
    assert off2 == p.stat().st_size


def test_truncated_file_resets(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_bytes(b'{"x":"0123456789"}\n')
    lines, off = read_new_lines(p, 500)
    assert lines == ['{"x":"0123456789"}']
    assert off == p.stat().st_size


def test_utf8_preserved(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_bytes('{"t":"Đề Cũ"}\n'.encode("utf-8"))
    lines, _ = read_new_lines(p, 0)
    assert lines == ['{"t":"Đề Cũ"}']

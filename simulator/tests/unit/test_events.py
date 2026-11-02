"""Events and the event log.  uv run pytest"""

from simulator.events import Event, EventLog, Severity, Source


def sample_log() -> EventLog:
    log = EventLog()
    log.add(Event.bottle(1.0, 192, "entered filler"))
    log.add(Event.bottle(2.0, 193, "entered filler"))
    log.add(Event.plc(2.5, "step 10 -> 20"))
    log.add(Event.bottle(3.0, 192, "left uncapped", warning=True))
    log.add(Event.sim(4.0, "belt jam injected"))
    return log


def test_constructors_set_source_and_severity():
    event = Event.bottle(1.0, 7, "filled 500 ml")
    assert (event.source, event.bottle_id, event.severity) == (Source.BOTTLE, 7, Severity.INFO)
    assert Event.plc(0, "x").source == Source.PLC
    assert Event.sim(0, "x", warning=True).is_warning


def test_format_one_line():
    assert Event.bottle(12.3, 192, "entered filler").format() == "   12.3 s   Bottle 192   entered filler"
    assert Event.plc(2.5, "step 10 -> 20").format() == "    2.5 s   PLC          step 10 -> 20"
    assert Event.bottle(3, 5, "spill", warning=True).format().startswith("    3.0 s !")


def test_recent_returns_last_events_oldest_first():
    log = sample_log()
    assert [e.time_s for e in log.recent(2)] == [3.0, 4.0]
    assert log.recent(0) == []


def test_filter_by_bottle_source_and_warnings():
    log = sample_log()
    assert [e.text for e in log.filter(bottle_id=192)] == ["entered filler", "left uncapped"]
    assert [e.text for e in log.filter(source=Source.PLC)] == ["step 10 -> 20"]
    assert [e.text for e in log.filter(warnings_only=True)] == ["left uncapped"]
    assert len(log.filter()) == 5


def test_memory_limit_keeps_newest_but_counts_all_warnings():
    log = EventLog(max_events=3)
    for i in range(10):
        log.add(Event.bottle(i, i, "spill", warning=True))
    assert len(log) == 3
    assert [e.bottle_id for e in log.recent(3)] == [7, 8, 9]
    assert log.warning_count == 10  # dropped events still count


def test_file_gets_one_line_per_event(tmp_path):
    path = tmp_path / "events.log"
    log = EventLog(file_path=path)
    log.extend([Event.bottle(1, 1, "entered filler"), Event.plc(2, "step 0 -> 10")])
    log.close()
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert "Bottle 1" in lines[0] and "step 0 -> 10" in lines[1]

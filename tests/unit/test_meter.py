import pytest

from trafficcop.daemon import meter, nft
from trafficcop.daemon.meter import Usage

ZOOM = 0x01000000
SPOTIFY = 0x02000000


class FakeNft:
    """Fake counters and clock: set ``bytes``/``now``, then sample."""

    def __init__(self):
        self.bytes: dict[str, int] = {}
        self.now = 100.0
        self.error: Exception | None = None

    def read(self) -> dict[str, tuple[int, int]]:
        if self.error:
            raise self.error
        return {name: (0, b) for name, b in self.bytes.items()}

    def clock(self) -> float:
        return self.now


@pytest.fixture
def fake() -> FakeNft:
    return FakeNft()


def make_meter(fake: FakeNft, marks: dict[str, int] | None = None) -> meter.Meter:
    return meter.Meter(marks or {"Zoom": ZOOM}, read=fake.read, clock=fake.clock)


def test_rates_and_totals(fake: FakeNft):
    m = make_meter(fake, {"Zoom": ZOOM, "Spotify": SPOTIFY})
    fake.now += 2
    fake.bytes = {"up_1": 2000, "down_1": 10_000, "down_2": 500}
    assert m.sample() == {
        "Zoom": Usage(1000.0, 5000.0, 2000, 10_000),
        "Spotify": Usage(0.0, 250.0, 0, 500),
    }
    fake.now += 1
    fake.bytes = {"up_1": 2500, "down_1": 10_000, "down_2": 500}
    assert m.sample() == {
        "Zoom": Usage(500.0, 0.0, 2500, 10_000),
        "Spotify": Usage(0.0, 0.0, 0, 500),
    }


def test_missing_counters_count_as_zero(fake: FakeNft):
    m = make_meter(fake)
    fake.now += 1
    assert m.sample() == {"Zoom": Usage()}


def test_no_time_elapsed(fake: FakeNft):
    m = make_meter(fake)
    fake.bytes = {"up_1": 100}
    assert m.sample() == {"Zoom": Usage(0.0, 0.0, 100, 0)}


def test_restarted_keeps_totals_for_remaining_apps(fake: FakeNft):
    m = make_meter(fake, {"Zoom": ZOOM, "Spotify": SPOTIFY})
    fake.now += 1
    fake.bytes = {"up_1": 1000, "up_2": 300}
    m.sample()

    # Config reload: Spotify removed, Zoom moves to slot 2, table rebuilt.
    fake.now += 1
    fake.bytes = {}
    m.restarted({"Zoom": SPOTIFY})
    fake.now += 1
    fake.bytes = {"up_2": 400}
    assert m.sample() == {"Zoom": Usage(400.0, 0.0, 1400, 0)}


def test_counter_reset_without_restarted(fake: FakeNft):
    m = make_meter(fake)
    fake.now += 1
    fake.bytes = {"down_1": 5000}
    m.sample()
    fake.now += 1
    fake.bytes = {"down_1": 300}  # table was rebuilt behind the meter's back
    assert m.sample() == {"Zoom": Usage(0.0, 300.0, 0, 5300)}


def test_read_errors_propagate(fake: FakeNft):
    m = make_meter(fake)
    fake.error = nft.NftError("Error: No such file or directory")
    with pytest.raises(nft.NftError):
        m.sample()

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


def make_meter(
    fake: FakeNft, marks: dict[str, int] | None = None, smoothing: float = 0
) -> meter.Meter:
    """A meter on the fakes; unsmoothed unless asked, so rates are exact."""
    return meter.Meter(
        marks or {"Zoom": ZOOM}, read=fake.read, clock=fake.clock, smoothing=smoothing
    )


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


def test_prefers_cake_upload_counter(fake: FakeNft):
    m = make_meter(fake)
    fake.now += 1
    fake.bytes = {"up_1": 5000, "tc_up_1": 4000, "down_1": 100}
    assert m.sample() == {"Zoom": Usage(4000.0, 100.0, 4000, 100)}


def test_prefers_cake_download_counter(fake: FakeNft):
    m = make_meter(fake)
    fake.now += 1
    fake.bytes = {"down_1": 2000, "tc_down_1": 3500}  # 1500 dropped by CAKE
    assert m.sample()["Zoom"] == Usage(0.0, 3500.0, 0, 3500)


def test_switching_upload_sources_counts_once(fake: FakeNft):
    m = make_meter(fake)
    fake.now += 1
    fake.bytes = {"up_1": 5000}  # not shaped yet
    m.sample()
    fake.now += 1
    fake.bytes = {"up_1": 6000, "tc_up_1": 800}  # shaping started
    m.forget(["tc_up_1"])
    assert m.sample()["Zoom"].up_total == 5800
    fake.now += 1
    fake.bytes = {"up_1": 9000, "tc_up_1": 1300}
    assert m.sample()["Zoom"].up_total == 6300
    fake.now += 1
    fake.bytes = {"up_1": 9500}  # WAN lost: back to nft, from its baseline
    assert m.sample()["Zoom"].up_total == 6800


def test_forget_after_tc_rebuild(fake: FakeNft):
    m = make_meter(fake)
    fake.now += 1
    fake.bytes = {"tc_up_1": 5000}
    m.sample()
    m.forget(["tc_up_1"])  # tc rebuilt; the new leaf already sent 6000
    fake.now += 1
    fake.bytes = {"tc_up_1": 6000}
    assert m.sample()["Zoom"].up_total == 11000


def test_read_errors_propagate(fake: FakeNft):
    m = make_meter(fake)
    fake.error = nft.NftError("Error: No such file or directory")
    with pytest.raises(nft.NftError):
        m.sample()


def test_rates_are_smoothed(fake: FakeNft):
    m = make_meter(fake, smoothing=3.0)
    total = 0
    rates = []
    for burst in (30_000, 0, 30_000, 0, 30_000, 0):  # bursty: 15 kB/s on average
        fake.now += 1
        total += burst
        fake.bytes = {"down_1": total}
        rates.append(m.sample()["Zoom"].down_rate)
    assert rates[0] == 30_000  # nothing to smooth against at first
    assert max(rates[1:]) < 30_000 and min(rates[1:]) > 0  # no swings to 0 or full
    assert m.sample()["Zoom"].down_total == 90_000  # totals stay exact


def test_smoothing_follows_a_change(fake: FakeNft):
    m = make_meter(fake, smoothing=3.0)
    total = 0
    for _ in range(15):  # 15 s at 10 kB/s, then it stops
        fake.now += 1
        total += 10_000
        fake.bytes = {"up_1": total}
        m.sample()
    rate = 0.0
    for _ in range(10):  # 10 s idle
        fake.now += 1
        rate = m.sample()["Zoom"].up_rate
    assert rate < 10_000 * 0.05  # after ~3 time constants, close to 0


def test_restarted_drops_rates_of_removed_apps(fake: FakeNft):
    m = make_meter(fake, {"Zoom": ZOOM, "Spotify": SPOTIFY}, smoothing=3.0)
    fake.now += 1
    fake.bytes = {"up_2": 500}
    m.sample()
    m.restarted({"Zoom": ZOOM})
    fake.now += 1
    assert set(m.sample()) == {"Zoom"}

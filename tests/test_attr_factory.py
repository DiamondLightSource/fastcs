import pytest

from fastcs.attributes import AttrFactory, AttrR, AttrRW, AttrW, NotPolled, Polled
from fastcs.util import ONCE
from tests.conftest import FakeBackend


@pytest.mark.asyncio
async def test_attr_r_builds_a_readable_attribute_bound_to_the_backend():
    backend = FakeBackend(value=2.5)

    attr = AttrFactory(backend).attr_r(float, "R")

    assert isinstance(attr, AttrR)
    assert await attr.poll() == 2.5
    assert backend.gets == [("R",)]


@pytest.mark.parametrize(
    "schedule, expected_poll_period",
    [(None, ONCE), (Polled(period=0.5), 0.5), (NotPolled(), None)],
    ids=["no-schedule", "polled", "not-polled"],
)
def test_attr_r_applies_the_given_schedule(
    schedule: Polled[float] | NotPolled[float] | None, expected_poll_period
):
    attr = AttrFactory(FakeBackend()).attr_r(float, "R", schedule=schedule)

    assert attr.poll_period == expected_poll_period


@pytest.mark.parametrize(
    "schedule, expected_poll_period",
    [(None, ONCE), (Polled(period=0.5), 0.5), (NotPolled(), None)],
    ids=["no-schedule", "polled", "not-polled"],
)
def test_attr_rw_applies_the_given_schedule(
    schedule: Polled[float] | NotPolled[float] | None, expected_poll_period
):
    attr = AttrFactory(FakeBackend()).attr_rw(float, "R", schedule=schedule)

    assert attr.poll_period == expected_poll_period


@pytest.mark.asyncio
async def test_attr_w_builds_a_writable_attribute_bound_to_the_backend():
    backend = FakeBackend()

    attr = AttrFactory(backend).attr_w(float, "R")

    assert isinstance(attr, AttrW)
    await attr.set(3.5)
    assert backend.sets == [(3.5, ("R",))]


@pytest.mark.asyncio
async def test_attr_rw_binds_both_getter_and_setter():
    backend = FakeBackend(value=4.5)

    attr = AttrFactory(backend).attr_rw(float, "R")

    assert isinstance(attr, AttrRW)
    assert await attr.poll() == 4.5
    await attr.set(5.5)
    assert backend.sets == [(5.5, ("R",))]


@pytest.mark.asyncio
async def test_fill_binds_a_getter_onto_a_read_only_attribute():
    backend = FakeBackend(value=6.5)
    attr = AttrR(float)

    AttrFactory(backend).fill(attr, "R")

    assert await attr.poll() == 6.5
    assert backend.gets == [("R",)]


@pytest.mark.asyncio
async def test_fill_binds_a_setter_onto_a_write_only_attribute():
    backend = FakeBackend()
    attr = AttrW(float)

    AttrFactory(backend).fill(attr, "R")

    await attr.set(7.5)
    assert backend.sets == [(7.5, ("R",))]


@pytest.mark.asyncio
async def test_fill_setter_return_value_updates_the_readback():
    """A setter returning a value is the device's accepted/clamped value."""
    backend = FakeBackend(set_result=6.0)
    attr = AttrRW(float)

    AttrFactory(backend).fill(attr, "R")
    await attr.set(5.0)

    assert attr.readback == 6.0


@pytest.mark.asyncio
async def test_fill_binds_both_halves_onto_a_read_write_attribute():
    backend = FakeBackend(value=8.5)
    attr = AttrRW(float)

    AttrFactory(backend).fill(attr, "R")

    assert await attr.poll() == 8.5
    await attr.set(9.5)
    assert backend.sets == [(9.5, ("R",))]


@pytest.mark.parametrize(
    "schedule, expected_poll_period",
    [(None, ONCE), (Polled(period=0.2), 0.2), (NotPolled(), None)],
    ids=["no-schedule", "polled", "not-polled"],
)
def test_fill_applies_the_given_schedule(
    schedule: Polled[float] | NotPolled[float] | None, expected_poll_period
):
    attr = AttrR(float)

    AttrFactory(FakeBackend()).fill(attr, "R", schedule=schedule)

    assert attr.poll_period == expected_poll_period


def test_fill_with_a_schedule_that_already_has_a_getter_raises():
    attr = AttrR(float)

    async def get() -> float:
        return 0.0

    with pytest.raises(TypeError, match="already has a getter"):
        AttrFactory(FakeBackend()).fill(attr, "R", schedule=Polled(get, period=0.2))


@pytest.mark.asyncio
async def test_fill_forwards_every_positional_argument_to_the_backend():
    backend = FakeBackend()
    attr = AttrR(float)

    AttrFactory(backend).fill(attr, "R", "01")
    await attr.poll()

    assert backend.gets == [("R", "01")]

"""A sample set of multiplexes, for trying a television scan without a tuner.

Three multiplexes on real UHF channels, carrying television and radio, a scrambled service, and
logical channel numbers that are not in the order the services appear. The frequencies are
genuine UHF channels so that the output reads like a real scan; the content is invented.
"""

from __future__ import annotations

from typing import Final

from openwave.sdr.mock import MockDvbDevice, SyntheticMux
from openwave.tv.band import UHF_PLAN
from openwave.tv.synthesis import SyntheticMultiplex, SyntheticService, build_transport_stream
from openwave.tv.tables import ServiceType

#: UHF channels the demonstration multiplexes sit on.
DEMO_CHANNELS: Final = (24, 27, 31)


def _first_multiplex() -> SyntheticMultiplex:
    """The main multiplex: a spread of television, radio and one scrambled service."""
    return SyntheticMultiplex(
        transport_stream_id=0x1001,
        network_id=0x2000,
        network_name="OpenWave Network",
        centre_frequency_hz=UHF_PLAN.frequency_of(24),
        services=(
            SyntheticService(service_id=0x0001, name="OpenWave One", logical_channel=1),
            SyntheticService(service_id=0x0002, name="OpenWave Two", logical_channel=2),
            SyntheticService(
                service_id=0x0003,
                name="OpenWave News",
                service_type=int(ServiceType.DIGITAL_TELEVISION),
                logical_channel=7,
            ),
            SyntheticService(
                service_id=0x0004,
                name="OpenWave Radio",
                service_type=int(ServiceType.DIGITAL_RADIO),
                logical_channel=701,
            ),
        ),
    )


def _second_multiplex() -> SyntheticMultiplex:
    """A high-definition multiplex, including a service nobody can watch."""
    return SyntheticMultiplex(
        transport_stream_id=0x1002,
        network_id=0x2000,
        network_name="OpenWave Network",
        centre_frequency_hz=UHF_PLAN.frequency_of(27),
        services=(
            SyntheticService(
                service_id=0x0010,
                name="OpenWave HD",
                service_type=int(ServiceType.ADVANCED_CODEC_HD_TELEVISION),
                logical_channel=101,
            ),
            SyntheticService(
                service_id=0x0011,
                name="OpenWave Films",
                service_type=int(ServiceType.ADVANCED_CODEC_HD_TELEVISION),
                logical_channel=102,
            ),
            SyntheticService(
                service_id=0x0012,
                name="Premium Sport",
                provider="Somebody Else",
                logical_channel=401,
                scrambled=True,
            ),
        ),
    )


def _third_multiplex() -> SyntheticMultiplex:
    """A small multiplex from a different operator, with no channel numbers published.

    Plenty of real multiplexes do not carry the private descriptor that holds the numbers, and
    a scan has to list their services anyway.
    """
    return SyntheticMultiplex(
        transport_stream_id=0x1003,
        network_id=0x2100,
        network_name="Local Transmissions",
        centre_frequency_hz=UHF_PLAN.frequency_of(31),
        services=(
            SyntheticService(service_id=0x0020, name="Local TV", provider="Local"),
            SyntheticService(
                service_id=0x0021,
                name="Local Radio",
                provider="Local",
                service_type=int(ServiceType.DIGITAL_RADIO),
            ),
        ),
    )


#: The multiplexes the demonstration tuner carries.
DEMO_MULTIPLEXES: Final = (_first_multiplex(), _second_multiplex(), _third_multiplex())


def demo_tuner(*, repeats: int = 3) -> MockDvbDevice:
    """A simulated DVB-T tuner carrying :data:`DEMO_MULTIPLEXES`.

    Args:
        repeats: How many times each multiplex repeats its tables. More is more realistic and
            slower; three is enough for a scan to read them.
    """
    muxes = tuple(
        SyntheticMux(
            freq_hz=multiplex.centre_frequency_hz,
            transport_stream=build_transport_stream(multiplex, repeats=repeats),
            bandwidth_hz=multiplex.bandwidth_hz,
            snr_db=24.0 - index * 3.0,
            strength_dbm=-45.0 - index * 5.0,
            label=multiplex.network_name,
        )
        for index, multiplex in enumerate(DEMO_MULTIPLEXES)
    )
    return MockDvbDevice(muxes=muxes)

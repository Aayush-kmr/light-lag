"""DSN Now parser on a FORMAT fixture (synthetic values, real element layout)."""
from lightlag.data_sources import parse_dsn_xml

FIXTURE = """<dsn>
<station name="gdscc" friendlyName="Goldstone" timeUTC="0" timeZoneOffset="0"/>
<dish name="DSS14" azimuthAngle="120.5" elevationAngle="35.2" isMSPA="false" isArray="false" isDDOR="false">
  <downSignal active="true" signalType="data" dataRate="2000" frequency="8.4e9" band="X" power="-150" spacecraft="TEST" spacecraftID="-999"/>
  <upSignal active="true" signalType="data" dataRate="250" frequency="7.1e9" band="X" power="1" spacecraft="TEST" spacecraftID="-999"/>
  <target name="TEST" id="999" uplegRange="1e8" downlegRange="1e8" rtlt="667.1"/>
</dish>
<station name="mdscc" friendlyName="Madrid" timeUTC="0" timeZoneOffset="0"/>
<dish name="DSS63" azimuthAngle="0" elevationAngle="0"></dish>
<timestamp>1700000000000</timestamp>
</dsn>"""


def test_parse_dsn_now_layout():
    d = parse_dsn_xml(FIXTURE)
    assert [s["friendly"] for s in d["stations"]] == ["Goldstone", "Madrid"]
    dish = d["stations"][0]["dishes"][0]
    assert dish["name"] == "DSS14" and dish["targets"] == ["TEST"] and dish["rtlt_s"] == [667.1]
    assert d["stations"][1]["dishes"][0]["targets"] == []
    assert d["timestamp_ms"] == 1700000000000
